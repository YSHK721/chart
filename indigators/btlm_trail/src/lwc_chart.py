"""出力アダプタ: lightweight-charts への btlm_trail 系列追加（duck typing）。

層名/責務:
    出力アダプタ。``lightweight_charts`` を import せず、``create_line`` を持つ
    オブジェクト（chart）をダックタイピングで受ける（ガイド §2/§6）。btlm_trail の
    トレンド現在位置（btlm_mean）と分位ペアごとのバンド端（q_low/q_high）を系列として
    追加する。ドット/ライン・塗り・外れ値オフセット等の表示切替は front 描画層が担う
    （本層は数値系列を供給する）。

系列名（固定・F3 照合は catalog の SeriesDef 集合と突合）:
    - "btlm_trail_mean"       : 窓末尾 btlm_mean（トレンド現在位置＝ドットの原子）。
    - "btlm_trail_q{pct}"     : 分位ペアごとのバンド端（pct = round(q*100)）。

依存:
    標準: __future__, typing / 外部: numpy, pandas / プロジェクト内: core, trail
"""

from __future__ import annotations

from typing import Optional, Protocol, runtime_checkable

import numpy as np
import pandas as pd

from .core import DEFAULT_EMP_N, DEFAULT_MAXBARS, DEFAULT_N_COV, DEFAULT_Q_HIGH, DEFAULT_Q_LOW
from .trail import build_btlm_trail, rolling_coverage

_COLOR_MEAN = "rgba(123, 104, 238, 1)"   # MediumSlateBlue（tgp_btlm と同系色）
_COLOR_BAND = "rgba(123, 104, 238, 0.6)"
_COLOR_OFFSET = "rgba(210, 67, 58, 0.8)"  # 外れ値オフセット（赤系・ストップ距離の可視化）
_COLOR_METRIC = "rgba(160, 160, 160, 1)"  # 数値表示（読取欄用・不可視系列）


@runtime_checkable
class _Line(Protocol):
    def set(self, data: pd.DataFrame) -> None: ...


@runtime_checkable
class _Chart(Protocol):
    def create_line(self, name: str, **kwargs) -> _Line: ...


def _resolve_times(df: pd.DataFrame, time_column: Optional[str]) -> pd.Series:
    """時刻系列を解決する（明示指定 > time 列 > date 列 > DatetimeIndex の順）。"""
    lower_map = {str(c).lower(): c for c in df.columns}
    if time_column is not None:
        tcol = lower_map.get(time_column.lower(), time_column)
        if tcol not in df.columns:
            raise KeyError(f"指定された時刻列が存在しません: {time_column}")
        return pd.to_datetime(df[tcol]).reset_index(drop=True)
    if "time" in lower_map:
        return pd.to_datetime(df[lower_map["time"]]).reset_index(drop=True)
    if "date" in lower_map:
        return pd.to_datetime(df[lower_map["date"]]).reset_index(drop=True)
    if isinstance(df.index, pd.DatetimeIndex):
        return pd.Series(df.index, name="time").reset_index(drop=True)
    raise KeyError("時刻を解決できません（time/date 列、または DatetimeIndex が必要）。")


def _quantile_series_name(q: float) -> str:
    """分位 q（0..1）に対応する系列名（例 0.05 -> 'btlm_trail_q5'）。"""
    return f"btlm_trail_q{int(round(q * 100))}"


_POINT_RADIUS = 3.5  # ドット（サークル）の明示半径（px）。ズームアウトでも円と視認できる大きさ。


def _emit(chart, name, times, values, color, *, style="solid", width=1,
          point_markers=None, line_visible=None, readout_only=None,
          point_markers_radius=None):
    """1 系列を chart へ追加する（値列名は系列名と一致・NaN は除外）。

    描画ヒント（表示層で lightweight-charts のオプションへ写像される・後方互換で任意）:
        point_markers: ドット（サークル）描画の有無（pointMarkersVisible）。
        line_visible : 接続線の可視性（lineVisible）。
        readout_only : 読取欄専用（β/σ/被覆率など）。価格軸オートスケールから除外する
                       （autoscaleInfoProvider→null）。0..1 等の小値系列が価格スケールを歪め
                       ローソクを圧縮するのを防ぐ。
    None は従来挙動（ヒント未付与）。
    """
    kwargs = dict(
        name=name, color=color, style=style, width=width,
        price_line=False, price_label=False,
    )
    if point_markers is not None:
        kwargs["point_markers"] = point_markers
    if line_visible is not None:
        kwargs["line_visible"] = line_visible
    if readout_only is not None:
        kwargs["readout_only"] = readout_only
    if point_markers_radius is not None:
        kwargs["point_markers_radius"] = point_markers_radius
    line = chart.create_line(**kwargs)
    series = pd.DataFrame({"time": times, name: np.asarray(values, dtype=float)}).dropna()
    line.set(series)
    return line


