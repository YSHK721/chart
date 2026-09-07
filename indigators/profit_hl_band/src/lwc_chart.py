"""層名: 出力アダプタ（lightweight-charts への系列追加・duck typing）。

責務:
    出力アダプタ。``lightweight_charts`` を import せず、``horizontal_line`` を持つ
    オブジェクト（chart）をダックタイピングで受ける（PORTING_GUIDE §2/§6）。指標
    パッケージの依存を numpy/pandas に保つ。本指標は元 ``indicator_chart_window``
    （メインチャート重畳）であり、separate ウィンドウ・ヒストグラム・プロット用
    バッファを持たない overlay 専用指標である。メイン chart へ価格軸の水平バンド線
    8 本（up_*/dn_*）のみを追加する。価格系列（close 等）の描画は呼び出し側の前提
    （本関数はバンドのみ追加する）。

元 MQL4 対応（``PRO!fit_HLBand.mq4`` chart_window / OBJ_TREND 8 本）:
    L220-227 StdDevArray[1..8] = iClose(1) ± iBandsOnArray(...)
        → up_*（加算）/ dn_*（減算）の 8 価格値（hl_band_levels が算出）。
    L238-246 ObjectCreate(..., OBJ_TREND, 0, Time[1], StdDev[k], Time[0], StdDev[k]) ×8
        → メイン chart の水平線 8 本（実質水平な OBJ_TREND を horizontal_line で再表現）。
    L249-252 ObjectSetInteger(..., OBJPROP_COLOR, LimeGreen)
        → 水平線色（既定 LimeGreen）。
    MT4 描画オブジェクト（ObjectCreate / ObjectDelete）のライフサイクルは移植対象外
    （SPEC §2）。投影値 8 本の「意味」のみを水平線として移す。

    値（price）とライン名（text）を完全一致させる（ガイド §5）。多数の水平線は
    price_line=False / price_label=False（ガイド §6）。具体描画ライブラリを core/
    成果物層へ侵入させない。

依存（PORTING_GUIDE §8）:
    標準: __future__, typing / 外部: pandas / プロジェクト内: hl_band
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

import pandas as pd

from common_view import LEVEL_LINE_WIDTH  # noqa: E402

# 時刻解決の規則は共有アダプタが単一所有する（ISSUE-502 段階 2 D-7）。旧・自前実装は共有版と
#   AST 一致（``c.lower()`` 版）だったが、公開入口 ``add_hl_band`` では ``hl_band_levels`` が
#   先に ``c.lower()`` で列を照合するため、非 str 列名は本関数へ到達しない（挙動不変）。
from common_view.lwc_adapter import resolve_times as _resolve_times  # noqa: E402

from .core import DEFAULT_WINDOW
from .hl_band import hl_band_levels

# 元 OBJPROP_COLOR LimeGreen（OBJ_TREND 8 本の色）。
_BAND_COLOR = "rgba(50, 205, 50, 1)"

# overlay の水平線 8 本（上側 4 = 加算 / 下側 4 = 減算）。元 StdDevArray[1..8]。
_BAND_KEYS: tuple[str, ...] = (
    "up_067",
    "up_165",
    "up_196",
    "up_258",
    "dn_067",
    "dn_165",
    "dn_196",
    "dn_258",
)


@runtime_checkable
class _Chart(Protocol):
    def horizontal_line(self, price: float, **kwargs): ...


def add_hl_band(
    chart: _Chart,
    df: pd.DataFrame,
    *,
    time_column: str | None = None,
    color: str = _BAND_COLOR,
    window: int | None = DEFAULT_WINDOW,
    normalize: bool = True,
) -> list:
    """メインチャートに HL バンドの水平線 8 本（up_*/dn_*）を追加する。

    元 ``ObjectCreate(..., OBJ_TREND, ...)`` 8 本（LimeGreen）を水平線で再表現する
    （MT4 描画オブジェクト自体は移植対象外・SPEC §2、close[-2] への ±band 投影値
    8 本のみを移す）。価格系列の描画は呼び出し側前提（本関数はバンドのみ追加）。
    多数線のため axis_label_visible=False（ガイド §6。実 horizontal_line API 準拠）。

    Args:
        chart: ``horizontal_line(price, **kwargs)`` を持つメインチャート（duck typing）。
        df: high/low/close を含む DataFrame（列名大小不問）。
        time_column: 時刻列の明示指定（省略時は time/date/DatetimeIndex を探索）。
        color: 水平線色（既定 LimeGreen）。

    Returns:
        生成した水平線オブジェクトのリスト（8 本・_BAND_KEYS 順）。

    Raises:
        KeyError: high/low/close 列が無い / 時刻が解決できない場合。
        ValueError: N<2（close[-2] 不在）の場合（成果物層ガード）。
    """
    levels = hl_band_levels(
        df, window=window, normalize=normalize
    )  # high/low/close 欠落 KeyError・N<2 ValueError はここで送出
    _resolve_times(df, time_column)  # 時刻解決不可は KeyError（先例 profit_hlband 準拠）
    created: list = []
    if not levels.get("available", True):
        return created  # 有効本数 < 2: バンド線を描画しない（levels は NaN）
    for key in _BAND_KEYS:
        created.append(
            chart.horizontal_line(
                price=float(levels[key]),
                color=color,
                width=LEVEL_LINE_WIDTH,
                style="solid",
                text=key,
                axis_label_visible=False,
            )
        )
    return created
