"""tf_period_columns — tf-period 列の集計エンジン（compute 層・ISSUE-094 🔴-2 item3）。

``tf_period_profile_controller`` が controller 名目で抱えていた「集計エンジン」——z 統計の直計算・
ライブ末尾合成・バケット（1W/1M）合成——を compute 層へ移送する。controller は「HTTP パラメータ検証
＋窓走査＋compute 委譲＋応答整形」と per-day キャッシュ・オーケストレーション（LRU/ディスク・
``_TFP_CACHE_ROOT`` の monkeypatch アンカー）へ縮退する。

本モジュールの関数は **キャッシュ非依存の純集計** である（メモリ LRU・ディスク JSON I/O・
``_TFP_CACHE_ROOT`` には触れない）。controller 側の薄いキャッシュ・ラッパー
（``_day_columns_zp`` / ``_bucket_columns`` / ``_bucket_columns_zp``）が「完了判定→メモ/ディスク照合→
本 compute コア呼び出し→保存」を担い、本コアは「その日/バケットの列を計算する」責務だけを持つ。

依存: numpy＋:mod:`market_profile_zp`（帰無/観測/z/POC*）・:mod:`market_profile_dwell`（tick ローダ・
GRID_W・外れ値規約）・:func:`market_profile._value_area`・:mod:`tf_period_profile`（count 列 VA）・
:mod:`marketdata.session_day`（セッション日規則）。tick 読込・zp 統計パラメータは各モジュールの
単一定義を call-time 参照する（テストの monkeypatch を尊重）。
"""

from __future__ import annotations

from typing import Any

import numpy as np

from market_profile_api.compute import market_profile_dwell as _mpd
from market_profile_api.compute import market_profile_zp as _zp
# ISSUE-178: 層間 DTO（不変）。日別 z ロールアップは frozen dataclass で受け渡す。
from market_profile_api.compute.rollup_dto import ZpRollup
from market_profile_api.compute.market_profile import _value_area
from market_profile_api.compute.tf_period_profile import _value_area_sparse
from marketdata.session_day import (
    next_session_day_start,
    period_session_labels,
    session_label_to_start,
)

_DAY = 86400  # 1 カレンダー日（秒）。バケット合成の 1D 列取得単位。


