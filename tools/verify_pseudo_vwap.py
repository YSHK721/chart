"""verify_pseudo_vwap — 疑似VWAP（ティック回数加重平均価格）の成立判定（読み取り専用・ISSUE-243 Phase 1）。

疑似VWAP の定義（本スクリプトが検証する唯一の式）::

    疑似VWAP_t(N) = Σ_{i=t-N+1..t} PV_i / Σ_{i=t-N+1..t} V_i
        PV_i = Σ_{tick ∈ bar i} mid    （バー内のティック価格合計）
        V_i  = bar i のティック数（既存 volume 列と同一＝marketdata.tick_m1.ticks_to_m1）

依頼式 ``(価格帯別ティック回数 × 該当価格) / ティックボリューム`` は、価格帯幅 → 0 の極限で
上式に一致する。本スクリプトは (a) 上式が既存の SMA と別物か、(b) 価格帯を経由した近似が
どれだけ誤差を生むか、(c) 情報を持つか、(d) 形成中バーで厳密に更新できるか を実測する。

**コードは一切変更しない**（data/marketdata 配下を read-only で読むだけ）。

計測内容:
    測定 1 (gate): D = 疑似VWAP − SMA(close, N)。``median(|D|) / median(TR)`` が 10% 未満なら
                   「SMA の再発明」＝不採用。併せて相関と、滞在秒加重平均（時間加重）との差も出す。
    測定 2      : 依頼原式（価格帯経由）との差。GRID_W=10pt と unit=0.0255 の 2 解像度。
    測定 3      : s = sign(close − 疑似VWAP) の将来 h 本リターンに対する情報量。標本は
                  **h 本ごとの非重複**、帰無は**ブロック順列**（ブロック長 = 10N 本相当）、
                  多重比較は Holm 補正。
    測定 4      : 形成中バー（部分窓）で Σmid を厳密に持てるか（tick から直接検算）。

使い方::

    lightweight-charts-python-main/.venv/bin/python -m tools.verify_pseudo_vwap \\
        --periods 2024-01-01:2024-12-31 2026-01-01:2026-07-31 \\
        --json /tmp/.../pseudo_vwap.json

依存: numpy / pandas と marketdata（本番の集計関数をそのまま使う）。scipy はプロジェクト方針で禁止。
"""
from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from marketdata import outlier_policy
from marketdata.resample import (
    SESSION_TFS,
    TIMEFRAME_RULES,
    _to_broker_naive_index,
    resample_ohlc_tf,
)
# 生ティックの必須列・mid 規則・M1 集計は本番の単一規則源をそのまま使う（式を写さない）。
from marketdata.tick_m1 import (  # noqa: E402
    _TICK_COLUMNS,
    _ts_and_mid,
    day_parquet_files,
    ticks_to_m1,
)
# 価格帯（依頼原式）の解像度も既存実装の値を参照する（定数を写さない）。
from market_profile_api.compute.market_profile_dwell_kernel import GRID_W
from market_profile_api.compute.session_activity import ACTIVE_FRAC, build_active_table
from market_profile_api.compute.market_profile_dwell_kernel import _session_dwell
from market_profile_api.controller.tf_period_profile_controller import _UNIT_BY_TF
# 乖離率の閾値は既存の因果ローリング分位バンド（当該バー除外・非リペイント）を使う。
from common.marod_bands import quantile_bands
from marketdata.session_day import session_day_starts

UNIT_FINE = float(_UNIT_BY_TF["1m"])  # 1m の最小価格単位（実測 0.0255）。

# 合算集約する追加列（Phase 2 で csv_schema.SUM_COLUMNS へ入る予定の列と同じ性質）。
_SUM_EXTRA = ("pv", "pv_g10", "pv_u", "pw", "w")

DEFAULT_TFS = ("5m", "15m", "1h")
DEFAULT_WINDOWS = (20, 50, 100)
DEFAULT_HORIZONS = (1, 5, 20)
DEFAULT_PERMS = 999
GATE_RATIO = 0.10  # 測定 1 の事前登録閾値: median(|D|) / median(TR)


# --------------------------------------------------------------------------- 素材化


def _day_paths(lo: pd.Timestamp, hi: pd.Timestamp, symbol: str) -> "list[Path]":
    return day_parquet_files(lo, hi, symbol=symbol)


def _active_table(paths: "list[Path]") -> np.ndarray:
    """全期間のティック時刻から曜日×時の活発地図を作る（本番 build_active_table を使用）。"""
    chunks = []
    for p in paths:
        ts = pd.read_parquet(p, columns=["timestamp"])["timestamp"]
        ts = pd.to_datetime(ts)
        if getattr(ts.dt, "tz", None) is not None:
            ts = ts.dt.tz_convert("UTC").dt.tz_localize(None)
        chunks.append(ts.to_numpy().astype("datetime64[s]").astype("int64"))
    if not chunks:
        return np.zeros((7, 24), dtype=bool)
    return build_active_table(np.concatenate(chunks), active_frac=ACTIVE_FRAC)


def _m1_with_pv(path: Path, table: "np.ndarray | None") -> pd.DataFrame:
    """1 日分の M1（本番 ticks_to_m1）へ pv 系の列を足して返す（外れ分バー除去も本番同一）。

    追加列:
        pv     : Σ mid（厳密。Phase 2 で M1 に載せる予定の列）
        pv_g10 : Σ ((floor(mid/GRID_W)+0.5)*GRID_W)  依頼原式・10pt 帯中心加重
        pv_u   : Σ (round(mid/UNIT_FINE)*UNIT_FINE)  依頼原式・最小価格単位加重
        pw     : Σ (mid × 活発滞在秒)   時間加重平均の分子
        w      : Σ (活発滞在秒)         時間加重平均の分母
    """
    ticks = pd.read_parquet(path, columns=_TICK_COLUMNS)
    m1 = ticks_to_m1(ticks)
    if len(m1) == 0:
        return m1
    ts, mid = _ts_and_mid(ticks)
    work = pd.DataFrame({"ts": ts.to_numpy(), "mid": mid.to_numpy()})
    work = work.sort_values("ts", kind="stable", ignore_index=True)
    work["date"] = work["ts"].dt.floor("min")

    m = work["mid"].to_numpy()
    work["pv"] = m
    work["pv_g10"] = (np.floor(m / GRID_W) + 0.5) * GRID_W
    work["pv_u"] = np.round(m / UNIT_FINE) * UNIT_FINE

    cols = ["pv", "pv_g10", "pv_u"]
    if table is not None:
        secs = work["ts"].to_numpy().astype("datetime64[s]").astype("int64")
        dwell = _session_dwell(secs, table)      # len = n-1、始端ティックへ帰属
        w = np.zeros(len(work), dtype="float64")
        w[: len(dwell)] = dwell
        work["w"] = w
        work["pw"] = m * w
        cols += ["pw", "w"]

    g = work.groupby("date", sort=True)
    for col in cols:
        m1[col] = g[col].sum()
    return outlier_policy.repair_day_outliers(m1)


