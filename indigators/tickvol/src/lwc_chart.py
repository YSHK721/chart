"""出力アダプタ: lightweight-charts への系列追加（duck typing）。

層名/責務:
    出力アダプタ。``lightweight_charts`` を import せず、``create_histogram`` /
    ``create_line`` を持つオブジェクト（chart）をダックタイピングで受ける（ガイド §2/§6）。
    ティックボリューム本体（別ウィンドウ＝専用ペインのヒストグラム 1 本）に、外れ値水準線
    3 本を重ねる。水準線は同じペインの同じスケール（tick 数）上に乗る。

系列名（F3 照合は catalog の SeriesDef 集合と突合）:
    - ``tickvol``             : 本体（ヒストグラム）。その足の tick 数。
    - ``tickvol_q{pct}``      : 正常帯（因果ローリング経験分位・下側 q_low／上側 q_high・点線）。
      上側は POT の閾値そのもの。命名は ``btlm_trail_q{pct}`` / ``btlm_trail_marod_q{pct}``
      と対称（分位値に依存する動的名）。
    - ``tickvol_evq_med_hi``  : 典型深度（イベントの中央値・実線）。
    - ``tickvol_evq_ext_hi``  : 経験的極端分位（q_out・破線）。
    - ``tickvol_gpd_hi``      : GPD 外挿水準（同じ q_out・破線・別色）。
    - ``tickvol_trend_mean``  : 回帰トレンド現在位置（btlm_trail 仕様・ドット）。
    - ``tickvol_trend_q{pct}``: トレンド帯（下側 q_low／上側 q_high・点線・動的名）。
    - ``tickvol_trend_off_hi`` / ``tickvol_trend_off_lo`` : 外れ値分位線（q_out・破線）。
    - ``tickvol_trend_beta`` / ``_sigma`` / ``_band_hit_rate`` : 読取欄専用（不可視）。

    トレンド系は `btlm_trail` の仕様（回帰窓末尾 OLS＋帯＋外れ値分位線＋β/σ/実績率）を
    tick 数系列へ適用したもので、既存の水準帯（``tickvol_q{pct}``＝水準分布の分位）とは
    基準が違う（トレンドからの乖離率 vs 水準そのもの）。分位値 q_low/q_high/q_out は
    両者で共有する（同じ分位で「水準の帯」と「トレンドの帯」を並べて読む）。

    ``_evq_{med|ext}_{hi|lo}`` の命名・色・線種は共有規約
    （:mod:`common.event_quantiles` の ``EVQ_LINE_SPECS`` / ``EVQ_COLOR``）に従う。下側
    （``_lo``）は持たない: tickvol は 1 tick 以上でしか足が立たない計数量で下側は裾でない
    （実測 min=1・0 の足は 0 本）。GPD 線は経験的線と**並べて読む**ことが目的なので、
    共有の外れ値色をそのまま使うと 2 本が区別できない。別色を本モジュールに置き、共有定数
    ``EVQ_COLOR`` は書き換えない（他 2 指標へ非波及・ISSUE-223 と同規律）。

呼出規約:
    ``add_tickvol(chart, df)``。API 経路（``adapter.compute.call_binding``）は df 以降を
    キーワード専用（kind="kw"）で渡す。

依存:
    標準: __future__, typing / 外部: pandas / 共有: common_view.lwc_adapter・
    common.event_quantiles / プロジェクト内: .core, .levels
"""

from __future__ import annotations

from typing import Optional, Protocol, runtime_checkable

import numpy as np
import pandas as pd

from common.event_quantiles import DEFAULT_K_EVENTS, DEFAULT_Q_OUT, EVQ_COLOR, EVQ_LINE_SPECS
from common_view.lwc_adapter import SeriesLike  # noqa: E402
from common_view.lwc_adapter import emit_line as _emit_line  # noqa: E402
from common_view.lwc_adapter import resolve_times as _resolve_times  # noqa: E402

from .core import TICKVOL_COLUMN, build_tickvol
from .levels import DEFAULT_Q_HIGH, DEFAULT_Q_LOW, DEFAULT_WINDOW_N, tickvol_levels
from .trend import (
    DEFAULT_BAND_METHOD,
    DEFAULT_EMP_N,
    DEFAULT_MAXBARS,
    DEFAULT_N_COV,
    tickvol_trend,
)

