"""profit_mfi の公開 API（core 層 ＋ 成果物層 ＋ 入出力アダプタ）。

PRO!fitMFI Python 移植のうち core 層（純粋計算）・成果物層（pandas）・入力アダプタ
（loader）・lightweight-charts 出力アダプタ（lwc_chart）の公開シンボルを再エクスポート
する。matplotlib 出力アダプタ（``src.plot``）は matplotlib 依存のため公開 API から除外
し、``from src.plot import plot_mfi`` で明示 import する（matplotlib 未導入環境でも
``import src`` を壊さないため。先例 profit_stc 準拠）。
"""

from __future__ import annotations

from .core import (
    DEFAULT_MA_PERIOD,
    DEFAULT_MFI_PERIOD,
    MfiResult,
    compute_mfi,
    compute_mfi_full,
    compute_mfi_levels,
)
from .loader import load_ohlcv_csv
from .lwc_chart import add_mfi
from .mfi import (
    MA_COLUMN,
    MFI_COLUMN,
    build_mfi,
    mfi_levels,
)

__all__ = [
    "DEFAULT_MA_PERIOD",
    "DEFAULT_MFI_PERIOD",
    "MfiResult",
    "compute_mfi",
    "compute_mfi_full",
    "compute_mfi_levels",
    "MA_COLUMN",
    "MFI_COLUMN",
    "build_mfi",
    "mfi_levels",
    "load_ohlcv_csv",
    "add_mfi",
]