def build_m1(lo: str, hi: str, symbol: str, *, with_dwell: bool = True) -> pd.DataFrame:
    """期間の M1（OHLCV + up/dn + pv 系）を素材化する（本番 CSV 素材化と同じ経路）。

    ``with_dwell=False`` は滞在秒加重（測定 1b）用の列を作らない。長期間（十数年）で
    全ティック時刻を 1 本の配列に集める活発地図の構築を避けるため。
    """
    paths = _day_paths(pd.Timestamp(lo), pd.Timestamp(hi), symbol)
    if not paths:
        raise SystemExit(f"ティック parquet が見つかりません: {lo}..{hi} {symbol}")
    table = _active_table(paths) if with_dwell else None
    frames = [_m1_with_pv(p, table) for p in paths]
    frames = [f for f in frames if len(f)]
    m1 = pd.concat(frames).sort_index(kind="stable")
    if m1.index.has_duplicates:
        m1 = m1[~m1.index.duplicated(keep="last")]
    return m1


def resample_with_pv(m1: pd.DataFrame, tf: str) -> pd.DataFrame:
    """OHLCV は本番 resample_ohlc_tf、pv 系は合算で再集計する（Phase 2 の挙動と同一）。

    OHLCV/up/dn 側が本番と 1 行 1 値まで一致することを都度アサートする（自己検証）。
    """
    rule = TIMEFRAME_RULES[tf]
    base_cols = [c for c in m1.columns if c not in _SUM_EXTRA]
    extra_cols = [c for c in m1.columns if c in _SUM_EXTRA]
    base = resample_ohlc_tf(m1[base_cols], tf)
    if rule is None:
        out = m1.copy()
    else:
        ext = m1[extra_cols]
        if tf in SESSION_TFS:
            # 1D/1W/1M はブローカー暦日で集計する（本番 resample_ohlc_session と同じ index 写像）。
            ext = ext.copy()
            ext.index = _to_broker_naive_index(m1.index)
        out = base.join(ext.resample(rule).sum(), how="left")
    assert out[base_cols].equals(base[base_cols]), f"{tf}: OHLCV が本番 resample と不一致"
    return out


# --------------------------------------------------------------------------- 指標量


def rolling_ratio(num: pd.Series, den: pd.Series, n: int) -> np.ndarray:
    """Σnum / Σden（当該バーを含む直近 n 本・窓不足は NaN）。"""
    return (num.rolling(n).sum() / den.rolling(n).sum()).to_numpy()


def true_range(df: pd.DataFrame) -> np.ndarray:
    prev = df["close"].shift(1)
    hi = np.maximum(df["high"], prev)
    lo = np.minimum(df["low"], prev)
    return (hi - lo).to_numpy()


# --------------------------------------------------------------------------- 検定


def _block_permute(x: np.ndarray, block: int, rng: np.random.Generator) -> np.ndarray:
    """長さ block のブロック単位で並べ替える（ブロック内の自己相関を保存する帰無）。"""
    n = x.size
    nb = max(1, math.ceil(n / block))
    blocks = [x[i * block : (i + 1) * block] for i in range(nb)]
    order = rng.permutation(len(blocks))
    return np.concatenate([blocks[i] for i in order])[:n]


def signed_state_test(
    close: np.ndarray, level: np.ndarray, h: int, block_bars: int, perms: int, seed: int
) -> "dict[str, Any]":
    """s=sign(close−level) の将来 h 本リターンに対する情報量を非重複標本 + ブロック順列で検定する。"""
    n = close.size
    valid = np.isfinite(level)
    start = int(np.argmax(valid)) if valid.any() else n
    idx = np.arange(start, n - h, h)          # 非重複標本（重なり窓の自己相関を作らない）
    idx = idx[np.isfinite(level[idx])]
    if idx.size < 30:
        return {"n": int(idx.size), "skipped": "標本不足"}
    s = np.sign(close[idx] - level[idx])
    r = np.log(close[idx + h] / close[idx])
    keep = (s != 0) & np.isfinite(r)
    s, r = s[keep], r[keep]
    if s.size < 30 or (s > 0).sum() < 5 or (s < 0).sum() < 5:
        return {"n": int(s.size), "skipped": "標本不足"}

    def stat(sv: np.ndarray) -> float:
        return float(r[sv > 0].mean() - r[sv < 0].mean())

    obs = stat(s)
    block = max(1, math.ceil(block_bars / h))  # バー単位のブロック長を標本単位へ換算
    rng = np.random.default_rng(seed)
    null = np.empty(perms, dtype="float64")
    for i in range(perms):
        null[i] = stat(_block_permute(s, block, rng))
    p = float((1 + np.sum(np.abs(null) >= abs(obs))) / (perms + 1))
    return {
        "n": int(s.size),
        "n_up": int((s > 0).sum()),
        "n_dn": int((s < 0).sum()),
        "above_bp": float(r[s > 0].mean() * 1e4),
        "below_bp": float(r[s < 0].mean() * 1e4),
        "diff_bp": float(obs * 1e4),
        "null_sd_bp": float(null.std() * 1e4),
        "p": p,
        "block_samples": int(block),
    }


