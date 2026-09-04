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

    ⚠ 回帰トレンド系（``tickvol_trend_*``）は ISSUE-244 で **UI から外した**。計算は
    :mod:`.trend` にアーカイブとして残っている（結線からは呼ばれない）。

    ``_evq_{med|ext}_{hi|lo}`` の命名・色・線種は表示仕様層の共有規約
    （:mod:`common_view.event_quantile_view` の ``EVQ_LINE_SPECS`` / ``EVQ_COLOR``）に従う。下側
    （``_lo``）は持たない: tickvol は 1 tick 以上でしか足が立たない計数量で下側は裾でない
    （実測 min=1・0 の足は 0 本）。GPD 線は経験的線と**並べて読む**ことが目的なので、
    共有の外れ値色をそのまま使うと 2 本が区別できない。別色を本モジュールに置き、表示仕様層の
    共有定数（common_view 側の ``EVQ_COLOR``）は書き換えない（他 2 指標へ非波及・ISSUE-223 と同規律）。

呼出規約:
    ``add_tickvol(chart, df)``。API 経路（``adapter.compute.call_binding``）は df 以降を
    キーワード専用（kind="kw"）で渡す。

依存:
    標準: __future__, typing / 外部: pandas / 共有: common_view.lwc_adapter・
    common.event_quantiles / プロジェクト内: .core, .levels
"""

from __future__ import annotations

from typing import Optional, Protocol, runtime_checkable

import pandas as pd

from common.event_quantiles import DEFAULT_K_EVENTS, DEFAULT_Q_OUT
from common_view.event_quantile_view import EVQ_COLOR, EVQ_LINE_SPECS
from common_view.lwc_adapter import SeriesLike  # noqa: E402
from common_view.lwc_adapter import emit_line as _emit_line  # noqa: E402
from common_view.lwc_adapter import resolve_times as _resolve_times  # noqa: E402

from .core import TICKVOL_COLUMN, build_tickvol
from .levels import DEFAULT_Q_HIGH, DEFAULT_Q_LOW, DEFAULT_WINDOW_N, tickvol_levels

# 既定色。暗背景（チャート #131722）で価格系列と competing しない中間色（Blue Grey）。
_COLOR = "rgba(120, 144, 156, 0.85)"
# GPD 外挿線の色。経験的線（EVQ_COLOR＝赤系）と並べて読むための別色（琥珀系）。
_GPD_COLOR = "rgba(255, 167, 38, 1)"
# 正常帯（分位バンド）の色・線種。MAROD 系の _COLOR_QUANTILE と同値（指標間で同じ意味の
#   線は同じ見た目にする）。
_QUANTILE_COLOR = "rgba(38, 198, 218, 1)"

# 共有の表示規約から線種を引く（med=実線 / ext=破線）。規約の単一情報源は event_quantiles。
_LINE_STYLE = dict(EVQ_LINE_SPECS)


def _quantile_series_name(q: float) -> str:
    """分位 q（0..1）に対応する系列名（例 0.10 -> 'tickvol_q10'）。

    ``btlm_trail_marod/src/lwc_chart._quantile_series_name`` と対称の命名。
    """
    return f"{TICKVOL_COLUMN}_q{int(round(q * 100))}"


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
        time_column: 時刻列の明示指定（省略時は探索）。
        color: ヒストグラムの色。

    Returns:
        追加した系列オブジェクトの list（ヒストグラム 1 + 正常帯 2 + 水準線 3）。

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
    return out
