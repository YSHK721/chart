"""層名: 出力アダプタ（lightweight-charts への系列追加・duck typing）。

責務:
    出力アダプタ。``lightweight_charts`` を import せず、``create_line`` を持つオブジェクト
    （chart）をダックタイピングで受ける（PORTING_GUIDE §2/§6）。指標パッケージの依存を
    numpy/pandas に保つ。別ウィンドウ（[0,100]）に RSI 線 1 本、正常帯 2 本（因果ローリング
    分位）、外れ値水準 4 本（経験的分位 ext・GPD 外挿 gpd の上下）を追加する。
    warm-up（i<rsi_period）は元 iRSI 既定どおり 0 で描画される（NaN は発生しない）。

    ライン ``name`` は値列名（``rsi`` / ``rsi_q10`` / ``rsi_evq_ext_hi`` …）と完全一致させる
    （ガイド §5）。Apply 依存の短名（"RSI-Typical price (6)" 等）は plot 凡例側の関心事であり、
    lwc のライン name には用いない。

    色・線種は共有の表示規約に従う（同じ意味の線は指標間で同じ見た目にする）:
      正常帯 = 分位バンド色（シアン・点線）／経験的水準 = ``EVQ_COLOR``（赤系・破線）／
      GPD 外挿 = 琥珀系（tickvol の ``_GPD_COLOR`` と同値。経験的線と並べて読むための別色）。
    共有定数は書き換えない（他指標へ非波及・ISSUE-223 と同規律）。

元 MQL4 対応:
    ``SetIndexStyle(0, DRAW_LINE)``（ExtRSIBuffer, indicator_color1 clrLime,
    separate_window, indicator_minimum 0 / indicator_maximum 100）。
    元 ExtMABuffer（EMA 平滑線）と σ 7 水準は本移植では持たない（SPEC §2 / §5.4・
    承認 2026-08-02）。σ 水準は全系列＝非因果であり、因果ローリング分位＋POT/GPD へ置換した。

依存（PORTING_GUIDE §8）:
    標準: __future__, typing / 外部: pandas / プロジェクト内: rsi, levels, core, common*
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

import pandas as pd

from common.event_quantiles import DEFAULT_K_EVENTS, DEFAULT_Q_OUT
from common_view.event_quantile_view import EVQ_COLOR
from common_view.lwc_adapter import SeriesLike  # noqa: E402
from common_view.lwc_adapter import emit_line as _emit_line  # noqa: E402
from common_view.lwc_adapter import resolve_times as _resolve_times  # noqa: E402

from .core import DEFAULT_APPLY, DEFAULT_RSI_PERIOD
from .levels import DEFAULT_Q_HIGH, DEFAULT_Q_LOW, DEFAULT_WINDOW_N
from .rsi import LEVEL_COLUMNS, RSI_COLUMN, build_rsi, quantile_column

_RSI_COLOR = "rgba(0, 255, 0, 1)"       # 元 indicator_color1 clrLime
_WIDTH = 1                              # 元 indicator_width 未指定（既定 1）
# 正常帯（分位バンド）の色・線種。MAROD 系・tickvol と同値（同じ意味の線は同じ見た目）。
_QUANTILE_COLOR = "rgba(38, 198, 218, 1)"
# GPD 外挿線の色。経験的線（EVQ_COLOR＝赤系）と並べて読むための別色（琥珀系・tickvol と同値）。
_GPD_COLOR = "rgba(255, 167, 38, 1)"


_Line = SeriesLike  # 共有 Protocol の別名（要求は ``set`` のみ・構造的部分型）


@runtime_checkable
class _Chart(Protocol):
    def create_line(self, name: str, **kwargs) -> _Line: ...


def add_rsi(
    chart: _Chart,
    df: pd.DataFrame,
    *,
    rsi_period: int = DEFAULT_RSI_PERIOD,
    apply: int = DEFAULT_APPLY,
    window_n: int = DEFAULT_WINDOW_N,
    q_low: float = DEFAULT_Q_LOW,
    q_high: float = DEFAULT_Q_HIGH,
    q_out: "float | None" = DEFAULT_Q_OUT,
    k_events: int = DEFAULT_K_EVENTS,
    time_column: str | None = None,
    draw_levels: bool = True,
) -> list:
    """chart に RSI 線（1 本）と正常帯（2 本）・外れ値水準（4 本）を追加する。

    Args:
        chart: ``create_line(name, **kwargs)`` を持つオブジェクト（duck typing。別ウィンドウの
            場合は subchart を渡す）。
        df: OHLC DataFrame（open/high/low/close 必須・列名大小不問・**volume 不要**）。
        rsi_period: RSI 期間（既定 6。元 InpRSIPeriod）。
        apply: 適用価格選択（既定 5 -> TYPICAL。元 Apply。core の APPLY_TO_PRICE 写像）。
        window_n: 正常帯（＝POT 閾値）の因果ローリング窓（当該バー除外の直近 N 本）。
        q_low / q_high: 正常帯の下側・上側分位（＝下側・上側 POT の閾値分位）。
        q_out: 超過エピソードの極端分位。無効値（``max(q_high, 0.5) < q_out < 1`` を満たさない）
            は黙って無効化し、外れ値水準を NaN にする（共有規約 ``q_out_valid``）。
        k_events: 水準に使う直近観測件数（経験的・GPD で共通）。
        time_column: 時刻列の明示指定（省略時は time/date/DatetimeIndex を探索）。
        draw_levels: False で正常帯・外れ値水準を描かない（RSI 線のみ）。

    Returns:
        生成したオブジェクトのリスト（[rsi_line] ＋ draw_levels 時は正常帯 2 ＋ 水準 4）。

    Raises:
        KeyError: 時刻が解決できない / OHLC 列が無い場合。
        ValueError: 水準パラメータ（window_n・分位ペア・k_events）が不正な場合。
    """
    built = build_rsi(
        df, rsi_period=rsi_period, apply=apply, window_n=window_n,
        q_low=q_low, q_high=q_high, q_out=q_out, k_events=k_events,
    )
    times = _resolve_times(df, time_column)

    created: list = []
    # RSI 線。値列名はライン名と完全一致させる（ガイド §5）。
    # 多数線のため price_line/label は False（§6）。warm-up は 0 で残る（NaN 無し）。
    line = chart.create_line(
        name=RSI_COLUMN, color=_RSI_COLOR, style="solid", width=_WIDTH,
        price_line=False, price_label=False,
    )
    line.set(pd.DataFrame({"time": times, RSI_COLUMN: built[RSI_COLUMN].to_numpy()}))
    created.append(line)

    if not draw_levels:
        return created

    # 正常帯（下側 → 上側の順。MAROD 系・tickvol の emit 順と同一）。
    for q in (q_low, q_high):
        name = quantile_column(q)
        created.append(_emit_line(
            chart, name, times, built[name].to_numpy(), _QUANTILE_COLOR, "dotted"
        ))
    # 外れ値水準（経験的 ext = 赤系破線 / GPD 外挿 = 琥珀破線。上側 → 下側の順）。
    for key, color in (("ext_hi", EVQ_COLOR), ("ext_lo", EVQ_COLOR),
                       ("gpd_hi", _GPD_COLOR), ("gpd_lo", _GPD_COLOR)):
        name = LEVEL_COLUMNS[key]
        created.append(_emit_line(
            chart, name, times, built[name].to_numpy(), color, "dashed"
        ))
    return created
