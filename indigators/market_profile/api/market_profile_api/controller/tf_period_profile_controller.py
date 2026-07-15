"""tf_period_profile_controller — GET /tf_period_profile の純ロジック（HTTP 殻非依存）。

「時間足毎のprofile列」機能の配信点。ローリング窓 ``[from, to)`` の実 tick を読み、選択 tf 周期で
分割して**最小価格単位**でビニングした sparse プロファイル列を返す（列数は窓で有界＝応答肥大を防ぐ）。

``handle_tf_period_profile(ref, timeframe, frm, to) -> (status, body)``:
  - 非 tick ref / 非対応 tf（1W/1M・floor 不可）→ 400 nested error（``_error_body`` 再利用）。
  - 不正窓（from>=to / 欠落）→ 400。
  - 正常: ``{ok, tf, unit, from, to, columns:[{time, levels, poc, va_low, va_high, ...}]}``。

依存方向: framework → 本 controller → compute（tf_period_profile / market_profile_dwell）＋ forming_bar
（ref/tf 判定）。tick 読込は :func:`market_profile_dwell._load_window_ticks`（単一注入点・read-only）を再利用。
"""
from __future__ import annotations

import json as _json
import os as _os
import time as _time
from collections import OrderedDict
from pathlib import Path as _Path
from typing import Any

import numpy as np

from adapter.compute import forming_bar as _forming_bar
from market_profile_api.compute import market_profile_dwell as _mpd
from market_profile_api.compute import market_profile_zp as _zp
from market_profile_api.compute.market_profile import _value_area
from market_profile_api.compute.tf_period_profile import tf_period_profiles
from market_profile_api.controller.market_profile_controller import _error_body

# セッション日境界（ISSUE-078・NY17:00 ET 基準）。日切り・完了判定の唯一の規則源。
from marketdata.session_day import (  # noqa: E402
    next_session_day_start,
    session_bar_time,
    session_day_start,
)

_DAY = 86400  # 1 カレンダー日（秒）。per-day キャッシュ／窓分割の単位。

# 対応 tf（固定周期＝floor 可能）→ 周期秒。1W/1M（カレンダー）は floor 不可で非対応。
_TF_SECONDS = {"1m": 60, "5m": 300, "15m": 900, "30m": 1800, "1h": 3600, "4h": 14400, "1D": _DAY}

# ISSUE-073: 時間足別ビニング解像度（依頼者承認 2026-07-13「1分足は細かく分析したい」・
#   同日 0.5pt→0.0255pt へ細分化指示）。1m は 1 周期の値幅が数〜数十 pt で GRID_W(10pt) では
#   1〜数レベルに潰れるため、データの最小価格刻み 0.0255（mid 量子化＝実 tick の真の解像度・
#   ISSUE-068 以前の min-unit 稼働実績と同値）で量子化する（sparse 応答は占有レベル数に比例＝
#   1m 窓では実用範囲。ISSUE-068 の肥大は 1h×長窓の話）。
#   未指定 tf（5m..1D）は ISSUE-068 の GRID_W(=10pt) を維持（応答肥大・メインスレッドブロック対策を保つ）。
#   ディスクキャッシュは g{unit:g} サブディレクトリで解像度別に自動分離される（混在しない）。
_UNIT_BY_TF: dict[str, float] = {"1m": 0.0255}

# src=zp の対応 tf。周期内分数が少なすぎると z が退化するため 15m 以上に限定する
#   （1m=1 分・5m=5 分では帰無/観測とも計数が退化し z の意味が立たない）。
_ZP_TF_ALLOWED = ("15m", "30m", "1h", "4h", "1D")

# ISSUE-055（B: per-day キャッシュ）: 窓 ``[from, to)`` を**カレンダー日単位**に分割し、各日を
#   ``(symbol, tf, day_start)`` でキャッシュする。過去日ティックは不変（実測:
#   .doc/TICK_IMMUTABILITY_VERIFICATION.md）＝完了日（``day_start + _DAY <= now``）の列は永続再利用でき、
#   無効化不要。窓は「完了日はキャッシュ／当日のみ再計算」で組み立てるため、直近窓も初回表示（ウォーム後）も
#   高速化する。窓端がずれても日粒度でヒットする（窓単位キャッシュより頑健）。副次効果として、同一日を
#   常に同じ日内 unit で量子化する＝ローリングで窓ごとに unit が揺れて同一日の描画が変わる現行の不整合も解消。
#   メモリはホット層（有界 LRU）、ディスクは JSON で跨プロセス永続（dwell の日別ディスクキャッシュと同方針）。
_DAY_MEM_MAX = 256  # メモリ LRU 上限（日エントリ数）。5m の 1 日は数百列で数百 KB になり得るため有界化。
_DAY_MEM: "OrderedDict[tuple, tuple]" = OrderedDict()  # (symbol, tf, day_start) -> (unit, columns)。完了日のみ。

