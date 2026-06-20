"""TradeMarkerPresenterPort（確定トレードのマーカー表現を出力する境界）。

設計入力: CHART_TRADE_MARKERS_DETAILED_DESIGN.md §2.1、
  CHART_TRADE_MARKERS_BASIC_DESIGN.md §5.1（ISP: ReportPresenterPort とは別 Port）。

usecase 層の境界 Port。domain のみに依存し、フレームワーク型（pandas/lwc 等）を
シグネチャに露出しない（戻り値 None・副作用は path への書き出し）。

ports.py は無改変（C3）。新 Port は本ファイルに分離する。
"""
from __future__ import annotations

import abc
from typing import Any


class TradeMarkerPresenterPort(abc.ABC):
    """確定トレードのマーカー表現を出力する境界（ReportPresenterPort とは別 Port＝ISP）。"""

    @abc.abstractmethod
    def present_markers(
        self, result: Any, path: Any, *, symbol: Any, ea_name: Any
    ) -> None:
        """result.trades → Marker JSON を path へ書き出す。

        symbol/ea_name は引数注入（_present_outputs の setattr 非依存＝M-1）。
        """
        raise NotImplementedError