def _non_overlapping(flags: np.ndarray, h: int, limit: int) -> np.ndarray:
    """成立バーから重ならないエントリー列を取る（採用したら h 本は次を取らない）。"""
    out: "list[int]" = []
    nxt = 0
    for t in np.flatnonzero(flags):
        if t < nxt or t >= limit:
            continue
        out.append(int(t))
        nxt = t + h
    return np.asarray(out, dtype=np.int64)


def deviation_test(
    close: np.ndarray,
    pvwap: np.ndarray,
    sma: np.ndarray,
    *,
    n: int,
    h: int,
    q: float,
    band_window: int,
    perms: int,
    seed: int,
) -> "list[dict[str, Any]]":
    """下方乖離ロング（押し目買い）で 疑似VWAP乖離率 と SMA乖離率 を比較する。

    閾値は乖離率の**因果ローリング経験分位**（当該バー除外・`common.marod_bands.quantile_bands`）。
    エントリーは h 本重ならないよう間引き、帰無は将来リターン系列のブロック順列
    （ブロック長 10N 本）＝エントリー時刻が将来リターンと無関係、という帰無。

    集合: pvwap 全体 / sma 全体 / pvwap のみ成立 / sma のみ成立 / 両方成立。
    「pvwap のみ成立」にエッジがあるかが、pv 列を足す価値の直接の判定になる。
    """
    dev_p = close / pvwap - 1.0
    dev_s = close / sma - 1.0
    lo_p, _ = quantile_bands(dev_p, window_n=band_window, q_low=q, q_high=1.0 - q)
    lo_s, _ = quantile_bands(dev_s, window_n=band_window, q_low=q, q_high=1.0 - q)
    sig_p = np.isfinite(lo_p) & np.isfinite(dev_p) & (dev_p <= lo_p)
    sig_s = np.isfinite(lo_s) & np.isfinite(dev_s) & (dev_s <= lo_s)

    limit = close.size - h
    r = np.full(close.size, np.nan, dtype="float64")
    r[:limit] = np.log(close[h:] / close[:limit])

    sets = {
        "pvwap": sig_p,
        "sma": sig_s,
        "pvwap_only": sig_p & ~sig_s,
        "sma_only": sig_s & ~sig_p,
        "both": sig_p & sig_s,
    }
    idxs = {k: _non_overlapping(v, h, limit) for k, v in sets.items()}
    obs = {k: (float(np.nanmean(r[i])) if i.size else float("nan")) for k, i in idxs.items()}

    base_idx = np.arange(0, limit, h)
    base = float(np.nanmean(r[base_idx]))

    rng = np.random.default_rng(seed)
    block = max(1, 10 * n)
    null = {k: np.empty(perms, dtype="float64") for k in sets}
    for j in range(perms):
        rp = _block_permute(r, block, rng)
        for k, i in idxs.items():
            null[k][j] = float(np.nanmean(rp[i])) if i.size else np.nan

    rows = []
    for k, i in idxs.items():
        if i.size < 20 or not math.isfinite(obs[k]):
            rows.append({"set": k, "n_entries": int(i.size), "skipped": "標本不足"})
            continue
        p = float((1 + np.sum(np.abs(null[k] - base) >= abs(obs[k] - base))) / (perms + 1))
        rows.append({
            "set": k,
            "n_entries": int(i.size),
            "mean_bp": obs[k] * 1e4,
            "base_bp": base * 1e4,
            "excess_bp": (obs[k] - base) * 1e4,
            "null_sd_bp": float(np.nanstd(null[k]) * 1e4),
            "p": p,
        })
    return rows


def forward_return(close: np.ndarray, h: int) -> np.ndarray:
    """t → t+h の対数リターン（末尾 h 本は NaN）。"""
    r = np.full(close.size, np.nan, dtype="float64")
    r[: close.size - h] = np.log(close[h:] / close[: close.size - h])
    return r


def forward_rv(close: np.ndarray, h: int) -> np.ndarray:
    """t+1..t+h の 1 本リターン二乗和の平方根（実現ボラ・末尾 h 本は NaN）。"""
    step = np.full(close.size, np.nan, dtype="float64")
    step[1:] = np.log(close[1:] / close[:-1])
    sq = np.nan_to_num(np.square(step), nan=0.0)
    out = np.full(close.size, np.nan, dtype="float64")
    csum = np.cumsum(sq)
    lim = close.size - h
    if lim > 0:
        out[:lim] = np.sqrt(np.maximum(csum[h:] - csum[:lim], 0.0))
    return out


def contrast_test(
    state: np.ndarray,
    outcome: np.ndarray,
    *,
    n: int,
    h: int,
    q: float,
    band_window: int,
    perms: int,
    seed: int,
    mask: "np.ndarray | None" = None,
) -> "dict[str, Any]":
    """状態変数の因果分位で上位群 / 下位群へ分け、将来量の群間差を検定する。

    上位群 = state >= 因果ローリング (1−q) 分位、下位群 = state <= 因果ローリング q 分位
    （いずれも当該バー除外・`quantile_bands`）。標本は h 本ごとに重ならないよう間引く。
    帰無は将来量系列のブロック順列（ブロック長 10N 本）＝「群の時刻が将来量と無関係」。
    """
    lo, hi = quantile_bands(state, window_n=band_window, q_low=q, q_high=1.0 - q)
    limit = int(np.sum(np.isfinite(outcome)))
    limit = outcome.size - h
    top = np.isfinite(hi) & np.isfinite(state) & (state >= hi)
    bot = np.isfinite(lo) & np.isfinite(state) & (state <= lo)
    if mask is not None:
        top = top & mask
        bot = bot & mask
    i_top = _non_overlapping(top, h, limit)
    i_bot = _non_overlapping(bot, h, limit)
    i_top = i_top[np.isfinite(outcome[i_top])] if i_top.size else i_top
    i_bot = i_bot[np.isfinite(outcome[i_bot])] if i_bot.size else i_bot
    if i_top.size < 20 or i_bot.size < 20:
        return {"n_top": int(i_top.size), "n_bot": int(i_bot.size), "skipped": "標本不足"}

    def diff(y: np.ndarray) -> float:
        return float(np.nanmean(y[i_top]) - np.nanmean(y[i_bot]))

    obs = diff(outcome)
    rng = np.random.default_rng(seed)
    block = max(1, 10 * n)
    null = np.array([diff(_block_permute(outcome, block, rng)) for _ in range(perms)])
    return {
        "n_top": int(i_top.size), "n_bot": int(i_bot.size),
        "top": float(np.nanmean(outcome[i_top])),
        "bot": float(np.nanmean(outcome[i_bot])),
        "diff": obs,
        "null_sd": float(np.nanstd(null)),
        "p": float((1 + np.sum(np.abs(null) >= abs(obs))) / (perms + 1)),
    }


