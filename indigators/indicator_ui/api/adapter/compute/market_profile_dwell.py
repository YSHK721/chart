"""market_profile_dwell — 実ティック滞在（真の time-at-price・セッション認識）プロファイル計算。

``src=candle``（足レンジ TPO・:mod:`market_profile`）に対し、本モジュールは ``src=dwell`` を担う。
原子＝「価格帯の実ティック滞在秒」で集計する。応答スキーマ（bins/poc/va_low/va_high/price_min/
price_max/tpo_units/n_bins）は candle 版と同一に保つ（tpo は dwell 秒＝int へ丸め）。

セッション認識（休場自動除外）:
    (曜日×時) のティック密度から活発/休場を判定し、隣接ティック間ギャップのうち「活発な時間帯に
    属する秒」だけを滞在に計上する。これにより週末・日次メンテの休場帯を除外しつつ、取引中の
    静かな滞在は満額残す（試作 prototype_260630-01/mp_core.py が実証したアルゴリズムを本体作法へ移植）。

perf（単一スレッド常駐サーバ保護・必須）:
    - dwell 集計の対象日数に上限 :data:`_MAX_DWELL_DAYS` を設け、要求窓がそれ以上でも直近
      ``_MAX_DWELL_DAYS`` 日ぶんのサブ窓に限定する（初回は per-day parquet 逐次読込で日数比例のため）。
    - 固定グリッド日別ロールアップをメモリキャッシュし、2 回目以降を高速化する（走査した過去日ぶんが
      ``_DAY_CACHE`` / ``_PARTIAL_CACHE`` に累積する。各エントリは固定グリッドの小配列なのでメモリは緩く
      有界。現在進行中の当日は Y2a によりキャッシュせず都度計算する）。active table はプロセス内で 1 回だけ
      構築しキャッシュする。

依存方向: 本モジュールは numpy + pandas + :mod:`marketdata.tick_m1`（正準ティック経路・read-only）に
のみ依存し、:mod:`market_profile` の ``_value_area``（POC/VA の単一定義）を import して再利用する（DRY）。
marketdata は import して使うだけ（既存データは読むだけ・波及させない）。
"""

from __future__ import annotations

import sys as _sys
import time as _time
from pathlib import Path as _Path
from typing import Any

import numpy as np
import pandas as pd

# POC/VA は candle 版の単一定義を再利用する（DRY・同一定義）。
from adapter.compute.market_profile import _value_area

# repo 根を sys.path へ（marketdata を import するため・dataset/forming_bar と同じロード境界）。
_WORKSPACE_ROOT = _Path(__file__).resolve().parents[5]
if str(_WORKSPACE_ROOT) not in _sys.path:
    _sys.path.insert(0, str(_WORKSPACE_ROOT))
from marketdata.tick_m1 import day_parquet_files  # noqa: E402  (正準ティック経路・read-only)

# datasetRef → 実ティック symbol 解決（forming_bar.TICK_REFS と整合。'jp225_tick'→'JP225'）。
TICK_REF_SYMBOLS: dict[str, str] = {"jp225_tick": "JP225"}

# セッション認識 dwell のパラメータ（試作と一致）。
_ACTIVE_FRAC = 0.10   # (曜日×時) のティック数が ピーク×この割合 未満なら「休場」とみなす。
GRID_W = 10.0         # 固定価格グリッド幅(pt)。日別集計→窓合算→表示 bin へ再集計する中間解像度。

# perf 上限（単一スレッド常駐サーバ保護・必須）。
MAX_DWELL_DAYS = 250      # dwell 集計の対象日数上限（直近ぶんに限定）。controller がレンジ算出にも参照する。
_MAX_DWELL_DAYS = MAX_DWELL_DAYS  # 後方互換の別名（既存テスト・内部参照が使用）。
_ACTIVE_TABLE_DAYS = 120  # active table 構築に用いる直近日数（試作と同じ・一度だけ構築）。

# 生ティック parquet の必須列（marketdata.tick_m1._TICK_COLUMNS と同じ意味）。
_TICK_COLUMNS = ["timestamp", "bidPrice", "askPrice"]
_OUTLIER_FRAC = 0.30      # 窓内 mid 中央値 ±30% の外れ値除去（tick_window と同基準）。

# プロセス内キャッシュ（AB 兼用・perf）。走査した過去日ぶんが累積する（各エントリは小配列＝緩く有界）。
# 完了した過去日/窓のみ登録し、現在進行中の当日はキャッシュしない（Y2a・_day_rollup/_partial_rollup 参照）。
_DAY_CACHE: dict[tuple[str, int], "dict | None"] = {}      # (symbol, day_start) → rollup or None
_PARTIAL_CACHE: dict[tuple[str, int, int], "dict | None"] = {}  # (symbol, lo, hi) → rollup or None
_ACTIVE_TABLE: dict[str, np.ndarray] = {}                  # symbol → 7×24 bool 活動テーブル