# ディスクキャッシュ根。None=既定（DATA_DIR/cache/tf_period）。False=ディスク無効（テスト隔離用）。
#   Path/str=差替（テストは tmp を注入）。完了日のみ JSON 保存し、当日（未確定）は保存しない。
_TFP_CACHE_ROOT: "Any" = None


def _reset_tf_period_cache() -> None:
    """per-day メモリキャッシュを全消去する（テスト隔離用・ディスクは触らない）。"""
    _DAY_MEM.clear()


def _tfp_disk_root() -> "_Path | None":
    """ディスクキャッシュ基点を返す（False=無効なら None・既定は DATA_DIR/cache/tf_period）。"""
    if _TFP_CACHE_ROOT is False:
        return None
    if _TFP_CACHE_ROOT is not None:
        return _Path(_TFP_CACHE_ROOT)
    return _mpd._paths.DATA_DIR / "cache" / "tf_period"


def _day_disk_path(root: _Path, symbol: Any, tf: Any, day_start: int) -> _Path:
    """完了日 JSON の保存パス ``<root>/<symbol>/<tf>/<day_start>.json``。"""
    return root / str(symbol) / str(tf) / f"{int(day_start)}.json"


def _load_day_disk(symbol: Any, tf: Any, day_start: int) -> "tuple[float, list] | None":
    """完了日の (unit, columns) をディスクから読む。無効/未ヒット/破損は None（＝再計算へ）。"""
    root = _tfp_disk_root()
    if root is None:
        return None
    try:
        with open(_day_disk_path(root, symbol, tf, day_start)) as f:
            d = _json.load(f)
        return float(d["unit"]), d["columns"]
    except Exception:
        return None


def _save_day_disk(symbol: Any, tf: Any, day_start: int, unit: float, columns: list) -> None:
    """完了日の (unit, columns) を JSON へ原子的に保存する（無効/失敗は握りつぶす＝次回再計算）。"""
    root = _tfp_disk_root()
    if root is None:
        return
    try:
        path = _day_disk_path(root, symbol, tf, day_start)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".json.tmp")
        with open(tmp, "w") as f:
            _json.dump({"unit": unit, "columns": columns}, f)
        _os.replace(tmp, path)
    except Exception:
        pass