def merge_live_tail(
    secs: np.ndarray, mids: np.ndarray, live_ticks: "list | None", lo: int, hi: int
) -> "tuple[np.ndarray, np.ndarray]":
    """parquet 窓ティックへ live buffer 末尾 ``[(unix_ms, mid)...]`` を合成する（ISSUE-083 追補）。

    採用条件: 窓 ``[lo, hi)`` 内・parquet 末尾秒より**後**（parquet 優先 dedup＝
    :func:`forming_bar.augment_forming_ticks` と同規約）・合成中央値±30%（
    :func:`market_profile_dwell._load_window_ticks` の外れ値規約と同一）。空/採用ゼロは入力不変。
    """
    if not live_ticks:
        return secs, mids
    pmax = int(secs[-1]) if len(secs) else int(lo) - 1
    add_s: "list[int]" = []
    add_m: "list[float]" = []
    for tk in live_ticks:
        sec = int(tk[0] // 1000)
        if lo <= sec < hi and sec > pmax:
            add_s.append(sec)
            add_m.append(float(tk[1]))
    if not add_s:
        return secs, mids
    med = float(np.median(np.concatenate([mids, np.asarray(add_m)]))) if (len(mids) + len(add_m)) else 0.0
    if med > 0:
        keep = [abs(v / med - 1.0) <= _mpd._OUTLIER_FRAC for v in add_m]
        add_s = [s for s, k in zip(add_s, keep) if k]
        add_m = [v for v, k in zip(add_m, keep) if k]
    if not add_s:
        return secs, mids
    out_s = np.concatenate([secs, np.asarray(add_s, dtype=np.int64)])
    out_m = np.concatenate([mids, np.asarray(add_m, dtype=np.float64)])
    order = np.argsort(out_s, kind="stable")
    return out_s[order].astype(np.int64), out_m[order].astype(np.float64)


def live_zp_day_roll(
    symbol: Any, day_start: int, now_val: float, live_ticks: "list | None"
) -> "ZpRollup | None":
    """当日（未完了セッション）の zp 日次ロールアップを live buffer 合成グリッドで都度計算する。

    :func:`market_profile_zp._zp_day_rollup` の未完了分岐と同一規約（elapsed cap・M_REPS_LIVE・
    day_seed）で、分足格子のみ live 末尾を合成した最新版にする（ISSUE-083 追補と同じ鮮度化）。
    live_ticks 空は _zp_day_rollup へ委譲（挙動同一・メモ化も既存に委ねる）。
    """
    if not live_ticks:
        return _zp._zp_day_rollup(symbol, int(day_start), now_val)
    day_start = int(day_start)
    day_end = next_session_day_start(day_start)
    secs, mids = _mpd._load_window_ticks(symbol, day_start, day_end)
    secs, mids = merge_live_tail(secs, mids, live_ticks, day_start, day_end)
    grid = _zp.minute_close_grid(secs, mids, day_start)
    if grid is None:
        return None
    closes, open_d = grid
    col_hi = _zp.asof_col_hi(now_val, day_start)  # ISSUE-179: as-of 経過分クランプの単一情報源。
    obs_closes = closes[:col_hi]
    klo = int(np.floor(np.log(float(obs_closes.min())) / _zp.W_LOG))
    khi = int(np.floor(np.log(float(obs_closes.max())) / _zp.W_LOG))
    S = _zp._hist_step_matrix(symbol, day_start, now_val)
    if S is None:
        return None
    rng = np.random.default_rng(_zp.day_seed(str(symbol), day_start))
    mean, var = _zp.null_b_moments_abs(
        S, open_d, klo, khi, rng=rng, m_reps=_zp.M_REPS_LIVE, col_hi=col_hi
    )
    obs = _zp.obs_cell_counts(closes, klo, khi, col_hi=col_hi)
    return ZpRollup(kmin=klo, obs=obs, mean=mean, var=var)


def day_columns_zp_compute(
    symbol: Any, tf_sec: int, day_start: int, day_end: int, completed: bool,
    now_val: float, live_ticks: "list | None", *, va_pct: float,
) -> "tuple[float, list]":
    """src=zp の 1 カレンダー日 tf-period 列 ``(unit, columns)`` を計算する（キャッシュ非依存の純集計）。

    周期解像度は 1bp log 一様セル（最小価格単位では帰無計数が退化するため）。levels の値は
    z（超過占有スコア）、levels は z>0 のセル＋POC セルのみ（sparse 維持）。帰無は
    :func:`market_profile_zp.null_b_period_moments`（1 回のサロゲート生成を周期カラム範囲で分割集計）。
    controller の :func:`_day_columns_zp` が完了判定・メモ/ディスク照合・保存でこれを包む。

    ``va_pct``（必須・ISSUE-260）: VA 比率。かつてここは 0.70 の直書きで、UI の「バリューエリア」を
    どう変えても列の VA は動かなかった。既定は :data:`market_profile.VA_PCT_DEFAULT` 唯一源が持つ。
    """
    if completed or not live_ticks:
        grid = _zp._mgrid_of_day(symbol, day_start, now_val)
    else:
        # ISSUE-083 追補: 当日のみ live buffer 末尾を合成して分足格子を最新化する（parquet フロンティア
        #   遅延中の ffill 停滞を解消）。合成後の格子構築は _mgrid_of_day の当日経路と同一規約（キャッシュ非使用）。
        secs, mids = _mpd._load_window_ticks(symbol, day_start, day_end)
        secs, mids = merge_live_tail(secs, mids, live_ticks, day_start, day_end)
        grid = _zp.minute_close_grid(secs, mids, day_start)
    cols: list = []
    if grid is not None:
        closes, open_d = grid
        S = _zp._hist_step_matrix(symbol, day_start, now_val)
        if S is not None:
            g = _zp.G_MINUTES
            if completed:
                col_cap = g
                m_reps = _zp.M_REPS_DAY
            else:
                col_cap = _zp.asof_col_hi(now_val, day_start)  # ISSUE-179: 同上（単一情報源）。
                m_reps = _zp.M_REPS_LIVE
            seg_all = closes[:col_cap]
            # ISSUE-079: zp 内部格子は 1bp log 一様（W_LOG）。セル中心価格は exp((k+0.5)·W_LOG)。
            klo = int(np.floor(np.log(float(seg_all.min())) / _zp.W_LOG))
            khi = int(np.floor(np.log(float(seg_all.max())) / _zp.W_LOG))
            centers = np.exp((klo + np.arange(khi - klo + 1) + 0.5) * _zp.W_LOG)
            mid_day = (centers[0] + centers[-1]) / 2.0
            # 周期のカラム範囲（セッション窓 index・半開）。空周期はスキップ。
            periods: "list[tuple[int, tuple[int, int]]]" = []
            # ISSUE-078: 周期グリッドは UTC floor（バー時刻整合）のまま、始端所属で本セッションへ割当。
            p = ((day_start + tf_sec - 1) // tf_sec) * tf_sec
            while p < day_end:
                lo = max(0, (p - day_start) // 60 - _zp.SESSION_OPEN_MOD)
                hi = min(col_cap, (p + tf_sec - day_start) // 60 - _zp.SESSION_OPEN_MOD)
                if hi > lo:
                    periods.append((p, (int(lo), int(hi))))
                p += tf_sec
            if periods:
                rng = np.random.default_rng(_zp.day_seed(str(symbol), day_start) ^ 0x7A7A7A7A)
                moments = _zp.null_b_period_moments(
                    S, open_d, klo, khi, [b for _, b in periods], rng=rng, m_reps=m_reps
                )
                for (p_time, (lo, hi)), (mean, var) in zip(periods, moments):
                    obs = _zp.obs_cell_counts(closes, klo, khi, col_lo=lo, col_hi=hi)
                    z = _zp._fine_z(obs, mean, var)
                    poc_price = _zp._poc_star_from_fine(z, klo, mid_day)
                    z_pos = np.maximum(z, 0.0)
                    va_low, va_high = _value_area(z_pos, centers, va_pct)
                    poc_k = int(np.floor(np.log(poc_price) / _zp.W_LOG)) - klo
                    keep = (z > 0)
                    keep[max(0, min(poc_k, keep.size - 1))] = True
                    levels = [
                        [round(float(centers[k]), 6), round(float(z[k]), 2)]
                        for k in np.flatnonzero(keep)
                    ]
                    seg = closes[lo:hi]
                    cols.append({
                        "time": int(p_time),
                        "levels": levels,
                        "poc": round(float(poc_price), 6),
                        "va_low": round(float(va_low), 6),
                        "va_high": round(float(va_high), 6),
                        "price_min": round(float(seg.min()), 6),
                        "price_max": round(float(seg.max()), 6),
                        "tpo_units": int(obs.sum()),
                    })
    # 応答 unit＝レンジ中央での 1 セル価格幅（bp 格子は価格比例のため代表値）。空日は W_LOG×基準価格の名目値。
    if cols:
        mids_p = [(c["price_min"] + c["price_max"]) / 2.0 for c in cols]
        unit = round(float(np.median(mids_p)) * (np.exp(_zp.W_LOG) - 1.0), 6)
    else:
        unit = round(60000.0 * (np.exp(_zp.W_LOG) - 1.0), 6)
    return unit, cols


def bucket_columns_compute(
    symbol: Any, tf: Any, label: str, bar_time: int, now_val: float,
    live_ticks: "list | None", *, va_pct: float, day_columns_fn,
) -> "tuple[float, list]":
    """1W/1M バケットの count 列 ``(unit=GRID_W, columns)`` を計算する（ISSUE-086・キャッシュ非依存）。

    セッション日次の 1D 列（``day_columns_fn``＝controller の完了日キャッシュ経路 :func:`_day_columns`
    を DIP 注入）をバケットの全セッション日で加算合成する。levels は同一 GRID_W 格子ゆえ価格キーで
    加算でき、poc/va は合成カウントから再計算（:func:`_value_area_sparse`＝count 列と同一 VA 規約）。
    controller の :func:`_bucket_columns` が完了判定・メモ/ディスク照合・保存でこれを包む。

    ``va_pct``（必須・ISSUE-260）: VA 比率。日次 1D 列の取得（``day_columns_fn``）にも同じ値を渡す
    （同一要求の中で日次とバケットが別比率で集計される状態を作らない）。
    """
    unit = float(_mpd.GRID_W)
    merged: "dict[float, float]" = {}
    pmin: "float | None" = None
    pmax: "float | None" = None
    tpo = 0
    for lab in period_session_labels(tf, label):
        day = session_label_to_start(lab)
        if day >= now_val:
            break  # 未来セッション（未開始）。
        _u, cols_d = day_columns_fn(
            symbol, "1D", _DAY, day, now_val, va_pct=va_pct, live_ticks=live_ticks
        )
        for c in cols_d:
            for price, cnt in c["levels"]:
                merged[price] = merged.get(price, 0) + cnt
            pmin = c["price_min"] if pmin is None else min(pmin, c["price_min"])
            pmax = c["price_max"] if pmax is None else max(pmax, c["price_max"])
            tpo += int(c["tpo_units"])
    if not merged:
        return unit, []
    prices = sorted(merged)
    counts = np.asarray([merged[p] for p in prices])
    poc_i = int(np.argmax(counts))  # 最大カウント（同値は低価格側＝count 列と同規約）。
    lo, hi = _value_area_sparse(counts, poc_i, va_pct)
    col = {
        "time": bar_time,
        "levels": [[float(p), int(merged[p])] for p in prices],
        "poc": float(prices[poc_i]),
        "va_low": float(prices[lo]),
        "va_high": float(prices[hi]),
        "price_min": float(pmin),
        "price_max": float(pmax),
        "tpo_units": int(tpo),
    }
    return unit, [col]


def bucket_columns_zp_compute(
    symbol: Any, tf: Any, label: str, bar_time: int, now_val: float,
    live_ticks: "list | None", *, va_pct: float,
) -> "tuple[float, list]":
    """1W/1M バケットの zp 列 ``(unit, columns)`` を計算する（ISSUE-086・キャッシュ非依存）。

    z は加算不可のため、セッション日次の :class:`ZpRollup`（:func:`market_profile_zp._zp_day_rollup`＝
    znull キャッシュ再利用・独立日ゆえモーメント加算可）を k 空間（絶対 log 格子＝日間で整列）で合成し、
    z = (Σobs − Σmean)/√Σvar を再計算する（compute_zp_profile の窓合成と同一規約）。当日を含むセッションは
    :func:`live_zp_day_roll` で都度計算。controller の :func:`_bucket_columns_zp` が完了判定・保存で包む。
    """
    rolls: "list[ZpRollup]" = []
    for lab in period_session_labels(tf, label):
        day = session_label_to_start(lab)
        if day >= now_val:
            break
        if next_session_day_start(day) <= now_val:
            roll = _zp._zp_day_rollup(symbol, day, now_val)
        else:
            roll = live_zp_day_roll(symbol, day, now_val, live_ticks)
        if roll is not None:
            rolls.append(roll)
    cols: list = []
    if rolls:
        klo = min(r.kmin for r in rolls)
        khi = max(r.kmin + len(r.obs) - 1 for r in rolls)
        size = khi - klo + 1
        obs_sum = np.zeros(size)
        mean_sum = np.zeros(size)
        var_sum = np.zeros(size)
        for r in rolls:
            # ISSUE-178: 書込先 obs_sum/mean_sum/var_sum は本関数所有の可変配列。DTO 配列（r.obs 等）は
            #   右辺＝読み取りのみのため read-only 化と両立する（in-place 更新は行わない）。
            off = r.kmin - klo
            obs_sum[off:off + len(r.obs)] += r.obs
            mean_sum[off:off + len(r.mean)] += r.mean
            var_sum[off:off + len(r.var)] += r.var
        centers = np.exp((klo + np.arange(size) + 0.5) * _zp.W_LOG)
        z = _zp._fine_z(obs_sum, mean_sum, var_sum)
        mid = (centers[0] + centers[-1]) / 2.0
        poc_price = _zp._poc_star_from_fine(z, klo, mid)
        z_pos = np.maximum(z, 0.0)
        va_low, va_high = _value_area(z_pos, centers, va_pct)
        occ = np.flatnonzero(obs_sum > 0)
        p_lo = float(np.exp((klo + int(occ[0])) * _zp.W_LOG)) if occ.size else float(centers[0])
        p_hi = float(np.exp((klo + int(occ[-1]) + 1) * _zp.W_LOG)) if occ.size else float(centers[-1])
        poc_k = int(np.floor(np.log(poc_price) / _zp.W_LOG)) - klo
        keep = (z > 0)
        keep[max(0, min(poc_k, keep.size - 1))] = True
        levels = [
            [round(float(centers[k]), 6), round(float(z[k]), 2)]
            for k in np.flatnonzero(keep)
        ]
        cols.append({
            "time": bar_time,
            "levels": levels,
            "poc": round(float(poc_price), 6),
            "va_low": round(float(va_low), 6),
            "va_high": round(float(va_high), 6),
            "price_min": round(p_lo, 6),
            "price_max": round(p_hi, 6),
            "tpo_units": int(obs_sum.sum()),
        })
    if cols:
        mids_p = [(c["price_min"] + c["price_max"]) / 2.0 for c in cols]
        unit = round(float(np.median(mids_p)) * (np.exp(_zp.W_LOG) - 1.0), 6)
    else:
        unit = round(60000.0 * (np.exp(_zp.W_LOG) - 1.0), 6)
    return unit, cols