def entry_test(
    flags: np.ndarray,
    outcome: np.ndarray,
    *,
    n: int,
    h: int,
    perms: int,
    seed: int,
) -> "dict[str, Any]":
    """成立バーの将来量が、全時点の基準値を超えるかを検定する（ブロック順列帰無）。"""
    limit = outcome.size - h
    idx = _non_overlapping(flags, h, limit)
    idx = idx[np.isfinite(outcome[idx])] if idx.size else idx
    if idx.size < 20:
        return {"n_entries": int(idx.size), "skipped": "標本不足"}
    base = float(np.nanmean(outcome[np.arange(0, limit, h)]))
    obs = float(np.nanmean(outcome[idx]))
    rng = np.random.default_rng(seed)
    block = max(1, 10 * n)
    null = np.array([float(np.nanmean(_block_permute(outcome, block, rng)[idx]))
                     for _ in range(perms)])
    return {
        "n_entries": int(idx.size), "mean_bp": obs * 1e4, "base_bp": base * 1e4,
        "excess_bp": (obs - base) * 1e4, "null_sd_bp": float(np.nanstd(null) * 1e4),
        "p": float((1 + np.sum(np.abs(null - base) >= abs(obs - base))) / (perms + 1)),
    }


def session_vwap(df: pd.DataFrame) -> np.ndarray:
    """セッション日起点で累積する疑似VWAP（本来の VWAP 用法・日付でリセット）。"""
    t = np.asarray(df.index.astype("datetime64[s]")).astype(np.int64)
    starts = session_day_starts(t)
    pv = df["pv"].to_numpy(dtype="float64")
    vol = df["volume"].to_numpy(dtype="float64")
    out = np.empty(t.size, dtype="float64")
    cum_pv = cum_v = 0.0
    prev = None
    for i in range(t.size):
        if starts[i] != prev:
            cum_pv = cum_v = 0.0
            prev = starts[i]
        cum_pv += pv[i]
        cum_v += vol[i]
        out[i] = cum_pv / cum_v if cum_v > 0 else np.nan
    return out


def session_vwap_and_index(df: pd.DataFrame) -> "tuple[np.ndarray, np.ndarray]":
    """セッション開始からの累積 疑似VWAP と、セッション内での通し本数（0 始まり）。"""
    t = np.asarray(df.index.astype("datetime64[s]")).astype(np.int64)
    starts = session_day_starts(t)
    pv = df["pv"].to_numpy(dtype="float64")
    vol = df["volume"].to_numpy(dtype="float64")
    out = np.empty(t.size, dtype="float64")
    idx = np.empty(t.size, dtype="int64")
    cum_pv = cum_v = 0.0
    k = 0
    prev = None
    for i in range(t.size):
        if starts[i] != prev:
            cum_pv = cum_v = 0.0
            k = 0
            prev = starts[i]
        cum_pv += pv[i]
        cum_v += vol[i]
        out[i] = cum_pv / cum_v if cum_v > 0 else np.nan
        idx[i] = k
        k += 1
    return out, idx


def session_cum_mean(df: pd.DataFrame, col: str) -> np.ndarray:
    """セッション開始からの累積単純平均（OHLCV だけで作れる当日版の対照）。"""
    t = np.asarray(df.index.astype("datetime64[s]")).astype(np.int64)
    starts = session_day_starts(t)
    v = df[col].to_numpy(dtype="float64")
    out = np.empty(t.size, dtype="float64")
    s = 0.0
    c = 0
    prev = None
    for i in range(t.size):
        if starts[i] != prev:
            s = 0.0
            c = 0
            prev = starts[i]
        s += v[i]
        c += 1
        out[i] = s / c
    return out


def holm(pvals: "list[float]") -> "list[float]":
    """Holm 補正後の p 値（単調化込み）。"""
    m = len(pvals)
    order = np.argsort(pvals)
    adj = np.empty(m, dtype="float64")
    run = 0.0
    for rank, i in enumerate(order):
        run = max(run, (m - rank) * pvals[i])
        adj[i] = min(1.0, run)
    return [float(v) for v in adj]


# --------------------------------------------------------------------------- 測定


