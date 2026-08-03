"""/live_ticks の末尾値同梱（ISSUE-250 Phase 1）— 薄殻。

責務: クエリ検証 → 窓ロード → 純ロジック（:mod:`usecase.serve_live_tick_tails`）と計算
アダプタ（:mod:`adapter.compute.live_tick_tails`）の結線のみ。計算規則は持たない。

申告が無い／不正／材料不足のときは ``None`` を返し、``/live_ticks`` は従来応答のままにする
（後方互換・byte 不変）。
"""

from __future__ import annotations

import json
from typing import Any

from adapter.compute.indicator_compute_adapter import IndicatorComputeAdapter
from adapter.compute.latest_dispatch import latest_compute
from adapter.compute.live_tick_tails import make_tail_at
from usecase.dataset_port import dataset_port as _dataset_port
from usecase.serve_live_tick_tails import forming_states, parse_specs, tails_for_ticks

#: tf 文字列 → 秒（フロントの TF_BAR_SEC と同じ固定周期のみ対象。1W/1M は非対象）。
_TF_SEC = {"1m": 60, "5m": 300, "15m": 900, "30m": 1800, "1h": 3600, "4h": 14400, "1D": 86400}


def _set_last_bar(window: Any, values: "dict[str, float]") -> None:
    """窓の末尾行だけを形成中バーで上書きする（pandas 依存はここに閉じる）。"""
    last = len(window) - 1
    for key, val in values.items():
        if key in window.columns:
            window.iat[last, window.columns.get_loc(key)] = val


def handle_live_tick_tails(
    query: "dict[str, list[str]]", ticks: "list", *, adapter: Any = None
) -> "list[dict] | None":
    """``/live_ticks`` へ同梱する ``tails`` を組む。組めないときは ``None``。"""
    raw = (query.get("specs") or [None])[0]
    ref = (query.get("datasetRef") or [None])[0]
    tf = (query.get("timeframe") or [None])[0]
    limit_raw = (query.get("limit") or [None])[0]
    if not raw or not ref or tf not in _TF_SEC or not ticks:
        return None
    try:
        specs = parse_specs(json.loads(raw))
    except (TypeError, ValueError):
        return None
    if not specs:
        return None

    port = _dataset_port()
    if not port.is_known(ref) or not port.is_known_timeframe(tf):
        return None
    try:
        df = port.load_dataframe(ref, tf)
    except Exception:
        return None
    if df is None or len(df) == 0:
        return None
    # 表示範囲（直近 N 本）は /compute と同一規約。窓を絞らないと 1 ステップが全件に比例する
    #   （実測 50,000 本で 29.4ms/tick → 1,386 本で 3.8ms/tick）。
    if limit_raw is not None and str(limit_raw).lstrip("-").isdigit():
        limit = int(limit_raw)
        if limit > 0:
            df = df.tail(limit)

    states = forming_states(ticks, _TF_SEC[tf])
    tail_at = make_tail_at(
        df=df, adapter=adapter or IndicatorComputeAdapter(),
        latest_compute=latest_compute, set_last_bar=_set_last_bar,
    )
    return tails_for_ticks(states, specs, tail_at)