def _day_columns(
    symbol: Any, tf: Any, tf_sec: int, day_start: int, now_val: float
) -> "tuple[float, list]":
    """1 カレンダー日 ``[day_start, day_start+_DAY)`` の tf-period 列 ``(unit, columns)`` を返す。

    完了日（``day_start + _DAY <= now``）は **メモリ → ディスク → 計算（＋両層へ保存）** の順で解決し、
    再利用する（不変ゆえ無効化不要）。当日（未確定）はキャッシュせず毎回計算する（ティック成長のため）。
    """
    day_start = int(day_start)
    day_end = next_session_day_start(day_start)  # ISSUE-078: セッション日窓（DST 切替日は 23h/25h）。
    completed = day_end <= now_val
    key = (symbol, tf, day_start)
    # ISSUE-068: 列のビニング解像度を最小価格単位→GRID_W(=10pt) へ粗くする（依頼者承認・2026-07-12）。
    #   最小単位（≈0.0255）は 1 期間に数百レベルを生み可視1年で 37MB → parse/描画でメインスレッドが
    #   5.4s ブロックしていた。列幅~10px では最小単位は視認不能ゆえ表示損失なし。GRID_W 化で 37MB→~1-2MB。
    #   旧 min-unit ディスクキャッシュ（<root>/<sym>/<tf>/<day>.json）と混ざらないよう新 subdir へ隔離する。
    # ISSUE-073: tf 別解像度（_UNIT_BY_TF）。1m のみ最小刻み 0.0255・その他は GRID_W 維持（依頼者承認 2026-07-13）。
    unit = float(_UNIT_BY_TF.get(str(tf), _mpd.GRID_W))
    disk_tf = f"{tf}/s1/g{unit:g}"  # ISSUE-078: セッション日キー世代は s1 subdir（旧 UTC 日と不混在）。
    if completed:
        hit = _DAY_MEM.get(key)
        if hit is not None:
            _DAY_MEM.move_to_end(key)
            return hit
        disk = _load_day_disk(symbol, disk_tf, day_start)
        if disk is not None:
            _DAY_MEM[key] = disk
            _DAY_MEM.move_to_end(key)
            while len(_DAY_MEM) > _DAY_MEM_MAX:
                _DAY_MEM.popitem(last=False)
            return disk
    # ISSUE-078: 周期は「始端が本セッション日に属する」もので構成する。周期グリッドは従来どおり
    #   UTC floor（チャートのバー時刻と一致）。tf<=1h は境界（毎時 21/22:00 UTC）に整列するため従来と
    #   同形。4h はセッション境界を跨ぐ最終周期が生じるが始端所属で一意に割当（重複列を作らない）。
    #   1D はセッション日そのもの＝1 周期（time=セッション始端）。
    if tf_sec >= _DAY:
        secs, mids = _mpd._load_window_ticks(symbol, day_start, day_end)
        # 単一周期化: 秒を始端相対へシフトし全ティックを period 0 に畳む → time をセッション始端へ戻す。
        shifted = np.asarray(secs, dtype=np.int64) - day_start if len(secs) else secs
        cols = tf_period_profiles(shifted, mids, int(day_end - day_start), unit, 0, int(day_end - day_start))
        for c in cols:
            # 1D 列の time は 1D バー時刻規約（セッション日ラベルの UTC 深夜＝rollup/forming と同一）。
            c["time"] = int(session_bar_time(day_start))
    else:
        p_first = ((day_start + tf_sec - 1) // tf_sec) * tf_sec       # 始端が本セッションに属す最初の周期。
        p_last = ((day_end - 1) // tf_sec) * tf_sec                    # 始端が day_end 未満の最後の周期。
        secs, mids = _mpd._load_window_ticks(symbol, p_first, p_last + tf_sec)
        cols = tf_period_profiles(secs, mids, tf_sec, unit, p_first, day_end)
    result = (unit, cols)
    if completed:
        _DAY_MEM[key] = result
        _DAY_MEM.move_to_end(key)
        while len(_DAY_MEM) > _DAY_MEM_MAX:
            _DAY_MEM.popitem(last=False)
        _save_day_disk(symbol, disk_tf, day_start, unit, cols)  # ISSUE-068: GRID_W subdir。
    return result


def _day_columns_zp(
    symbol: Any, tf: Any, tf_sec: int, day_start: int, now_val: float
) -> "tuple[float, list]":
    """src=zp の 1 カレンダー日 tf-period 列 ``(unit=GRID_W, columns)``。

    周期解像度は GRID_W セル（最小価格単位では帰無計数が退化するため）。levels の値は
    z（超過占有スコア）、levels は z>0 のセル＋POC セルのみ（sparse 維持）。帰無は
    :func:`market_profile_zp.null_b_period_moments`（1 回のサロゲート生成を周期カラム範囲で
    分割集計）。完了日はメモリ→ディスク（``<root>/<symbol>/<tf>/zp/<day>.json``）→計算、
    当日はキャッシュせず経過分までで都度計算する（既存 _day_columns と同規約）。
    """
    day_start = int(day_start)
    day_end = next_session_day_start(day_start)  # ISSUE-078。
    completed = day_end <= now_val
    key = (symbol, tf, day_start, "zp")
    disk_tf = f"{tf}/s2/zp"  # ISSUE-079: log 格子世代 s2（s1=10pt セッション日と不混在）。
    if completed:
        hit = _DAY_MEM.get(key)
        if hit is not None:
            _DAY_MEM.move_to_end(key)
            return hit
        disk = _load_day_disk(symbol, disk_tf, day_start)
        if disk is not None:
            _DAY_MEM[key] = disk
            _DAY_MEM.move_to_end(key)
            while len(_DAY_MEM) > _DAY_MEM_MAX:
                _DAY_MEM.popitem(last=False)
            return disk

    grid = _zp._mgrid_of_day(symbol, day_start, now_val)
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
                elapsed = int((now_val - day_start) // 60) - _zp.SESSION_OPEN_MOD + 1
                col_cap = max(1, min(g, elapsed))
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
            #   4h のセッション跨ぎ最終周期は本セッション窓内の分だけで評価する（観測・帰無とも同一
            #   col 範囲＝統計的に整合。跨ぎ先はブローカー 01:00 前の休場帯が大半＝損失は僅少）。
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
                    va_low, va_high = _value_area(z_pos, centers, 0.70)
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
    # 応答 unit＝レンジ中央での 1 セル価格幅（bp 格子は価格比例のため代表値。フロントは行高・
    #   ツールチップのスナップ幅にのみ使用＝厳密幅は不要）。空日は W_LOG×基準価格の名目値。
    if cols:
        mids_p = [(c["price_min"] + c["price_max"]) / 2.0 for c in cols]
        unit = round(float(np.median(mids_p)) * (np.exp(_zp.W_LOG) - 1.0), 6)
    else:
        unit = round(60000.0 * (np.exp(_zp.W_LOG) - 1.0), 6)
    result = (unit, cols)
    if completed:
        _DAY_MEM[key] = result
        _DAY_MEM.move_to_end(key)
        while len(_DAY_MEM) > _DAY_MEM_MAX:
            _DAY_MEM.popitem(last=False)
        _save_day_disk(symbol, disk_tf, day_start, unit, cols)
    return result


def _parse_int(v: Any) -> "int | None":
    if isinstance(v, bool):
        return None
    if isinstance(v, int):
        return v
    if isinstance(v, str) and v.strip().lstrip("-").isdigit():
        return int(v.strip())
    return None


def _min_unit(mids: np.ndarray) -> float:
    """最小価格単位＝窓内 tick の最小正 mid 増分（distinct mid の最小ギャップ）。

    グリッド整合のため銘柄の最小価格刻み（JP225 mid≈0.0255）へ収束する。tick <2 種は 1.0 フォールバック。
    """
    u = np.unique(mids)
    if u.size < 2:
        return 1.0
    gaps = np.diff(u)
    gaps = gaps[gaps > 1e-9]
    return float(gaps.min()) if gaps.size else 1.0


def handle_tf_period_profile(
    ref: Any, timeframe: Any, frm: Any, to: Any, now: "float | None" = None,
    src: Any = None,
) -> "tuple[int, dict]":
    """ローリング窓 ``[frm, to)`` の tf-period プロファイル列を返す（読取のみ）。

    ``now`` は完了窓判定（``to <= now`` のみキャッシュ）の基準時刻（既定は現在時刻・テスト注入用）。
    ``src``: None（既定）＝従来の最小価格単位カウント列（応答 byte 不変）。``"zp"``＝超過占有
    スコア z(p) 列（GRID_W セル解像度・levels 値は z・対応 tf は :data:`_ZP_TF_ALLOWED` のみ）。
    その他の値は 400。
    """
    if src is not None and src != "zp":
        return _error_body("validation", f"未知の src です: {src!r}（省略|zp）")
    if not _forming_bar.is_tick_ref(ref):
        return _error_body("validation", f"tick 由来 datasetRef ではありません: {ref!r}")
    if not _forming_bar.is_supported_timeframe(timeframe):
        return _error_body("validation", f"非対応の timeframe です（1W/1M は floor 不可）: {timeframe!r}")
    if src == "zp" and timeframe not in _ZP_TF_ALLOWED:
        return _error_body(
            "validation",
            f"src=zp は tf {'|'.join(_ZP_TF_ALLOWED)} のみ対応です: {timeframe!r}",
        )
    tf_sec = _TF_SECONDS.get(timeframe)
    if tf_sec is None:
        return _error_body("validation", f"周期秒を解決できない timeframe です: {timeframe!r}")
    from_i, to_i = _parse_int(frm), _parse_int(to)
    if from_i is None or to_i is None or from_i >= to_i:
        return _error_body("validation", f"不正なローリング窓です [from,to)=({frm!r},{to!r})")

    symbol = _mpd.resolve_symbol(ref)
    now_val = _time.time() if now is None else float(now)

    # per-day 組み立て: 窓が跨るカレンダー日を走査し、各日の列（完了日=キャッシュ／当日=都度計算）を
    #   集めて窓 ``[from_i, to_i)`` に入る周期のみ採用する。列は日昇順・各日内も時刻昇順ゆえ結合で整列済み。
    #   応答 unit は寄与日の unit の最小（最も細かい解像度）を採る（描画のレベル行高の基準）。
    day_fn = _day_columns_zp if src == "zp" else _day_columns
    columns: list = []
    units: list = []
    day = session_day_start(from_i)  # ISSUE-078: セッション日ウォーク。
    while day < to_i:
        unit_d, cols_d = day_fn(symbol, timeframe, tf_sec, day, now_val)
        if cols_d:
            picked = [c for c in cols_d if from_i <= c["time"] < to_i]
            if picked:
                columns.extend(picked)
                units.append(unit_d)
        day = next_session_day_start(day)
    unit = min(units) if units else (float(_zp.GRID_W) if src == "zp" else 1.0)
    return 200, {
        "ok": True,
        "tf": timeframe,
        "unit": round(unit, 6),
        "from": from_i,
        "to": to_i,
        "columns": columns,
    }