def measure_period(
    label: str,
    m1: pd.DataFrame,
    tfs: "tuple[str, ...]",
    windows: "tuple[int, ...]",
    horizons: "tuple[int, ...]",
    perms: int,
    seed: int,
    dev_q: float,
    dev_horizons: "tuple[int, ...]",
    band_window: int,
    confirm_only: bool = False,
) -> "dict[str, Any]":
    rows: "list[dict[str, Any]]" = []
    tests: "list[dict[str, Any]]" = []
    devs: "list[dict[str, Any]]" = []
    uses: "list[dict[str, Any]]" = []
    mech: "list[dict[str, Any]]" = []
    for tf in tfs:
        df = resample_with_pv(m1, tf)
        close = df["close"].to_numpy()
        tr_med = float(np.nanmedian(true_range(df)))
        for n in windows:
            pvwap = rolling_ratio(df["pv"], df["volume"], n)
            sma = df["close"].rolling(n).mean().to_numpy()
            has_dwell = "pw" in df.columns and "w" in df.columns
            twap = rolling_ratio(df["pw"], df["w"], n) if has_dwell else np.full(len(df), np.nan)
            g10 = rolling_ratio(df["pv_g10"], df["volume"], n)
            fine = rolling_ratio(df["pv_u"], df["volume"], n)
            # 既存列だけで作れる近似（新規列を足さずに済む対抗案）。
            #   bar_tp : Σ(hlc3 × volume) / Σvolume   古典的な VWAP 近似
            #   bar_oc : Σ(ohlc4 × volume) / Σvolume
            hlc3 = (df["high"] + df["low"] + df["close"]) / 3.0
            ohlc4 = (df["open"] + df["high"] + df["low"] + df["close"]) / 4.0
            bar_tp = rolling_ratio(hlc3 * df["volume"], df["volume"], n)
            bar_oc = rolling_ratio(ohlc4 * df["volume"], df["volume"], n)
            d = pvwap - sma
            ok = np.isfinite(d)
            d_med = float(np.nanmedian(np.abs(d)))
            rows.append({
                "period": label, "tf": tf, "n": n, "bars": int(np.isfinite(pvwap).sum()),
                "tr_median": tr_med,
                # 測定 1
                "abs_d_median": d_med,
                "d_over_tr": d_med / tr_med if tr_med else float("nan"),
                "corr_sma": float(np.corrcoef(pvwap[ok], sma[ok])[0, 1]),
                "abs_twap_diff_median": float(np.nanmedian(np.abs(pvwap - twap))),
                "twap_over_d": (float(np.nanmedian(np.abs(pvwap - twap))) / d_med) if d_med else float("nan"),
                # 測定 2（依頼原式の量子化誤差。信号 D に対する比が本質）
                "q_err_g10_median": float(np.nanmedian(np.abs(g10 - pvwap))),
                "q_err_g10_over_d": float(np.nanmedian(np.abs(g10 - pvwap))) / d_med if d_med else float("nan"),
                "q_err_fine_median": float(np.nanmedian(np.abs(fine - pvwap))),
                "q_err_fine_over_d": float(np.nanmedian(np.abs(fine - pvwap))) / d_med if d_med else float("nan"),
                # 対抗案（既存列のみ・新規列なし）との差。これが小さいなら pv 列追加は不要。
                "bar_tp_err_median": float(np.nanmedian(np.abs(bar_tp - pvwap))),
                "bar_tp_over_d": float(np.nanmedian(np.abs(bar_tp - pvwap))) / d_med if d_med else float("nan"),
                "bar_oc_err_median": float(np.nanmedian(np.abs(bar_oc - pvwap))),
                "bar_oc_over_d": float(np.nanmedian(np.abs(bar_oc - pvwap))) / d_med if d_med else float("nan"),
            })
            if not confirm_only:
                for h in horizons:
                    res = signed_state_test(close, pvwap, h, block_bars=10 * n,
                                            perms=perms, seed=seed)
                    res.update({"period": label, "tf": tf, "n": n, "h": h})
                    tests.append(res)
                for h in dev_horizons:
                    for row in deviation_test(close, pvwap, sma, n=n, h=h, q=dev_q,
                                              band_window=band_window, perms=perms, seed=seed):
                        row.update({"period": label, "tf": tf, "n": n, "h": h, "q": dev_q})
                        devs.append(row)

            # --- 用法 A: pv 固有成分（既存列では作れない残差）に情報があるか
            resid = pvwap - bar_tp
            # --- 用法 C: 疑似VWAP からの乖離幅（ボラティリティ代理）
            spread = np.abs(close - pvwap) / close
            # 対照: 同じ量を SMA で作ったもの。ボラは自己持続するため、この対照を超えない限り
            #       「疑似VWAP 固有の情報」とは言えない。差分が pv 列で初めて得られる成分。
            spread_sma = np.abs(close - sma) / close
            d_spread = spread - spread_sma
            # Δspread が単に spread_sma の裏返しでないかを見る条件付き標本:
            #   spread_sma が因果 25〜75% 分位の中位帯にあるバーだけに限定する。
            sma_lo, sma_hi = quantile_bands(spread_sma, window_n=band_window,
                                            q_low=0.25, q_high=0.75)
            mid_mask = (np.isfinite(sma_lo) & np.isfinite(sma_hi)
                        & (spread_sma >= sma_lo) & (spread_sma <= sma_hi))
            vol_n = df["volume"].rolling(n).sum().to_numpy()
            vol_lo, vol_hi = quantile_bands(vol_n, window_n=band_window,
                                            q_low=0.25, q_high=0.75)
            vol_mid_mask = (np.isfinite(vol_lo) & np.isfinite(vol_hi)
                            & (vol_n >= vol_lo) & (vol_n <= vol_hi))
            step_r = np.full(close.size, np.nan)
            step_r[1:] = np.log(close[1:] / close[:-1])
            past_rv = pd.Series(np.square(step_r)).rolling(n).sum().pow(0.5).to_numpy()
            prv_lo, prv_hi = quantile_bands(past_rv, window_n=band_window,
                                            q_low=0.25, q_high=0.75)
            prv_mid_mask = (np.isfinite(prv_lo) & np.isfinite(prv_hi)
                            & (past_rv >= prv_lo) & (past_rv <= prv_hi))
            for h in dev_horizons:
                fret = forward_return(close, h)
                frv = forward_rv(close, h)
                res = contrast_test(d_spread, frv, n=n, h=h, q=dev_q,
                                    band_window=band_window, perms=perms, seed=seed,
                                    mask=mid_mask)
                for k in ("top", "bot", "diff", "null_sd"):
                    if k in res:
                        res[k + "_bp"] = res.pop(k) * 1e4
                res.update({"period": label, "tf": tf, "n": n, "h": h,
                            "use": "C条件付:Δspread|sma中位→rv"})
                uses.append(res)
                # 交絡の排除: ティック数（既存 tickvol 指標）自体が RV の強い予測子なので、
                #   窓合計 tickvol も中位帯へ固定した標本で残るかを見る。
                res2 = contrast_test(d_spread, frv, n=n, h=h, q=dev_q,
                                     band_window=band_window, perms=perms, seed=seed,
                                     mask=mid_mask & vol_mid_mask)
                for k in ("top", "bot", "diff", "null_sd"):
                    if k in res2:
                        res2[k + "_bp"] = res2.pop(k) * 1e4
                res2.update({"period": label, "tf": tf, "n": n, "h": h,
                             "use": "C条件付2:Δspread|sma中位&tickvol中位→rv"})
                uses.append(res2)
                # 交絡の排除（測定 7 で判明した分）: Δspread 上位群は直近ボラが 7〜12% 低い。
                #   過去 N 本の実現ボラも中位帯へ固定して、ボラの自己持続で説明できるかを見る。
                res3 = contrast_test(d_spread, frv, n=n, h=h, q=dev_q,
                                     band_window=band_window, perms=perms, seed=seed,
                                     mask=mid_mask & vol_mid_mask & prv_mid_mask)
                for k in ("top", "bot", "diff", "null_sd"):
                    if k in res3:
                        res3[k + "_bp"] = res3.pop(k) * 1e4
                res3.update({"period": label, "tf": tf, "n": n, "h": h,
                             "use": "C条件付3:Δspread|sma&tickvol&過去RV中位→rv"})
                uses.append(res3)
                # 「静か＝疑似VWAP へ戻る」なのかを直接測る。
                #   gap_chg : |close−疑似VWAP| が h 本後にどれだけ縮むか（負＝縮む＝戻る）
                #   sgn_chg : 符号付き (close−疑似VWAP) の変化（負＝下へ動く＝上から戻る）
                gap = np.abs(close - pvwap)
                sgn = close - pvwap
                gap_chg = np.full(close.size, np.nan)
                sgn_chg = np.full(close.size, np.nan)
                lim2 = close.size - h
                if lim2 > 0:
                    gap_chg[:lim2] = (gap[h:] - gap[:lim2]) / close[:lim2]
                    sgn_chg[:lim2] = (sgn[h:] - sgn[:lim2]) / close[:lim2]
                # 乖離が縮むのは「価格が戻る」からか「平均が追いつく」からかを分解する。
                close_chg = np.full(close.size, np.nan)
                pvwap_chg = np.full(close.size, np.nan)
                if lim2 > 0:
                    close_chg[:lim2] = (close[h:] - close[:lim2]) / close[:lim2]
                    pvwap_chg[:lim2] = (pvwap[h:] - pvwap[:lim2]) / close[:lim2]
                for oname, ov in (("gap縮小", gap_chg), ("符号付き変化", sgn_chg),
                                  ("終値の動き", close_chg), ("疑似VWAPの動き", pvwap_chg)):
                    r4 = contrast_test(d_spread, ov, n=n, h=h, q=dev_q,
                                       band_window=band_window, perms=perms, seed=seed,
                                       mask=mid_mask & vol_mid_mask & prv_mid_mask)
                    for k in ("top", "bot", "diff", "null_sd"):
                        if k in r4:
                            r4[k + "_bp"] = r4.pop(k) * 1e4
                    r4.update({"period": label, "tf": tf, "n": n, "h": h,
                               "use": f"D:Δspread→{oname}"})
                    uses.append(r4)

                # --- 当日版（セッションアンカー）で同じボラ予測を測る。
                #   svwap    : セッション開始からの累積 疑似VWAP
                #   scum_ma  : セッション開始からの累積 終値平均（OHLCV だけで作れる対照）
                #   Δs_spread: 両者の乖離幅の差＝当日版の pv 固有成分
                sv, sidx = session_vwap_and_index(df)
                scum_ma = session_cum_mean(df, "close")
                s_spread = np.abs(close - sv) / close
                s_spread_ma = np.abs(close - scum_ma) / close
                ds_spread = s_spread - s_spread_ma
                # 場中序盤は累積平均が不安定なので開始 24 本（5m で 2 時間）を除く。
                warm = sidx >= 24
                for sname, sstate, smask in (
                    ("E当日:Δs_spread→rv", ds_spread, warm),
                    ("E当日:Δs_spread|過去RV&tickvol中位→rv", ds_spread,
                     warm & vol_mid_mask & prv_mid_mask),
                    ("E当日対照:s_spread→rv", s_spread, warm),
                ):
                    r5 = contrast_test(sstate, frv, n=n, h=h, q=dev_q,
                                       band_window=band_window, perms=perms, seed=seed,
                                       mask=smask)
                    for k in ("top", "bot", "diff", "null_sd"):
                        if k in r5:
                            r5[k + "_bp"] = r5.pop(k) * 1e4
                    r5.update({"period": label, "tf": tf, "n": n, "h": h, "use": sname})
                    uses.append(r5)
                if confirm_only:
                    continue
                for name, state, outcome, unit in (
                    ("A:resid→ret", resid, fret, "bp"),
                    ("A:resid→rv", resid, frv, "bp"),
                    ("C:spread→rv", spread, frv, "bp"),
                    ("C:spread→ret", spread, fret, "bp"),
                    ("C対照:spread_sma→rv", spread_sma, frv, "bp"),
                    ("C対照:spread_sma→ret", spread_sma, fret, "bp"),
                    ("C固有:Δspread→rv", d_spread, frv, "bp"),
                    ("C固有:Δspread→ret", d_spread, fret, "bp"),
                ):
                    res = contrast_test(state, outcome, n=n, h=h, q=dev_q,
                                        band_window=band_window, perms=perms, seed=seed)
                    for k in ("top", "bot", "diff", "null_sd"):
                        if k in res:
                            res[k + "_bp"] = res.pop(k) * 1e4
                    res.update({"period": label, "tf": tf, "n": n, "h": h, "use": name})
                    uses.append(res)

            # --- 機序の記述: Δspread 上位群 / 下位群が「何が違うバーなのか」を共変量で測る。
            #     仮説を立てず、観測できる量の群間差だけを出す（機序の断定はしない）。
            if not confirm_only:
                lo_d, hi_d = quantile_bands(d_spread, window_n=band_window,
                                            q_low=dev_q, q_high=1.0 - dev_q)
                step = np.full(close.size, np.nan)
                step[1:] = np.abs(close[1:] - close[:-1])
                path = pd.Series(step).rolling(n).sum().to_numpy()
                net = np.full(close.size, np.nan)
                net[n:] = np.abs(close[n:] - close[:-n])
                covars = {
                    "トレンド効率(net/path)": net / path,
                    "過去RV(直近N本)": pd.Series(np.square(np.log(
                        np.concatenate([[np.nan], close[1:] / close[:-1]])
                    ))).rolling(n).sum().pow(0.5).to_numpy() * 1e4,
                    "窓tickvol": df["volume"].rolling(n).sum().to_numpy(),
                    "疑似VWAP−SMA(pt)": pvwap - sma,
                    "close−疑似VWAP(pt)": close - pvwap,
                }
                top_m = np.isfinite(hi_d) & (d_spread >= hi_d)
                bot_m = np.isfinite(lo_d) & (d_spread <= lo_d)
                for cname, cv in covars.items():
                    mech.append({
                        "period": label, "tf": tf, "n": n, "covar": cname,
                        "top": float(np.nanmedian(cv[top_m])),
                        "bot": float(np.nanmedian(cv[bot_m])),
                        "ratio": float(np.nanmedian(cv[top_m]) / np.nanmedian(cv[bot_m]))
                        if np.nanmedian(cv[bot_m]) else float("nan"),
                    })

            # --- 用法 B: セッションアンカーVWAP（本来の VWAP 用法・N に依存しないので n 最小のみ）
            if (not confirm_only) and n == min(windows):
                svwap = session_vwap(df)
                sdev = close / svwap - 1.0
                lo_s, hi_s = quantile_bands(sdev, window_n=band_window,
                                            q_low=dev_q, q_high=1.0 - dev_q)
                cross_up = np.zeros(close.size, dtype=bool)
                cross_up[1:] = (close[1:] > svwap[1:]) & (close[:-1] <= svwap[:-1])
                flags = {
                    "B:下方乖離ロング": np.isfinite(lo_s) & (sdev <= lo_s),
                    "B:上方乖離（反転）": np.isfinite(hi_s) & (sdev >= hi_s),
                    "B:上抜け順張り": cross_up & np.isfinite(svwap),
                }
                for h in dev_horizons:
                    fret = forward_return(close, h)
                    for name, fl in flags.items():
                        res = entry_test(fl, fret, n=n, h=h, perms=perms, seed=seed)
                        res.update({"period": label, "tf": tf, "n": n, "h": h, "use": name})
                        uses.append(res)
    live = [t for t in tests if "p" in t]
    for t, adj in zip(live, holm([t["p"] for t in live])):
        t["p_holm"] = adj
    return {"rows": rows, "tests": tests, "devs": devs, "uses": uses, "mech": mech}