_EMPTY_SECS = np.array([], dtype=np.int64)
_EMPTY_MIDS = np.array([], dtype=np.float64)


def resolve_symbol(ref: Any) -> "str | None":
    """datasetRef を実ティック symbol へ解決する（非 tick ref は None）。"""
    return TICK_REF_SYMBOLS.get(ref)


def _reset_caches() -> None:
    """プロセス内キャッシュを全消去する（テスト隔離・データ更新時の明示無効化用）。"""
    _DAY_CACHE.clear()
    _PARTIAL_CACHE.clear()
    _ACTIVE_TABLE.clear()


# --------------------------------------------------------------------------- #
# 窓ティック読込（単一注入点。テストはここを monkeypatch して合成ティックを注入する）
# --------------------------------------------------------------------------- #
def _load_window_ticks(symbol: str, start: Any, end: Any) -> "tuple[np.ndarray, np.ndarray]":
    """``[start, end)`` の実ティックを ``(secs:int64, mids:float64)`` で返す（メモリ有界・時系列順）。

    正準ティック経路 :func:`marketdata.tick_m1.day_parquet_files` で日別 parquet を列挙し、各を
    ``timestamp/bidPrice/askPrice`` 列で読む → concat → tz 除去し UTC 秒 int64 へ → 窓 ``[start,end)``
    マスク → mid=(bid+ask)/2 → 窓内 mid 中央値 ±30% の外れ値除去 → secs で安定ソート。空なら空配列。
    """
    s, e = int(start), int(end)
    lo_day = pd.Timestamp(s, unit="s").normalize()
    hi_day = pd.Timestamp(max(s, e - 1), unit="s").normalize()
    files = day_parquet_files(lo_day, hi_day, symbol=symbol)
    if not files:
        return _EMPTY_SECS, _EMPTY_MIDS
    frames = [pd.read_parquet(p, columns=_TICK_COLUMNS) for p in files]
    tdf = pd.concat(frames, ignore_index=True) if len(frames) > 1 else frames[0]

    ts = pd.to_datetime(tdf["timestamp"])
    if getattr(ts.dt, "tz", None) is not None:
        ts = ts.dt.tz_convert("UTC").dt.tz_localize(None)
    secs = ts.to_numpy().astype("datetime64[s]").astype("int64")
    win = (secs >= s) & (secs < e)
    secs = secs[win]
    mids = ((tdf["bidPrice"].to_numpy(dtype="float64") + tdf["askPrice"].to_numpy(dtype="float64"))
            / 2.0)[win]
    if len(mids):
        m = float(np.median(mids))
        if m > 0:
            keep = np.abs(mids / m - 1.0) <= _OUTLIER_FRAC
            secs, mids = secs[keep], mids[keep]
    order = np.argsort(secs, kind="stable")
    return secs[order].astype(np.int64), mids[order].astype(np.float64)