# 既定色。暗背景（チャート #131722）で価格系列と competing しない中間色（Blue Grey）。
_COLOR = "rgba(120, 144, 156, 0.85)"
# GPD 外挿線の色。経験的線（EVQ_COLOR＝赤系）と並べて読むための別色（琥珀系）。
_GPD_COLOR = "rgba(255, 167, 38, 1)"
# 正常帯（分位バンド）の色・線種。MAROD 系の _COLOR_QUANTILE と同値（指標間で同じ意味の
#   線は同じ見た目にする）。
_QUANTILE_COLOR = "rgba(38, 198, 218, 1)"
# トレンド系の色。btlm_trail の表示規約と同値にする（同じ意味の線は同じ見た目・値は
#   btlm_trail/src/lwc_chart.py の _COLOR_MEAN / _COLOR_BAND / _COLOR_OFFSET / _COLOR_METRIC）。
#   btlm_trail 側の定数は private のため参照できない。同値であることを本コメントで固定する。
_TREND_COLOR = "rgba(123, 104, 238, 1)"
_TREND_BAND_COLOR = "rgba(123, 104, 238, 0.6)"
_TREND_OFFSET_COLOR = "rgba(210, 67, 58, 0.8)"
_TREND_METRIC_COLOR = "rgba(160, 160, 160, 1)"
# ドット（サークル）の明示半径（px）。btlm_trail の _POINT_RADIUS と同値。
_TREND_POINT_RADIUS = 3.5

# 共有の表示規約から線種を引く（med=実線 / ext=破線）。規約の単一情報源は event_quantiles。
_LINE_STYLE = dict(EVQ_LINE_SPECS)


def _quantile_series_name(q: float) -> str:
    """分位 q（0..1）に対応する系列名（例 0.10 -> 'tickvol_q10'）。

    ``btlm_trail_marod/src/lwc_chart._quantile_series_name`` と対称の命名。
    """
    return f"{TICKVOL_COLUMN}_q{int(round(q * 100))}"


def _trend_quantile_series_name(q: float) -> str:
    """トレンド帯の系列名（例 0.10 -> 'tickvol_trend_q10'）。

    水準帯（``tickvol_q{pct}``）と分位値を共有するため、接頭辞で区別する
    （同じ pct で 2 本あるので名前が衝突してはならない）。
    """
    return f"{TICKVOL_COLUMN}_trend_q{int(round(q * 100))}"


def _emit_hinted(chart, name, times, values, color, style, **hints):
    """描画ヒント付きで 1 本 emit する（``btlm_trail/src/lwc_chart._emit`` と同型）。

    共有の :func:`common_view.lwc_adapter.emit_line` はヒント（ドット表示・読取欄専用）を
    渡せない。btlm_trail 側の ``_emit`` は private で参照できないため、同じ配管を本モジュールへ
    置く（計算は持たない・NaN 除外の規約も同一）。
    """
    line = chart.create_line(
        name=name, color=color, style=style, price_line=False, price_label=False,
        **{k: v for k, v in hints.items() if v is not None},
    )
    frame = pd.DataFrame({"time": times, name: np.asarray(values, dtype=float)}).dropna()
    line.set(frame)
    return line


_Series = SeriesLike  # 共有 Protocol の別名（要求は ``set`` のみ・構造的部分型）


@runtime_checkable
class _Chart(Protocol):
    def create_histogram(self, name: str, **kwargs) -> _Series: ...
    def create_line(self, name: str, **kwargs) -> _Series: ...


