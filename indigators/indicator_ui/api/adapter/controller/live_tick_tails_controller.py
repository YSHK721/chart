"""/live_ticks の末尾値同梱（ISSUE-250 Phase 1）— 薄殻。

責務: クエリ検証 → 窓ロード → 形成中バーの材料（seed・端数 tick）解決 → 純ロジック
（:mod:`usecase.serve_live_tick_tails`）と計算アダプタ（:mod:`adapter.compute.live_tick_tails`）の
結線のみ。計算規則・畳み方は持たない。

申告が無い／不正／材料不足のときは ``None`` を返し、``/live_ticks`` は従来応答のままにする
（後方互換・byte 不変）。

ISSUE-251: ``/live_ticks`` が持つのは ``since`` 以降の**増分**だけであり、これをそのまま畳むと
周期の累積（open/high/low/volume）が poll ごとに消える。本殻が周期の累積を
:func:`adapter.compute.forming_bar.rollup_forming_bar`（``/forming_bar`` と同一実体）から復元し、
純ロジックへ seed として渡す。
"""

from __future__ import annotations

import json
from typing import Any

from adapter.compute import forming_bar as forming_bar_mod
from adapter.compute.indicator_compute_adapter import IndicatorComputeAdapter
from adapter.compute.latest_dispatch import latest_compute
from adapter.compute.live_tick_tails import make_tail_at
from usecase.dataset_port import dataset_port as _dataset_port
from usecase.serve_live_tick_tails import (
    parse_specs,
    period_of,
    states_for_batch,
    tails_for_ticks,
)

#: tf 文字列 → 秒（フロントの TF_BAR_SEC と同じ固定周期のみ対象。1W/1M は非対象）。
_TF_SEC = {"1m": 60, "5m": 300, "15m": 900, "30m": 1800, "1h": 3600, "4h": 14400, "1D": 86400}


def _period_fn(tf: str):
    """tf の周期キー規則（フロント ``live_tick_player._periodOf`` と同一）。

    ``1D`` はセッション日境界（ISSUE-078・NY17:00 ET 基準）で、UTC floor では表せない。規則源は
    ``marketdata.session_day``（ロールアップ / ``/forming_bar`` と同一実体）。日中足は UTC floor。
    """
    if tf != "1D":
        return None  # 既定（usecase の period_of＝UTC floor）。
    from marketdata.session_day import session_bar_time

    return lambda ms, _tf_sec: int(session_bar_time(int(ms) // 1000))


def _period_seed(ref: str, tf: str, first_ms: int, period_key: int, *, buffer: Any, forming: Any):
    """増分先頭 tick の直前までの「周期の累積」を ``(seed, prior_ticks)`` で返す（ISSUE-251）。

    材料は 2 系統あり、**周期が一致するほうを使う**:

    1. ロールアップ方式 forming（``rollup_forming_bar``＝確定畳み込み base ＋ バッファ tail の O(1)
       合成・``/forming_bar`` と同一実体＝フロントのシード源）を増分先頭 tick の秒で評価する。
       これは ``[周期始端, floor秒(first_ms))`` を被覆するので、残る端数
       ``[floor秒(first_ms), first_ms)`` だけをバッファから補う（同一秒に複数 tick が来るため
       端数を捨てると volume を取りこぼす）。
    2. 1 の周期が現周期と違うとき（周期境界直後は M1/ロールアップの焼き込みが最大 1 分遅れ、
       base が前周期のままになる）は seed を捨て、**周期始端以降のバッファ tick を全部畳む**。
       前周期の値を持ち込まないまま、周期の累積を復元できる（バッファ保持は直近 30 分）。

    buffer 未注入（テスト既定・非 served）は ``(None, [])``＝従来どおり増分だけで畳む。
    """
    if buffer is None:
        return None, []
    first_ms = int(first_ms)
    cut_sec = first_ms // 1000
    try:
        seed = forming.rollup_forming_bar(ref, tf, cut_sec, buffer=buffer)
    except Exception:  # noqa: BLE001 — seed 取得の失敗で tails 全体を落とさない。
        seed = None
    try:
        if seed is not None and int(seed.get("time", -1)) == int(period_key):
            lo_ms = cut_sec * 1000 - 1                      # seed は秒境界まで＝端数のみ補う
        else:
            # 周期始端は tf_meta（1D はセッション日始端＝ラベルとは別）を唯一源として解決する。
            from marketdata.tf_meta import period_start_unix

            seed = None
            lo_ms = int(period_start_unix(cut_sec, tf)) * 1000 - 1
        prior = [t for t in buffer.ticks_since(lo_ms) if int(t[0]) < first_ms]
    except Exception:  # noqa: BLE001 — 材料が揃わなければ増分のみへ縮退（tails は落とさない）。
        return None, []
    return seed, prior


def _set_last_bar(window: Any, values: "dict[str, float]") -> None:
    """窓の末尾行だけを形成中バーで上書きする（pandas 依存はここに閉じる）。"""
    last = len(window) - 1
    for key, val in values.items():
        if key in window.columns:
            window.iat[last, window.columns.get_loc(key)] = val


def handle_live_tick_tails(
    query: "dict[str, list[str]]",
    ticks: "list",
    *,
    adapter: Any = None,
    buffer: Any = None,
    forming: Any = None,
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

    # 形成中バーは「周期の累積」でなければならない（ISSUE-251）。増分だけを畳むと poll ごとに
    #   open/high/low/volume がリセットされ、フロントが描いたローソクと値が食い違う。
    tf_sec = _TF_SEC[tf]
    period_fn = _period_fn(tf)
    first_ms = int(ticks[0][0])
    period_key = (period_fn or period_of)(first_ms, tf_sec)
    seed, prior = _period_seed(
        ref, tf, first_ms, period_key, buffer=buffer, forming=forming or forming_bar_mod
    )
    states = states_for_batch(prior, ticks, tf_sec, seed=seed, period_fn=period_fn)
    tail_at = make_tail_at(
        df=df, adapter=adapter or IndicatorComputeAdapter(),
        latest_compute=latest_compute, set_last_bar=_set_last_bar,
    )
    return tails_for_ticks(states, specs, tail_at)
