#!/usr/bin/env python3
"""Market Profile コア（TPO 時間累積の計算）  prototype_260630-01

提供:
  - load_candles(tf, n)            : 表示用ローソク（data.json）
  - compute_profile(...)           : TPO 集計・POC・バリューエリア・per-session(積み上げ用)

時間単位(src):
  1D/4h … data.json の足。1本=1単位、[low,high]が跨ぐ各ビンに +1。
  m1/tick … 実ティック(per-day parquet, window_ticks)。1ティック=1単位＝真の time-at-price
            高分解能（B フェーズ・メモリ有界）。点なので np.histogram でベクトル集計。
per-session（C フェーズ・積み上げ）:
  各カレンダー日を 1 セッションとし、その日が触れたビン範囲[lo,hi]を返す（日次プロファイル積み上げ用）。
読み取り専用。既存データ・本番コードは変更しない。
"""
import os
import sys
import json
import importlib.util
import datetime as dt
import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(HERE, "..", "prototype_260626-01", "data.json")
_TW_PATH = os.path.join(HERE, "..", "prototype_260626-01", "contact_scan", "tick_window.py")

_DATA = None
_WT = None   # window_ticks 関数

# dwell（セッション認識方式）:
#   休場帯をデータから自動抽出し、各ギャップの「アクティブ時間に重なる秒数だけ」を滞在に計上する。
#   （週末・日次メンテ休止を直接除外しつつ、取引中の静かな滞在は満額残す＝17秒問題も解消）
ACTIVE_FRAC = 0.10  # (曜日×時)バケットのティック数が ピーク×この割合 未満なら「休場」とみなす