# --------------------------------------------------------------------------- #
# セッション認識 dwell（活動テーブル + 活発秒の積分）
# --------------------------------------------------------------------------- #
def _build_active_table(secs: np.ndarray) -> np.ndarray:
    """ティックから (曜日0-6 × 時0-23) の活動テーブル（True=活発/False=休場）を作る。

    曜日 = ``((s//86400)+3)%7``（1970-01-01=木を Mon0 基準へ）、時 = ``(s%86400)//3600``。
    バケット別ティック数が ピーク×``_ACTIVE_FRAC`` 以上を活発とする。
    """
    s = np.asarray(secs, dtype=np.int64)
    wd = ((s // 86400) + 3) % 7
    hod = (s % 86400) // 3600
    cnt = np.zeros((7, 24), dtype=np.int64)
    np.add.at(cnt, (wd, hod), 1)
    thr = cnt.max() * _ACTIVE_FRAC
    return cnt >= thr


def _active_seconds_cross(a: int, b: int, table: np.ndarray) -> int:
    """``[a, b)`` のうち活発な (曜日×時) に属する秒数を時間境界で積分する（跨ぎギャップ用）。"""
    total = 0
    t = int(a)
    b = int(b)
    while t < b:
        nb = (t // 3600 + 1) * 3600
        seg = min(nb, b)
        wd = ((t // 86400) + 3) % 7
        if table[wd, (t % 86400) // 3600]:
            total += seg - t
        t = seg
    return total


def _session_dwell(secs: np.ndarray, table: np.ndarray) -> np.ndarray:
    """各隣接ティック間ギャップの「活発秒」を返す（``len = len(secs)-1``）。

    同一時内は活発なら ``gap``/休場なら 0。時境界を跨ぐギャップのみ :func:`_active_seconds_cross`
    で厳密に積分する。dwell[i] はギャップ始端のティック（価格 mids[i]）に帰属する。
    """
    s = np.asarray(secs, dtype=np.int64)
    if s.size < 2:
        return np.zeros(max(s.size - 1, 0), dtype=float)
    start = s[:-1]
    end = s[1:]
    gap = (end - start).astype(float)
    wd = ((start // 86400) + 3) % 7
    hod = (start % 86400) // 3600
    act_start = table[wd, hod]
    same_hour = (start // 3600) == (end // 3600)
    # 同一時内: 活発なら gap、休場なら 0。
    dwell = np.where(same_hour & act_start, gap, 0.0)
    # 時境界を跨ぐギャップのみ厳密に積分（件数は僅少）。
    for i in np.where(~same_hour)[0]:
        dwell[i] = _active_seconds_cross(int(start[i]), int(end[i]), table)
    return dwell


# --------------------------------------------------------------------------- #
# 固定グリッド日別ロールアップ（メモリキャッシュ）
# --------------------------------------------------------------------------- #
def _rollup_ticks(secs: np.ndarray, mids: np.ndarray, table: np.ndarray) -> "dict | None":
    """ティック配列を固定グリッド ``{kmin, dwell[]}``（k=floor(mid/GRID_W)）へ集約する。空なら None。"""
    if len(secs) == 0:
        return None
    dwell = _session_dwell(secs, table)  # len = len(secs)-1
    k = np.floor(mids / GRID_W).astype(np.int64)
    kmin = int(k.min())
    size = int(k.max()) - kmin + 1
    dwell_arr = np.zeros(size, dtype=float)
    if dwell.size:
        np.add.at(dwell_arr, k[:-1] - kmin, dwell)  # dwell[i] は始端ティック価格 k[i] に帰属。
    return {"kmin": kmin, "dwell": dwell_arr}


def _active_table(symbol: str, at_from: int, win_to: int) -> np.ndarray:
    """symbol の活動テーブルをプロセス内で 1 回だけ構築してキャッシュする（直近ぶんから）。"""
    cached = _ACTIVE_TABLE.get(symbol)
    if cached is not None:
        return cached
    secs, _ = _load_window_ticks(symbol, at_from, win_to)
    table = _build_active_table(secs) if len(secs) else np.ones((7, 24), dtype=bool)
    _ACTIVE_TABLE[symbol] = table
    return table


def _day_rollup(symbol: str, day_start: int, table: np.ndarray, now: float) -> "dict | None":
    """1 カレンダー日 ``[day_start, day_start+86400)`` を固定グリッドへ集約する。

    Y2a: 完了した過去日（``day_start + 86400 <= now``）のみキャッシュする。現在進行中の当日
    （UTC 未確定日）はキャッシュせず毎回再計算し、新ティック到着による stale 化を防ぐ。
    """
    key = (symbol, int(day_start))
    if key in _DAY_CACHE:
        return _DAY_CACHE[key]
    secs, mids = _load_window_ticks(symbol, day_start, day_start + 86400)
    roll = _rollup_ticks(secs, mids, table)
    if int(day_start) + 86400 <= now:  # 完了日のみキャッシュ（未完了の当日は都度計算）。
        _DAY_CACHE[key] = roll
    return roll


def _partial_rollup(symbol: str, lo: int, hi: int, table: np.ndarray, now: float) -> "dict | None":
    """境界日（サブ日足）用の部分集計 ``[lo, hi)`` を固定グリッドへ集約する。

    Y2a: 窓終端が完了した（``hi <= now``）場合のみキャッシュする。当日の部分足（``hi > now``）は
    新ティックで stale 化しうるため毎回再計算する。
    """
    key = (symbol, int(lo), int(hi))
    if key in _PARTIAL_CACHE:
        return _PARTIAL_CACHE[key]
    secs, mids = _load_window_ticks(symbol, lo, hi)
    roll = _rollup_ticks(secs, mids, table)
    if int(hi) <= now:  # 完了した窓のみキャッシュ（未完了の当日部分は都度計算）。
        _PARTIAL_CACHE[key] = roll
    return roll


# --------------------------------------------------------------------------- #
# 公開 API: 窓合算 → 表示 bin 再集計
# --------------------------------------------------------------------------- #
def compute_dwell_profile(
    symbol: str,
    t0: Any,
    t1: Any,
    price_min: Any,
    price_max: Any,
    n_bins: int,
    va_pct: float = 0.70,
    bar_sec: int = 86400,
    now: float | None = None,
) -> dict:
    """実ティック滞在（セッション認識）プロファイルを計算する（candle 版と同一スキーマ）。

    実期間 ``[t0, t1+bar_sec)`` を日単位に走査する。完全日は :func:`_day_rollup`（キャッシュ）、境界日は
    :func:`_partial_rollup` で固定グリッド dwell を得て ``fine[]`` に加算し、固定グリッド中心を表示 bin へ
    再集計して tpo[]（=dwell 秒）を得る。POC/VA は :func:`market_profile._value_area` を再利用する。

    perf 上限（必須）: 走査開始を ``win_to - _MAX_DWELL_DAYS*86400`` 以降へ丸め、要求窓がそれ以上でも
    直近 ``_MAX_DWELL_DAYS`` 日ぶんのサブ窓に限定する（初回集計の日数比例ブロックを防ぐ）。
    """
    now_val = _time.time() if now is None else float(now)  # Y2a: 当日判定の基準時刻（既定は現在時刻）。
    price_min = float(price_min)
    price_max = float(price_max)
    if price_max <= price_min:  # レンジ縮退はゼロ割回避のため +1。
        price_max = price_min + 1.0
    n_bins = max(1, int(n_bins))

    edges = np.linspace(price_min, price_max, n_bins + 1)
    centers = (edges[:-1] + edges[1:]) / 2.0
    binw = (price_max - price_min) / n_bins

    win_to = int(t1) + int(bar_sec)
    win_from = int(t0)
    cap_from = win_to - _MAX_DWELL_DAYS * 86400
    if win_from < cap_from:  # perf 上限: 直近 _MAX_DWELL_DAYS 日ぶんへ限定。
        win_from = cap_from

    # active table は直近 _ACTIVE_TABLE_DAYS 日から構築する（要求窓の狭さに依存しない）。
    #   キャッシュはプロセス内 symbol 単位で 1 回。win_from で下限を切ると、初回要求が数日窓の場合に
    #   一部曜日が未カバーとなり「その曜日は全休場扱い→dwell=0」の欠陥マスクが恒久キャッシュされる。
    #   固定の直近スパンにすれば全曜日を確実にカバーする（試作の「直近120日」と一致）。
    at_from = win_to - _ACTIVE_TABLE_DAYS * 86400
    table = _active_table(symbol, at_from, win_to)

    kw0 = int(np.floor(price_min / GRID_W))
    size = int(np.floor(price_max / GRID_W)) - kw0 + 1
    fine = np.zeros(max(size, 1), dtype=float)

    day = (win_from // 86400) * 86400
    while day < win_to:
        lo_t = max(day, win_from)
        hi_t = min(day + 86400, win_to)
        if lo_t < hi_t:
            if lo_t == day and hi_t == day + 86400:
                roll = _day_rollup(symbol, day, table, now_val)          # 完全日=完了日のみキャッシュ。
            else:
                roll = _partial_rollup(symbol, lo_t, hi_t, table, now_val)  # 境界日=完了窓のみキャッシュ。
            if roll is not None:
                arr = roll["dwell"]
                off = roll["kmin"] - kw0
                lo = max(0, off)
                hi = min(size, off + len(arr))
                if hi > lo:
                    fine[lo:hi] += arr[(lo - off):(hi - off)]
        day += 86400

    # 固定グリッド(fine) → 表示 bin へ再集計。
    centers_fine = (kw0 + np.arange(size) + 0.5) * GRID_W
    disp = np.clip(((centers_fine - price_min) / binw).astype(int), 0, n_bins - 1)
    tpo = np.zeros(n_bins, dtype=float)
    np.add.at(tpo, disp, fine[:size])

    tmax = float(tpo.max()) if tpo.max() > 0 else 1.0
    poc = float(centers[int(tpo.argmax())])
    va_low, va_high = _value_area(tpo, centers, va_pct)

    bins = [
        {
            "price": round(float(centers[i]), 2),
            "tpo": int(round(float(tpo[i]))),
            "norm": round(float(tpo[i]) / tmax, 4),
        }
        for i in range(n_bins)
    ]
    return {
        "bins": bins,
        "poc": round(poc, 2),
        "va_low": round(float(va_low), 2),
        "va_high": round(float(va_high), 2),
        "price_min": price_min,
        "price_max": price_max,
        "tpo_units": int(round(float(fine.sum()))),  # 総 dwell 秒（int へ丸め）。
        "n_bins": n_bins,
    }
