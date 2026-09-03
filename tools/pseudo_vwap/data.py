"""data — 疑似VWAP 検証の素材化 Gateway（ISSUE-479 Wave2 M-4）。

本モジュールは日別ティック parquet を読み、M1（OHLCV + up/dn + pv 系）へ縮約する。
**marketdata / market_profile_api を知るのは本モジュールだけ**であり、指標・検定・測定の各層は
ここが返す DataFrame だけを見る（式を検証するのに実データの木を要求しない）。

集計・列・mid・セッション写像はすべて本番の唯一源へ委譲し、式を写さない。他パッケージの
private 名には依存しない（公開名は ISSUE-479 M-4 で加法的に足した）。
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

from marketdata import keep_last, outlier_policy
from marketdata.resample import (
    SESSION_TFS,
    TIMEFRAME_RULES,
    resample_ohlc_tf,
    to_broker_naive_index,
)
# 生ティックの必須列・mid 規則・M1 集計は本番の単一規則源をそのまま使う（式を写さない）。
from marketdata.session_day import session_day_starts
from marketdata.tick_m1 import TICK_COLUMNS, day_parquet_files, ticks_to_m1, ts_and_mid
# 価格帯（依頼原式）の解像度も既存実装の値を参照する（定数を写さない）。
from market_profile_api.compute.market_profile_dwell_kernel import GRID_W, session_dwell
from market_profile_api.compute.session_activity import ACTIVE_FRAC, build_active_table
from market_profile_api.controller.tf_period_profile_controller import UNIT_BY_TF

UNIT_FINE = float(UNIT_BY_TF["1m"])  # 1m の最小価格単位（実測 0.0255）。

# 合算集約する追加列（Phase 2 で csv_schema.SUM_COLUMNS へ入る予定の列と同じ性質）。
_SUM_EXTRA = ("pv", "pv_g10", "pv_u", "pw", "w")


def tick_work_frame(ticks: pd.DataFrame) -> pd.DataFrame:
    """生ティック frame → 時刻・mid・分床の 3 列を持つ作業 frame（並びの単一規則源）。

    素材化（:func:`_m1_with_pv`）と形成中バーの検算（測定 4）が同じ並び・同じ分床を見るため、
    構成をここ 1 箇所に閉じる。
    """
    ts, mid = ts_and_mid(ticks)
    work = pd.DataFrame({"ts": ts.to_numpy(), "mid": mid.to_numpy()})
    work = work.sort_values("ts", kind="stable", ignore_index=True)
    work["date"] = work["ts"].dt.floor("min")
    return work


def read_day_work(path: Path) -> pd.DataFrame:
    """1 日分のティック parquet を読み、:func:`tick_work_frame` の作業 frame を返す。"""
    return tick_work_frame(pd.read_parquet(path, columns=TICK_COLUMNS))


def session_starts(df: pd.DataFrame) -> np.ndarray:
    """バー index が属するセッション日の始端（UNIX 秒）。境界規則は marketdata の唯一源。"""
    t = np.asarray(df.index.astype("datetime64[s]")).astype(np.int64)
    return session_day_starts(t)


def day_paths(lo: pd.Timestamp, hi: pd.Timestamp, symbol: str) -> "list[Path]":
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
    ticks = pd.read_parquet(path, columns=TICK_COLUMNS)
    m1 = ticks_to_m1(ticks)
    if len(m1) == 0:
        return m1
    work = tick_work_frame(ticks)

    m = work["mid"].to_numpy()
    work["pv"] = m
    work["pv_g10"] = (np.floor(m / GRID_W) + 0.5) * GRID_W
    work["pv_u"] = np.round(m / UNIT_FINE) * UNIT_FINE

    cols = ["pv", "pv_g10", "pv_u"]
    if table is not None:
        secs = work["ts"].to_numpy().astype("datetime64[s]").astype("int64")
        dwell = session_dwell(secs, table)      # len = n-1、始端ティックへ帰属
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
    paths = day_paths(pd.Timestamp(lo), pd.Timestamp(hi), symbol)
    if not paths:
        raise SystemExit(f"ティック parquet が見つかりません: {lo}..{hi} {symbol}")
    table = _active_table(paths) if with_dwell else None
    frames = [_m1_with_pv(p, table) for p in paths]
    frames = [f for f in frames if len(f)]
    m1 = pd.concat(frames).sort_index(kind="stable")
    # keep-last の規則は marketdata.keep_last（唯一の実体・ISSUE-479 F-6）へ委譲する。
    return keep_last.dedupe_index_keep_last(m1)


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
            ext.index = to_broker_naive_index(m1.index)
        out = base.join(ext.resample(rule).sum(), how="left")
    assert out[base_cols].equals(base[base_cols]), f"{tf}: OHLCV が本番 resample と不一致"
    return out
