"""出力アダプタ: lightweight-charts への btlm_trail_marod 系列追加（duck typing）。

層名/責務:
    出力アダプタ。``lightweight_charts`` を import せず、``create_line`` と
    ``horizontal_line`` を持つオブジェクト（chart）をダックタイピングで受ける
    （btlm_trail / profit_rsi の出力アダプタと同一様式）。MAROD（移動平均乖離率）を
    別 pane のオシレータ line 系列として供給し、0% 水平基準線を 1 本添える
    （±閾値線は対象外）。NaN（warm-up・未定義）は描画から除外する。

系列名（固定・F3 照合は catalog の SeriesDef 集合と突合）:
    - "btlm_trail_marod" : MAROD line 系列（別 pane オシレータ）。0% 基準線群 payload の
      name も compute_id（= "btlm_trail_marod"）に一致する（統合 FakeChart 規約）。
    - "btlm_trail_marod_q{pct}" : 正常バンド（分位バンド）系列。
    - "btlm_trail_marod_evq_{med|ext}_{hi|lo}" : 外れ値イベント分位の水準線（計 4 本・
      ma_marod と対称の設計＝ユーザー裁定 2026-07-21。中央値＝典型深度（実線）・極端分位
      ＝q_out 流用（破線）・直近 k_events 件）。σ バンドは認知負荷削減のため描画廃止
      （core の計算関数は温存＝復帰容易）。

依存:
    標準: __future__, typing / 外部: numpy, pandas /
    プロジェクト内: core、common.event_quantiles（表示規約ヘルパー・絶対 import）
"""

from __future__ import annotations

from typing import Optional, Protocol, runtime_checkable

import pandas as pd

from common.event_quantiles import emit_event_quantile_lines
from common_view.lwc_adapter import (  # noqa: E402
    emit_line as _emit_line,
    resolve_times as _resolve_times,
)
from common_view.lwc_adapter import SeriesLike  # noqa: E402

from .core import (
    DEFAULT_EVENT_AGG,
    DEFAULT_K_EVENTS,
    DEFAULT_MAXBARS,
    DEFAULT_Q_HIGH,
    DEFAULT_Q_LOW,
    DEFAULT_Q_OUT,
    DEFAULT_SOURCE,
    DEFAULT_WINDOW_N,
    marod_outlier_event_quantiles,
    marod_quantile_bands,
    marod_series,
)

_SERIES_NAME = "btlm_trail_marod"
_COLOR_MAROD = "rgba(123, 104, 238, 1)"   # MediumSlateBlue（btlm_trail 系と同系色）
# バンドは MAROD 本体（紫）と明確に区別できる高コントラスト色にする（視認性・ISSUE-143）。
_COLOR_QUANTILE = "rgba(38, 198, 218, 1)"    # 分位バンド（シアン・点線）＝紫の本体から独立
# イベント分位水準線の色・線種は common.event_quantiles（EVQ_COLOR/EVQ_LINE_SPECS）が単一情報源。
_BASELINE = 0.0                            # 0% 基準線（乖離ゼロ＝トレンド一致水準）
_LEVEL_COLOR = "rgba(84, 84, 84, 1)"       # 基準線色（profit_* 水準線と同系）
_LEVEL_WIDTH = 1


def _quantile_series_name(q: float) -> str:
    """分位 q（0..1）に対応する系列名（例 0.05 -> 'btlm_trail_marod_q5'）。

    btlm_trail/src/lwc_chart._quantile_series_name（'btlm_trail_q{pct}'）と対称の命名。
    """
    return f"{_SERIES_NAME}_q{int(round(q * 100))}"


_Line = SeriesLike  # 共有 Protocol の別名（要求は ``set`` のみ・構造的部分型）


@runtime_checkable
class _Chart(Protocol):
    def create_line(self, name: str, **kwargs) -> _Line: ...
    def horizontal_line(self, price: float, **kwargs): ...