def _build_active_table(secs):
    """窓内ティックから (曜日0-6 × 時0-23) の活動テーブルを作り、活発/休場(bool)を返す。"""
    s = secs.astype(np.int64)
    wd = ((s // 86400) + 3) % 7          # 1970-01-01=木(=3 とした Mon0 基準)
    hod = (s % 86400) // 3600
    cnt = np.zeros((7, 24), dtype=np.int64)
    np.add.at(cnt, (wd, hod), 1)
    thr = cnt.max() * ACTIVE_FRAC
    return cnt >= thr                    # True=活発 / False=休場


def _active_seconds_cross(a, b, table):
    """[a,b) のうち活発(曜日×時)に属する秒数を時間境界で積分（境界跨ぎギャップ用）。"""
    s = 0; t = int(a); b = int(b)
    while t < b:
        nb = (t // 3600 + 1) * 3600
        seg = min(nb, b)
        wd = ((t // 86400) + 3) % 7
        if table[wd, (t % 86400) // 3600]:
            s += seg - t
        t = seg
    return s


def _session_dwell(secs, table=None):
    """各ギャップの「アクティブ秒」を返す（len=len(secs)-1）。休場帯は0、跨ぎは厳密積分。
    table 未指定時は secs から生成（後方互換）。日別ロールアップでは共通テーブルを渡す。"""
    if table is None:
        table = _build_active_table(secs)
    start = secs[:-1].astype(np.int64); end = secs[1:].astype(np.int64)
    gap = (end - start).astype(float)
    wd = ((start // 86400) + 3) % 7
    hod = (start % 86400) // 3600
    act_start = table[wd, hod]
    same_hour = (start // 3600) == (end // 3600)
    # 同一時内: 活発なら gap、休場なら 0
    dwell = np.where(same_hour & act_start, gap, 0.0)
    dwell = np.where(same_hour & ~act_start, 0.0, dwell)
    # 時間境界を跨ぐギャップのみ厳密に積分（件数僅少）
    for i in np.where(~same_hour)[0]:
        dwell[i] = _active_seconds_cross(start[i], end[i], table)
    return dwell, table


# ============ AB兼用: 固定グリッドの日別ロールアップ + メモリキャッシュ ============
GRID_W = 10.0               # 固定価格グリッド幅(pt)。日別集計→窓合算→表示binへ再集計
_DAY_CACHE = {}             # day_start(UNIX秒, UTC真夜中) -> rollup or None
_ACTIVE_TABLE = None


def _active_table():
    """休場マスク(7曜日×24時)を一度だけ構築してキャッシュ（直近120日から）。"""
    global _ACTIVE_TABLE
    if _ACTIVE_TABLE is None:
        d = _data()["timeframes"]["1D"]["candles"][-120:]
        s, _, _ = _window_ticks_full(int(d[0]["time"]), int(d[-1]["time"]) + 1)
        _ACTIVE_TABLE = _build_active_table(s) if len(s) else np.ones((7, 24), bool)
    return _ACTIVE_TABLE


def _day_rollup(day_start):
    """1カレンダー日(UTC)を固定グリッドに集計してキャッシュ。
    返り値 {kmin, dwell[], cnt[], vol[]}（k=floor(price/GRID_W)）or None(ティック無)。"""
    if day_start in _DAY_CACHE:
        return _DAY_CACHE[day_start]
    secs, mids, vols = _window_ticks_full(day_start, day_start + 86400)
    roll = _rollup_ticks(secs, mids, vols)
    _DAY_CACHE[day_start] = roll
    return roll


_PARTIAL_CACHE = {}


def _partial_rollup(lo, hi):
    """[lo,hi) の部分期間ロールアップ（境界日=サブ日足でのT前後）。(lo,hi)でキャッシュ。"""
    key = (int(lo), int(hi))
    if key in _PARTIAL_CACHE:
        return _PARTIAL_CACHE[key]
    secs, mids, vols = _window_ticks_full(int(lo), int(hi))
    roll = _rollup_ticks(secs, mids, vols)
    _PARTIAL_CACHE[key] = roll
    return roll


def _rollup_ticks(secs, mids, vols):
    """ティック配列 → 固定グリッド集計 {kmin, dwell[], cnt[], vol[]}。空なら None。"""
    if len(secs) == 0:
        return None
    dwell, _ = _session_dwell(secs, _active_table())
    k = np.floor(mids / GRID_W).astype(np.int64)
    kmin = int(k.min()); size = int(k.max()) - kmin + 1
    dwell_arr = np.zeros(size); np.add.at(dwell_arr, k[:-1] - kmin, dwell)
    cnt_arr = np.zeros(size); np.add.at(cnt_arr, k - kmin, 1.0)
    vol_arr = np.zeros(size); np.add.at(vol_arr, k - kmin, vols)
    return {"kmin": kmin, "dwell": dwell_arr, "cnt": cnt_arr, "vol": vol_arr}


def _data():
    global _DATA
    if _DATA is None:
        _DATA = json.load(open(DATA))
    return _DATA


def _window_ticks():
    """既存 contact_scan/tick_window.window_ticks を単体ロード（per-day parquet・メモリ有界）。"""
    global _WT
    if _WT is None:
        spec = importlib.util.spec_from_file_location("mp_tick_window", _TW_PATH)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        _WT = mod.window_ticks
    return _WT


_TICK_ROOT = os.path.join(HERE, "..", "data", "marketdata", "ticks")


def _window_ticks_full(start, end):
    """[start,end) の実ティックを (secs, mids, vols) で返す（出来高 bidVol+askVol 付き）。
    per-day parquet をメモリ有界に走査。価格の外れ値除去は tick_window と同基準(±30%)。
    """
    import pandas as pd
    from datetime import datetime, timezone, timedelta
    frames = []
    d0 = datetime.fromtimestamp(start, tz=timezone.utc).date()
    d1 = datetime.fromtimestamp(max(start, end - 1), tz=timezone.utc).date()
    day = d0
    while day <= d1:
        p = os.path.join(_TICK_ROOT, f"{day.year:04d}", f"{day.month:02d}",
                         f"{day.day:02d}", "JP225_ticks.parquet")
        if os.path.isfile(p):
            frames.append(pd.read_parquet(
                p, columns=["timestamp", "bidPrice", "askPrice", "bidVolume", "askVolume"]))
        day += timedelta(days=1)
    empty = (np.array([]), np.array([]), np.array([]))
    if not frames:
        return empty
    tdf = pd.concat(frames, ignore_index=True) if len(frames) > 1 else frames[0]
    secs = tdf["timestamp"].dt.tz_localize(None).to_numpy().astype("datetime64[s]").astype("int64")
    win = (secs >= start) & (secs < end)
    tdf = tdf[win]; secs = secs[win]
    mids = ((tdf["bidPrice"] + tdf["askPrice"]) / 2.0).to_numpy()
    vols = (tdf["bidVolume"] + tdf["askVolume"]).to_numpy()
    m = float(np.median(mids)) if len(mids) else 0.0
    if m > 0:                                   # 窓内mid中央値±30%の外れ値除去
        keep = np.abs(mids / m - 1.0) <= 0.3
        secs, mids, vols = secs[keep], mids[keep], vols[keep]
    o = np.argsort(secs, kind="stable")         # 時系列順
    return secs[o], mids[o], vols[o]


def load_candles(tf, n):
    c = _data()["timeframes"][tf]["candles"]
    return c[-n:] if n else c


def compute_profile(t0, t1, price_min, price_max, n_bins, src="1D",
                    va_pct=0.70, want_sessions=False, want_today=False, bar_sec=86400):
    edges = np.linspace(price_min, price_max, n_bins + 1)
    centers = (edges[:-1] + edges[1:]) / 2
    sessions = {}
    today_tpo = np.zeros(n_bins, dtype=float)   # 窓の最終日ぶん（別カラー表示用）

    if src in ("1D", "4h"):
        bars = [(b["time"], b["low"], b["high"])
                for b in _data()["timeframes"][src]["candles"] if t0 <= b["time"] <= t1]
        tpo = np.zeros(n_bins, dtype=float)
        for sec, lo, hi in bars:                    # 足は数百本＝Pythonループで十分
            i0 = max(0, int(np.searchsorted(edges, lo, side="right")) - 1)
            i1 = min(n_bins - 1, int(np.searchsorted(edges, hi, side="right")) - 1)
            if i1 < i0:
                i1 = i0
            tpo[i0:i1 + 1] += 1.0
            if want_sessions:
                day = dt.datetime.fromtimestamp(int(sec), dt.timezone.utc).strftime("%Y-%m-%d")
                a = sessions.get(day)
                if a is None:
                    a = np.zeros(n_bins); sessions[day] = a
                a[i0:max(i0, i1) + 1] += 1.0        # その日のプロファイル(表示bin)に加算
        units = len(bars)
        if want_today and bars:                 # 最終足ぶん
            _, lo, hi = bars[-1]
            i0 = max(0, int(np.searchsorted(edges, lo, side="right")) - 1)
            i1 = min(n_bins - 1, int(np.searchsorted(edges, hi, side="right")) - 1)
            today_tpo[i0:max(i0, i1) + 1] += 1.0
    else:  # dwell/vol/m1,tick … 日別ロールアップ(固定グリッド)をキャッシュ→窓合算→表示binへ再集計
        key = {"dwell": "dwell", "vol": "vol"}.get(src, "cnt")   # m1/tick は cnt
        kw0 = int(np.floor(price_min / GRID_W))
        size = int(np.floor(price_max / GRID_W)) - kw0 + 1
        binw = (price_max - price_min) / n_bins
        fine = np.zeros(max(size, 1))
        last_roll = None
        # 実期間 [t0, 最終足の終端)。サブ日足(4h等)ではTの当日を丸ごと含めない（未来リーク防止）
        win_from = int(t0); win_to = int(t1) + int(bar_sec)
        day = (win_from // 86400) * 86400
        while day < win_to:
            lo_t = max(day, win_from); hi_t = min(day + 86400, win_to)
            if lo_t < hi_t:
                roll = (_day_rollup(day) if (lo_t == day and hi_t == day + 86400)
                        else _partial_rollup(lo_t, hi_t))   # 完全日=キャッシュ / 境界=部分計算
                if roll is not None:
                    last_roll = roll
                    arr = roll[key]; off = roll["kmin"] - kw0
                    lo = max(0, off); hi = min(size, off + len(arr))
                    if hi > lo:
                        fine[lo:hi] += arr[(lo - off):(hi - off)]
                    if want_sessions:
                        # その日の roll[key] を表示binへ再集計＝日別プロファイルの形
                        cd = (roll["kmin"] + np.arange(len(arr)) + 0.5) * GRID_W
                        dd = np.clip(((cd - price_min) / binw).astype(int), 0, n_bins - 1)
                        da = np.zeros(n_bins); np.add.at(da, dd, arr)
                        ds = dt.datetime.fromtimestamp(day, dt.timezone.utc).strftime("%Y-%m-%d")
                        sessions[ds] = sessions.get(ds, 0) + da   # 同日(境界分割)も合算
            day += 86400
        # 固定グリッド(fine)→表示bin へ再集計
        centers_fine = (kw0 + np.arange(size) + 0.5) * GRID_W
        disp = np.clip(((centers_fine - price_min) / binw).astype(int), 0, n_bins - 1)
        tpo = np.zeros(n_bins, dtype=float)
        np.add.at(tpo, disp, fine[:size])
        units = int(round(float(fine.sum())))
        if want_today and last_roll is not None:   # 最終日ぶんを別集計
            arr = last_roll[key]; off = last_roll["kmin"] - kw0
            ft = np.zeros(max(size, 1))
            lo = max(0, off); hi = min(size, off + len(arr))
            if hi > lo:
                ft[lo:hi] += arr[(lo - off):(hi - off)]
            np.add.at(today_tpo, disp, ft[:size])

    poc_i, poc, val, vah = _value_area(centers, tpo, va_pct)
    tmax = float(tpo.max()) if tpo.max() > 0 else 1.0
    atom = {"dwell": "tick滞在秒", "vol": "出来高(bid+ask)", "m1": "tick数",
            "tick": "tick数"}.get(src, f"{src}足レンジ")
    out = {
        "src": src, "atom": atom, "tpo_units": int(units),
        "price_min": float(price_min), "price_max": float(price_max),
        "bin_h": float(edges[1] - edges[0]), "n_bins": n_bins,
        "poc": poc, "va_low": val, "va_high": vah, "tpo_max": tmax,
        "bins": [{"price": round(float(c), 2), "tpo": int(v), "norm": round(float(v) / tmax, 4)}
                 for c, v in zip(centers, tpo)],
    }
    if want_sessions:
        out["sessions"] = [{"date": d, "tpo": [round(float(v), 2) for v in a]}
                           for d, a in sorted(sessions.items())]
    if want_today:
        tmax_t = float(today_tpo.max()) if today_tpo.max() > 0 else 1.0
        out["today_max"] = tmax_t
        out["today"] = [round(float(v), 3) for v in today_tpo]
    return out


def _value_area(centers, tpo, va_pct):
    total = tpo.sum()
    if total <= 0:
        return 0, float(centers[0]), float(centers[0]), float(centers[-1])
    poc = int(np.argmax(tpo))
    order = np.argsort(tpo)[::-1]
    acc = 0.0; chosen = []
    for idx in order:
        chosen.append(idx); acc += tpo[idx]
        if acc >= total * va_pct:
            break
    chosen = np.array(chosen)
    return poc, float(centers[poc]), float(centers[chosen].min()), float(centers[chosen].max())