def measure_forming(path: Path, seed: int) -> "dict[str, Any]":
    """測定 4: 部分窓（形成中バー）でも Σmid が厳密に足せることを tick から直接検算する。"""
    ticks = pd.read_parquet(path, columns=_TICK_COLUMNS)
    ts, mid = _ts_and_mid(ticks)
    work = pd.DataFrame({"ts": ts.to_numpy(), "mid": mid.to_numpy()}).sort_values(
        "ts", kind="stable", ignore_index=True
    )
    work["date"] = work["ts"].dt.floor("min")
    per_min = work.groupby("date", sort=True)["mid"].agg(["sum", "count"])
    rng = np.random.default_rng(seed)
    worst = 0.0
    checks = 0
    minutes = per_min.index.to_numpy()
    for _ in range(50):
        if len(minutes) < 10:
            break
        i = int(rng.integers(5, len(minutes)))
        start = minutes[i - 5]
        cut = minutes[i] + np.timedelta64(int(rng.integers(1, 60)), "s")
        sel = work[(work["ts"] >= start) & (work["ts"] < cut)]
        if len(sel) == 0:
            continue
        closed = per_min.loc[(per_min.index >= start) & (per_min.index < minutes[i])]
        part = work[(work["ts"] >= minutes[i]) & (work["ts"] < cut)]
        num = float(closed["sum"].sum() + part["mid"].sum())
        den = float(closed["count"].sum() + len(part))
        exact = float(sel["mid"].sum()) / len(sel)
        worst = max(worst, abs(num / den - exact))
        checks += 1
    return {"file": str(path), "checks": checks, "max_abs_error": worst}