def add_btlm_trail_marod(
    chart: _Chart,
    df: pd.DataFrame,
    *,
    source: str = DEFAULT_SOURCE,
    maxbars: int = DEFAULT_MAXBARS,
    q_low: float = DEFAULT_Q_LOW,
    q_high: float = DEFAULT_Q_HIGH,
    q_out: Optional[float] = DEFAULT_Q_OUT,
    k_events: int = DEFAULT_K_EVENTS,
    event_agg: str = DEFAULT_EVENT_AGG,
    window_n: int = DEFAULT_WINDOW_N,
    time_column: Optional[str] = None,
    color: str = _COLOR_MAROD,
) -> list:
    """``chart`` へ MAROD line 系列・0% 基準線・正常バンド・イベント分位水準線を追加する。

    正常バンド（実測 2026-07-20: MAROD は平均定常・分散非定常ゆえローリング化）:
        - btlm_trail_marod_q{pct} : 因果ローリング経験分位バンド（下側 q_low／上側 q_high・点線）。
    当該バー除外・非リペイント（core の _rolling_causal＝btlm_trail 経験分位と同一規約）。

    外れ値イベント分位の水準線（ma_marod と対称の設計＝ユーザー裁定 2026-07-21）:
        - btlm_trail_marod_evq_med_hi/lo : 正常バンド超イベントの中央値＝典型深度（赤実線）。
        - btlm_trail_marod_evq_ext_hi/lo : 同・極端分位（上側 q_out／下側 1-q_out・赤破線。
          q_out 無効は黙ってオフ＝空データ）。
        水準はバー t より前に確定した観測のみから計算（因果・非リペイント）。表示規約
        （系列名・色・線種）は common.event_quantiles の共有ヘルパーが単一情報源。

    描画廃止（認知負荷削減・ユーザー裁定 2026-07-21）: σ バンド（正常バンドと実質重複）。
    core の計算関数（marod_sigma_band）は温存（復帰容易）。

    Args:
        chart: ``create_line(name, **kwargs)`` と ``horizontal_line(price, **kwargs)``
            を持つオブジェクト（duck typing。別 pane の場合は subchart を渡す）。
        df: OHLC DataFrame（時刻は time / date / DatetimeIndex で解決）。
        source: 8 択ソース（既定 close）。maxbars: 回帰窓（既定 100・min 3）。
        q_low/q_high: 分位ペア（0<q_low<q_high<1・既定 0.05/0.95）＝正常バンド兼イベント境界。
        q_out: イベント極端分位（max(q_high, 0.5)<q_out<1 のみ有効・None/範囲外は黙ってオフ）。
        k_events: イベント分位ローリングの直近観測件数（既定 50・min 1）。
        event_agg: イベント集計単位（"episode"＝エピソード極値・既定／"bar"＝旧方式）。
        window_n: 正常バンドの因果ローリング窓（本数・既定 500・min 2）。
        time_column: 時刻列。color: MAROD 線の色。

    Returns:
        生成したオブジェクトのリスト（[marod_line, baseline_hline, q_lo, q_hi]
        ＋イベント分位水準線 4 本）。

    Raises:
        ValueError: source / maxbars / 分位ペア / window_n / k_events / event_agg 不正時
            （core の契約）。
        KeyError: 時刻が解決できない場合。
    """
    values = marod_series(df, source=source, maxbars=maxbars)
    times = _resolve_times(df, time_column)
    # バンド（MAROD 系列から因果ローリングで算出。window_n / 分位ペアは core が検証）。
    band_lo, band_hi = marod_quantile_bands(values, window_n=window_n, q_low=q_low, q_high=q_high)
    evq = marod_outlier_event_quantiles(
        values, window_n=window_n, q_low=q_low, q_high=q_high,
        q_out=q_out, k_events=k_events, event_agg=event_agg,
        bands=(band_lo, band_hi), include_all=False,   # 二重計算回避・_all 非描画（ISSUE-154）
    )

    created: list = []
    created.append(_emit_line(chart, _SERIES_NAME, times, values, color, "solid"))

    # 0% 水平基準線（乖離ゼロ＝価格が OLS トレンドに一致する水準）。±閾値線は対象外。
    created.append(chart.horizontal_line(
        price=_BASELINE, color=_LEVEL_COLOR, width=_LEVEL_WIDTH,
        style="solid", text="0%", axis_label_visible=False,
    ))

    # 分位バンド（点線・下側 q_low／上側 q_high）。
    created.append(_emit_line(chart, _quantile_series_name(q_low), times, band_lo, _COLOR_QUANTILE, "dotted"))
    created.append(_emit_line(chart, _quantile_series_name(q_high), times, band_hi, _COLOR_QUANTILE, "dotted"))
    # 外れ値イベント分位の水準線（共有ヘルパー＝表示規約の単一情報源・4 本）。
    created.extend(emit_event_quantile_lines(
        _SERIES_NAME, times, evq,
        lambda name, ts, vals, c, style: _emit_line(chart, name, ts, vals, c, style),
    ))
    return created
