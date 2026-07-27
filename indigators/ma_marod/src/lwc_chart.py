"""出力アダプタ: lightweight-charts への ma_marod 系列追加（duck typing）。

層名/責務:
    出力アダプタ。``lightweight_charts`` を import せず、``create_line`` と
    ``horizontal_line`` を持つオブジェクト（chart）をダックタイピングで受ける
    （btlm_trail_marod の出力アダプタと同一様式）。MA_MAROD（移動平均乖離率・MA 種別
    選択式）を別 pane のオシレータ line 系列として供給し、0% 水平基準線を 1 本添える
    （±閾値線は対象外）。NaN（warm-up・未定義）は描画から除外する。

    ライブ・リプレイで実装は単一（本アダプタのみ。モード別の分岐は持たない）。

系列名（固定・F3 照合は catalog の SeriesDef 集合と突合）:
    - "ma_marod" : MA_MAROD line 系列（別 pane オシレータ）。0% 基準線群 payload の
      name も compute_id（= "ma_marod"）に一致する（統合 FakeChart 規約）。
    - "ma_marod_q{pct}" : 正常バンド（分位バンド）系列。
    - "ma_marod_evq_{med|ext}_{hi|lo}" : 外れ値イベント分位の水準線（計 4 本・ユーザー裁定
      2026-07-21。正常バンド超のイベントの中央値＝典型深度（実線）と極端分位＝q_out 流用
      （破線）。直近 k_events 件のローリング。トレード時に「外れたらどこまで行くか」を
      事前把握するための水準）。σ バンドと全履歴（_all）系列は認知負荷削減のため描画廃止
      （ユーザー裁定 2026-07-21・core の計算機能は温存＝復帰容易）。

依存:
    標準: __future__, typing / 外部: numpy, pandas / プロジェクト内: core
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
    DEFAULT_LENGTH,
    DEFAULT_MA_TYPE,
    DEFAULT_Q_HIGH,
    DEFAULT_Q_LOW,
    DEFAULT_Q_OUT,
    DEFAULT_SOURCE,
    DEFAULT_WINDOW_N,
    ma_marod_outlier_event_quantiles,
    ma_marod_quantile_bands,
    ma_marod_series,
)

_SERIES_NAME = "ma_marod"
_COLOR_MA_MAROD = "rgba(255, 152, 0, 1)"   # 橙（marod の紫系と識別・基本設計 §5 仮値）
# バンドは本体（橙）と明確に区別できる色にする（btlm_trail_marod の視認性規約 ISSUE-143 踏襲）。
_COLOR_QUANTILE = "rgba(38, 198, 218, 1)"    # 分位バンド（シアン・点線）＝marod と同色
# イベント分位水準線の色・線種は common.event_quantiles（EVQ_COLOR/EVQ_LINE_SPECS）が単一情報源。
_BASELINE = 0.0                            # 0% 基準線（乖離ゼロ＝価格が MA に一致する水準）
_LEVEL_COLOR = "rgba(84, 84, 84, 1)"       # 基準線色（profit_* 水準線と同系）
_LEVEL_WIDTH = 1


def _quantile_series_name(q: float) -> str:
    """分位 q（0..1）に対応する系列名（例 0.05 -> 'ma_marod_q5'）。

    btlm_trail_marod/src/lwc_chart._quantile_series_name（'{id}_q{pct}'）と対称の命名。
    """
    return f"{_SERIES_NAME}_q{int(round(q * 100))}"


_Line = SeriesLike  # 共有 Protocol の別名（要求は ``set`` のみ・構造的部分型）


@runtime_checkable
class _Chart(Protocol):
    def create_line(self, name: str, **kwargs) -> _Line: ...
    def horizontal_line(self, price: float, **kwargs): ...


def add_ma_marod(
    chart: _Chart,
    df: pd.DataFrame,
    *,
    source: str = DEFAULT_SOURCE,
    ma_type: str = DEFAULT_MA_TYPE,
    length: int = DEFAULT_LENGTH,
    q_low: float = DEFAULT_Q_LOW,
    q_high: float = DEFAULT_Q_HIGH,
    q_out: Optional[float] = DEFAULT_Q_OUT,
    k_events: int = DEFAULT_K_EVENTS,
    event_agg: str = DEFAULT_EVENT_AGG,
    window_n: int = DEFAULT_WINDOW_N,
    time_column: Optional[str] = None,
    color: str = _COLOR_MA_MAROD,
) -> list:
    """``chart`` へ MA_MAROD line 系列・0% 基準線・正常バンド・イベント分位水準線を追加する。

    正常バンド（btlm_trail_marod と同一仕様・実測 2026-07-20: 乖離率は分散非定常ゆえローリング化）:
        - ma_marod_q{pct} : 因果ローリング経験分位バンド（下側 q_low／上側 q_high・点線）。
    当該バー除外・非リペイント（btlm_trail_marod core の _rolling_causal 規約）。

    外れ値イベント分位の水準線（ユーザー裁定 2026-07-21・トレード時の事前把握水準）:
        - ma_marod_evq_med_hi/lo : 正常バンド超イベントの中央値＝典型深度（直近 k_events 件・
          赤実線）。「外れたら典型的にここまで行く」水準。
        - ma_marod_evq_ext_hi/lo : 同・極端分位（上側 q_out／下側 1-q_out・赤破線）。
          q_out 無効（None/範囲外）は黙ってオフ。
        水準はバー t より前のイベントのみから計算（因果・非リペイント）＝現在バーの水準は
        足が動く前から確定しており、事前に把握できる。

    描画廃止（認知負荷削減・ユーザー裁定 2026-07-21）: σ バンド（正常バンドと実質重複）と
    全履歴版イベント分位（_all・直近K件と重複しがち）。core の計算機能は温存（復帰容易）。

    Args:
        chart: ``create_line(name, **kwargs)`` と ``horizontal_line(price, **kwargs)``
            を持つオブジェクト（duck typing。別 pane の場合は subchart を渡す）。
        df: OHLC DataFrame（時刻は time / date / DatetimeIndex で解決）。
        source: 8 択ソース（既定 close・解決は moving_averages と同期）。
        ma_type: 基準線 MA 種別（sma/ema/smma/lwma・既定 ema）。
        length: MA 本数（既定 50・min 2）。
        q_low/q_high: 分位ペア（0<q_low<q_high<1・既定 0.05/0.95）＝正常バンド兼イベント境界。
        q_out: イベント極端分位（max(q_high, 0.5)<q_out<1 のみ有効・None/範囲外は黙ってオフ）。
        k_events: イベント分位ローリングの直近観測件数（既定 50・min 1）。
        event_agg: イベント集計単位（"episode"＝エピソード極値・既定／"bar"＝バー値・旧方式。
            確認の結果で戻せるよう UI から切替可能）。
        window_n: σ・分位の因果ローリング窓（本数・既定 500・min 2）。
        time_column: 時刻列。color: MA_MAROD 線の色。

    Returns:
        生成したオブジェクトのリスト（[marod_line, baseline_hline, q_lo, q_hi]
        ＋イベント分位水準線（中央値 2 本＋極端 2 本。q_out 無効時の極端線は空データ））。

    Raises:
        ValueError: source / ma_type / length / 分位ペア / window_n / k_events 不正時（core の契約）。
        KeyError: 時刻が解決できない場合。
    """
    values = ma_marod_series(df, source=source, ma_type=ma_type, length=length)
    times = _resolve_times(df, time_column)
    # バンド（MA_MAROD 系列から因果ローリングで算出。window_n / 分位ペアは参照 core が検証）。
    band_lo, band_hi = ma_marod_quantile_bands(values, window_n=window_n, q_low=q_low, q_high=q_high)
    evq = ma_marod_outlier_event_quantiles(
        values, window_n=window_n, q_low=q_low, q_high=q_high,
        q_out=q_out, k_events=k_events, event_agg=event_agg,
        bands=(band_lo, band_hi), include_all=False,   # 二重計算回避・_all 非描画（ISSUE-154）
    )

    created: list = []
    created.append(_emit_line(chart, _SERIES_NAME, times, values, color, "solid"))

    # 0% 水平基準線（乖離ゼロ＝価格が基準線 MA に一致する水準）。±閾値線は対象外。
    created.append(chart.horizontal_line(
        price=_BASELINE, color=_LEVEL_COLOR, width=_LEVEL_WIDTH,
        style="solid", text="0%", axis_label_visible=False,
    ))

    # 分位バンド（点線・下側 q_low／上側 q_high）。
    created.append(_emit_line(chart, _quantile_series_name(q_low), times, band_lo, _COLOR_QUANTILE, "dotted"))
    created.append(_emit_line(chart, _quantile_series_name(q_high), times, band_hi, _COLOR_QUANTILE, "dotted"))
    # 外れ値イベント分位の水準線（共有ヘルパー＝表示規約の単一情報源・直近K件のみ・4 本）。
    #   極端分位（ext_*）は q_out 無効時に core が全 NaN を返し dropna で空＝空系列 emit
    #   （系列自体は静的名で常設。空データは描画なしと等価）。
    created.extend(emit_event_quantile_lines(
        _SERIES_NAME, times, evq,
        lambda name, ts, vals, c, style: _emit_line(chart, name, ts, vals, c, style),
    ))
    return created