# --------------------------------------------------------------------------- 出力


def _fmt(v: Any) -> str:
    if isinstance(v, float):
        if not math.isfinite(v):
            return "nan"
        return f"{v:.6g}"
    return str(v)


def print_table(rows: "list[dict[str, Any]]", cols: "list[str]") -> None:
    if not rows:
        print("（該当なし）")
        return
    widths = [max(len(c), *(len(_fmt(r.get(c, ""))) for r in rows)) for c in cols]
    print("  ".join(c.ljust(w) for c, w in zip(cols, widths)))
    print("  ".join("-" * w for w in widths))
    for r in rows:
        print("  ".join(_fmt(r.get(c, "")).ljust(w) for c, w in zip(cols, widths)))


def main(argv: "list[str] | None" = None) -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--periods", nargs="+", default=["2024-01-01:2024-12-31", "2026-01-01:2026-07-31"])
    ap.add_argument("--symbol", default="JP225")
    ap.add_argument("--tfs", nargs="+", default=list(DEFAULT_TFS))
    ap.add_argument("--windows", nargs="+", type=int, default=list(DEFAULT_WINDOWS))
    ap.add_argument("--horizons", nargs="+", type=int, default=list(DEFAULT_HORIZONS))
    ap.add_argument("--perms", type=int, default=DEFAULT_PERMS)
    ap.add_argument("--dev-q", type=float, default=0.10, help="下方乖離エントリーの因果分位")
    ap.add_argument("--dev-horizons", nargs="+", type=int, default=[5, 20])
    ap.add_argument("--band-window", type=int, default=1000, help="乖離率分位の因果窓（本）")
    ap.add_argument("--no-dwell", action="store_true", help="滞在秒加重（測定 1b）を省く")
    ap.add_argument("--confirm-only", action="store_true",
                    help="確定条件（条件付 Δspread→rv）だけを回す")
    ap.add_argument("--seed", type=int, default=20260802)
    ap.add_argument("--json", default=None)
    args = ap.parse_args(argv)

    out: "dict[str, Any]" = {"gate_ratio": GATE_RATIO, "periods": {}}
    all_rows: "list[dict[str, Any]]" = []
    all_tests: "list[dict[str, Any]]" = []
    all_devs: "list[dict[str, Any]]" = []
    all_uses: "list[dict[str, Any]]" = []
    all_mech: "list[dict[str, Any]]" = []
    forming: "list[dict[str, Any]]" = []

    for spec in args.periods:
        lo, hi = spec.split(":")
        print(f"\n=== 期間 {spec} ===", flush=True)
        m1 = build_m1(lo, hi, args.symbol, with_dwell=not args.no_dwell)
        print(f"M1 {len(m1):,} 本 / {m1.index[0]} .. {m1.index[-1]}", flush=True)
        res = measure_period(
            spec, m1, tuple(args.tfs), tuple(args.windows), tuple(args.horizons),
            args.perms, args.seed, args.dev_q, tuple(args.dev_horizons), args.band_window,
            args.confirm_only,
        )
        out["periods"][spec] = res
        all_rows += res["rows"]
        all_tests += res["tests"]
        all_devs += res["devs"]
        all_uses += res["uses"]
        all_mech += res["mech"]

        paths = _day_paths(pd.Timestamp(lo), pd.Timestamp(hi), args.symbol)
        forming.append(measure_forming(paths[len(paths) // 2], args.seed))

    print("\n### 測定 1: 非退化ゲート（D = 疑似VWAP − SMA）")
    print_table(all_rows, ["period", "tf", "n", "bars", "tr_median", "abs_d_median",
                           "d_over_tr", "corr_sma", "abs_twap_diff_median", "twap_over_d"])
    print(f"\n判定基準（事前登録）: d_over_tr >= {GATE_RATIO} で通過")
    worst = min(r["d_over_tr"] for r in all_rows)
    best = max(r["d_over_tr"] for r in all_rows)
    print(f"d_over_tr の範囲: {worst:.4f} .. {best:.4f} → "
          f"{'通過' if worst >= GATE_RATIO else ('一部通過' if best >= GATE_RATIO else '不通過')}")

    print("\n### 測定 2: 依頼原式（価格帯経由）の量子化誤差")
    print_table(all_rows, ["period", "tf", "n", "abs_d_median", "q_err_g10_median",
                           "q_err_g10_over_d", "q_err_fine_median", "q_err_fine_over_d"])

    print("\n### 測定 2b: 既存列だけの近似（新規列なし）で代替できるか")
    print_table(all_rows, ["period", "tf", "n", "abs_d_median", "bar_tp_err_median",
                           "bar_tp_over_d", "bar_oc_err_median", "bar_oc_over_d"])

    print("\n### 測定 3: 情報量（非重複標本・ブロック順列・Holm 補正）")
    print_table([t for t in all_tests if "p" in t],
                ["period", "tf", "n", "h", "n_up", "n_dn", "above_bp", "below_bp",
                 "diff_bp", "null_sd_bp", "p", "p_holm"])
    sig = [t for t in all_tests if t.get("p_holm", 1.0) < 0.05]
    print(f"Holm 後 p<0.05: {len(sig)} 件 / {len([t for t in all_tests if 'p' in t])} 件")

    print("\n### 測定 5: 下方乖離ロング（押し目買い）— 疑似VWAP乖離率 vs SMA乖離率")
    # Holm は集合（set）ごとの族内で補正する。判定の主役は pvwap_only（pv 列を足す価値の直接判定）。
    for name in ("pvwap", "sma", "pvwap_only", "sma_only", "both"):
        fam = [d for d in all_devs if d["set"] == name and "p" in d]
        for d, adj in zip(fam, holm([d["p"] for d in fam])):
            d["p_holm"] = adj
    for name in ("pvwap_only", "sma_only", "both", "pvwap", "sma"):
        fam = [d for d in all_devs if d["set"] == name and "p" in d]
        if not fam:
            continue
        print(f"\n-- 集合 {name}（Holm は本族内 {len(fam)} 件で補正）")
        print_table(fam, ["period", "tf", "n", "h", "n_entries", "mean_bp", "base_bp",
                          "excess_bp", "null_sd_bp", "p", "p_holm"])
        hit = [d for d in fam if d["p_holm"] < 0.05]
        print(f"Holm 後 p<0.05: {len(hit)} 件 / {len(fam)} 件")

    print("\n### 測定 6: 別用法（A: pv 固有成分 / B: セッションアンカーVWAP / C: ボラ代理）")
    for name in sorted({u["use"] for u in all_uses}):
        fam = [u for u in all_uses if u["use"] == name and "p" in u]
        if not fam:
            skipped = [u for u in all_uses if u["use"] == name]
            print(f"\n-- {name}: 全 {len(skipped)} 条件が標本不足")
            continue
        for u, adj in zip(fam, holm([u["p"] for u in fam])):
            u["p_holm"] = adj
        print(f"\n-- {name}（Holm は本族内 {len(fam)} 件で補正）")
        if "diff_bp" in fam[0]:
            print_table(fam, ["period", "tf", "n", "h", "n_top", "n_bot", "top_bp",
                              "bot_bp", "diff_bp", "null_sd_bp", "p", "p_holm"])
        else:
            print_table(fam, ["period", "tf", "n", "h", "n_entries", "mean_bp", "base_bp",
                              "excess_bp", "null_sd_bp", "p", "p_holm"])
        hit = [u for u in fam if u["p_holm"] < 0.05]
        print(f"Holm 後 p<0.05: {len(hit)} 件 / {len(fam)} 件")

    if all_mech:
        print("\n### 測定 7: Δspread 上位群 / 下位群は何が違うバーか（記述統計・中央値）")
        print_table(all_mech, ["period", "tf", "n", "covar", "top", "bot", "ratio"])

    print("\n### 測定 4: 形成中バー（部分窓）での厳密性")
    print_table(forming, ["file", "checks", "max_abs_error"])

    out["forming"] = forming
    if args.json:
        Path(args.json).write_text(json.dumps(out, indent=2, ensure_ascii=False))
        print(f"\nJSON: {args.json}")


if __name__ == "__main__":
    main()