def add_tickvol(
    chart: _Chart,
    df: pd.DataFrame,
    *,
    window_n: int = DEFAULT_WINDOW_N,
    q_low: float = DEFAULT_Q_LOW,
    q_high: float = DEFAULT_Q_HIGH,
    q_out: Optional[float] = DEFAULT_Q_OUT,
    k_events: int = DEFAULT_K_EVENTS,
    maxbars: int = DEFAULT_MAXBARS,
    band_method: str = DEFAULT_BAND_METHOD,
    empirical_n: int = DEFAULT_EMP_N,
    show_metrics: bool = True,
    n_cov: int = DEFAULT_N_COV,
    time_column: "str | None" = None,
    color: str = _COLOR,
) -> list:
    """chart にティックボリューム（1 足あたり tick 数）・正常帯・外れ値水準線を追加する。

    Args:
        chart: ``create_histogram`` / ``create_line`` を持つオブジェクト（duck typing。
            別ウィンドウの場合は subchart を渡す）。
        df: OHLCV DataFrame（``volume`` 列＝tick 数が必須）。時刻は time/date 列または
            DatetimeIndex から解決する。
        window_n: 正常帯（＝POT 閾値）の因果窓（当該バー除外の直近 N 本）。
        q_low: 正常帯の下側分位（表示専用。POT/GPD には使わない）。
        q_high: 正常帯の上側分位＝POT の閾値分位。
        q_out: イベントの極端分位。無効値（``max(q_high, 0.5) < q_out < 1`` を満たさない）は
            黙って無効化し、極端分位・GPD 線を NaN にする（共有規約 ``q_out_valid``）。
        k_events: 水準に使う直近観測件数（経験的・GPD で共通）。
        maxbars: 回帰トレンドの窓（btlm_trail F-01）。
        band_method: トレンド帯の方式（``"ols"`` / ``"empirical"``・既定は実測により経験分位）。
        empirical_n: 経験分位トレンド帯の参照本数。
        show_metrics: β/σ/バンド内実績率の読取欄系列を出すか（btlm_trail F-09）。
        n_cov: バンド内実績率のローリング本数。
        time_column: 時刻列の明示指定（省略時は探索）。
        color: ヒストグラムの色。

    Returns:
        追加した系列オブジェクトの list（ヒストグラム 1 + 正常帯 2 + 水準線 3 + トレンド系）。

    Raises:
        KeyError: volume 列が無い、または時刻を解決できない場合。
        ValueError: ``k_events < 1``・分位ペア不正など水準パラメータが不正な場合。
    """
    times = _resolve_times(df, time_column)
    values = build_tickvol(df).reset_index(drop=True)

    out = []
    hist = chart.create_histogram(TICKVOL_COLUMN, color=color)
    frame = pd.DataFrame({"time": times, TICKVOL_COLUMN: values}).dropna(
        subset=[TICKVOL_COLUMN]
    )
    hist.set(frame)
    out.append(hist)

    levels = tickvol_levels(
        values.to_numpy(dtype=float),
        window_n=window_n, q_low=q_low, q_high=q_high, q_out=q_out, k_events=k_events,
    )
    # 正常帯（下側 → 上側の順。MAROD 系の emit 順と同一）。
    for q, key in ((q_low, "band_low"), (q_high, "band_high")):
        out.append(_emit_line(
            chart, _quantile_series_name(q), times, levels[key], _QUANTILE_COLOR, "dotted"
        ))
    for key, name, line_color in (
        ("med", f"{TICKVOL_COLUMN}_evq_med_hi", EVQ_COLOR),
        ("ext", f"{TICKVOL_COLUMN}_evq_ext_hi", EVQ_COLOR),
        ("gpd", f"{TICKVOL_COLUMN}_gpd_hi", _GPD_COLOR),
    ):
        style = _LINE_STYLE.get("med_hi" if key == "med" else "ext_hi", "solid")
        out.append(_emit_line(chart, name, times, levels[key], line_color, style))

    # --- 回帰トレンド（btlm_trail 仕様の参照拡張）---------------------------
    trend = tickvol_trend(
        values.to_numpy(dtype=float), maxbars=maxbars, q_low=q_low, q_high=q_high,
        band_method=band_method, empirical_n=empirical_n, q_out=q_out, n_cov=n_cov,
        with_metrics=show_metrics,
    )
    # トレンド現在位置（既定ドット emit＝btlm_trail と同一。切替はスタイルタブ）。
    out.append(_emit_hinted(
        chart, f"{TICKVOL_COLUMN}_trend_mean", times, trend["mean"], _TREND_COLOR,
        "solid", width=2, point_markers=True, line_visible=False,
        point_markers_radius=_TREND_POINT_RADIUS,
    ))
    # トレンド帯（下側 → 上側）。水準帯と分位を共有するため接頭辞で区別する。
    for q, key in ((q_low, "band_low"), (q_high, "band_high")):
        out.append(_emit_hinted(
            chart, _trend_quantile_series_name(q), times, trend[key], _TREND_BAND_COLOR,
            "dotted", point_markers=True, line_visible=False,
            point_markers_radius=_TREND_POINT_RADIUS,
        ))
    # 外れ値分位線（q_out 無効時は compute が None＝線なし。btlm_trail F-08 と同一規約）。
    if trend["off_low"] is not None and trend["off_high"] is not None:
        for key, suffix in (("off_high", "off_hi"), ("off_low", "off_lo")):
            out.append(_emit_hinted(
                chart, f"{TICKVOL_COLUMN}_trend_{suffix}", times, trend[key],
                _TREND_OFFSET_COLOR, "dashed", line_visible=True, point_markers=False,
            ))
    # 数値表示（β・残差 σ・バンド内実績率）は読取欄オーバーレイ用の不可視系列（btlm_trail F-09）。
    if show_metrics:
        for key, suffix in (("beta", "beta"), ("sigma", "sigma"),
                            ("band_hit_rate", "band_hit_rate")):
            if trend[key] is None:
                continue
            out.append(_emit_hinted(
                chart, f"{TICKVOL_COLUMN}_trend_{suffix}", times, trend[key],
                _TREND_METRIC_COLOR, "solid", width=1, line_visible=False,
                point_markers=False, readout_only=True,
            ))
    return out
