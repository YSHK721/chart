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

from typing import Any

import numpy as np

from adapter.compute import forming_bar as _forming_bar
from market_profile_api.compute import market_profile_dwell as _mpd
from market_profile_api.compute.tf_period_profile import tf_period_profiles
from market_profile_api.controller.market_profile_controller import _error_body

# 対応 tf（固定周期＝floor 可能）→ 周期秒。1W/1M（カレンダー）は floor 不可で非対応。
_TF_SECONDS = {"1m": 60, "5m": 300, "15m": 900, "30m": 1800, "1h": 3600, "4h": 14400, "1D": 86400}


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
    ref: Any, timeframe: Any, frm: Any, to: Any
) -> "tuple[int, dict]":
    """ローリング窓 ``[frm, to)`` の tf-period 最小単位プロファイル列を返す（読取のみ）。"""
    if not _forming_bar.is_tick_ref(ref):
        return _error_body("validation", f"tick 由来 datasetRef ではありません: {ref!r}")
    if not _forming_bar.is_supported_timeframe(timeframe):
        return _error_body("validation", f"非対応の timeframe です（1W/1M は floor 不可）: {timeframe!r}")
    tf_sec = _TF_SECONDS.get(timeframe)
    if tf_sec is None:
        return _error_body("validation", f"周期秒を解決できない timeframe です: {timeframe!r}")
    from_i, to_i = _parse_int(frm), _parse_int(to)
    if from_i is None or to_i is None or from_i >= to_i:
        return _error_body("validation", f"不正なローリング窓です [from,to)=({frm!r},{to!r})")

    symbol = _mpd.resolve_symbol(ref)
    secs, mids = _mpd._load_window_ticks(symbol, from_i, to_i)
    unit = _min_unit(np.asarray(mids, dtype=float))
    columns = tf_period_profiles(secs, mids, tf_sec, unit, from_i, to_i)
    return 200, {
        "ok": True,
        "tf": timeframe,
        "unit": round(unit, 6),
        "from": from_i,
        "to": to_i,
        "columns": columns,
    }