def add_btlm_trail(
    chart: _Chart,
    df: pd.DataFrame,
    *,
    source: str = "close",
    maxbars: int = DEFAULT_MAXBARS,
    q_low: float = DEFAULT_Q_LOW,
    q_high: float = DEFAULT_Q_HIGH,
    band_method: str = "ols",
    empirical_n: int = DEFAULT_EMP_N,
    q_out=None,
    show_metrics: bool = True,
    n_cov: int = DEFAULT_N_COV,
    time_column: Optional[str] = None,
    color: str = _COLOR_MEAN,
) -> dict[str, object]:
    """``chart`` へ btlm_trail の系列一式を追加する（単一分位ペア）。

    系列:
        - btlm_trail_mean / btlm_trail_q{pct}: トレンド現在位置とバンド端。既定はドット（サークル）で
          emit し、ドット/ライン切替は設定ダイアログ「スタイル」タブで系列単位に行う（案A）。
        - btlm_trail_off_hi/lo: 外れ値分位ライン（上側 q_out／下側 1-q_out・既定オフ）。
        - btlm_trail_beta/sigma/band_hit_rate: β・残差 σ・バンド内実績率（読取欄オーバーレイ用・不可視）。

    分位ペアは UI 由来のスカラ q_low/q_high（0<q_low<q_high<1）を用いる（tgp_btlm と対称）。

    Args:
        chart: ``create_line(name=...)`` を持つオブジェクト（duck typing）。
        df: OHLC DataFrame（時刻は time / date / DatetimeIndex で解決）。
        source: 8 択ソース。maxbars: 回帰窓。q_low/q_high: 分位ペア。
        band_method: "ols"/"empirical"。empirical_n: 経験分位の参照本数。
        q_out: 外れ値分位（q_high<q_out<1 のみ有効・無効/空はオフ＝補助線なし）。バンド方式と同一規約で算出。
        show_metrics: β/σ/バンド内実績率の読取欄系列を出すか。n_cov: 被覆率のローリング本数。
        time_column: 時刻列。color: btlm_mean の色。

    Returns:
        ``{series_name: Line}``。

    Raises:
        ValueError: source / 分位ペア / band_method 不正時。
        KeyError: 時刻が解決できない場合。
    """
    res = build_btlm_trail(
        df, source=source, maxbars=maxbars,
        q_low=q_low, q_high=q_high, band_method=band_method,
        empirical_n=empirical_n, q_out=q_out,
    )
    times = _resolve_times(df, time_column)
    # 既定はドット（サークル）で emit。ドット/ライン切替はスタイルタブ（applySeriesStyle の display）が
    #   描画後に上書きする（案A）。ここでは常に dots ヒント＋明示半径を付す（従来 display_mode='dots' と同一）。
    pm, lv = True, False
    radius = _POINT_RADIUS
    low, high = res.band_low, res.band_high

    lines: dict[str, object] = {}
    lines["btlm_trail_mean"] = _emit(
        chart, "btlm_trail_mean", times, res.mean, color,
        style="solid", width=2, point_markers=pm, line_visible=lv,
        point_markers_radius=radius,
    )
    # バンド端（下側 q_low・上側 q_high）。既定ドット emit（切替はスタイルタブ・案A）。
    lo_name = _quantile_series_name(q_low)
    hi_name = _quantile_series_name(q_high)
    lines[lo_name] = _emit(chart, lo_name, times, low, _COLOR_BAND,
                           style="dotted", point_markers=pm, line_visible=lv,
                           point_markers_radius=radius)
    lines[hi_name] = _emit(chart, hi_name, times, high, _COLOR_BAND,
                           style="dotted", point_markers=pm, line_visible=lv,
                           point_markers_radius=radius)

    # 外れ値分位ライン（上側 q_out／下側 1-q_out・バンド方式と同一規約・既定オフ）。
    #   q_out 無効（None/範囲外/q_out<=q_high）は compute が off_low/off_high=None＝補助線なし。
    if res.off_low is not None and res.off_high is not None:
        lines["btlm_trail_off_hi"] = _emit(
            chart, "btlm_trail_off_hi", times, res.off_high, _COLOR_OFFSET,
            style="dashed", line_visible=True, point_markers=False,
        )
        lines["btlm_trail_off_lo"] = _emit(
            chart, "btlm_trail_off_lo", times, res.off_low, _COLOR_OFFSET,
            style="dashed", line_visible=True, point_markers=False,
        )

    # 数値表示（β・残差 σ・バンド内実績率）: 読取欄オーバーレイ用の不可視系列として供給する。
    if show_metrics:
        cov = None
        lower = {str(c).lower(): c for c in df.columns}
        if "close" in lower:
            close = df[lower["close"]].to_numpy(dtype=float)
            cov = rolling_coverage(close, low, high, n_cov)
        for name, vals in (
            ("btlm_trail_beta", res.beta),
            ("btlm_trail_sigma", res.sigma),
            ("btlm_trail_band_hit_rate", cov),
        ):
            if vals is None:
                continue
            lines[name] = _emit(
                chart, name, times, vals, _COLOR_METRIC,
                style="solid", width=1, line_visible=False, point_markers=False,
                readout_only=True,
            )
    return lines
