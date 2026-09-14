"""mql_builtins — MetaTrader 組込指標（iRSI/iMFI/iWPR/iStochastic）の共有実装。

入出力・描画を含まない純粋な数値計算ライブラリ（依存は numpy のみ）。
profit_rsi / profit_mfi / profit_rmm / profit_stc などが重複保持していた
compute_rsi / compute_mfi / compute_wpr / compute_stochastic を 1:1 で集約した
正準実装。各関数の ``period`` はキーワード必須（既定値は各 core 側に残置）。

公開 API:
    compute_rsi        : iRSI（Wilder RSI・flat→50）
    compute_mfi        : iMFI（負MF==0→100）
    compute_wpr        : iWPR（flat→前値・warm-up i<period-1）
    compute_stochastic : iStochastic 生 %K（fast, MODE_MAIN）

典型的な使い方:
    >>> import numpy as np
    >>> from mql_builtins import compute_rsi
    >>> rsi = compute_rsi(np.array([1.0, 2.0, 3.0, 2.0, 1.0]), period=2)
"""

from __future__ import annotations

from .core import (
    compute_mfi,
    compute_rsi,
    compute_rsi_stateful,
    RsiState,
    compute_stochastic,
    compute_wpr,
)

__all__ = [
    "compute_rsi",
    "compute_rsi_stateful",
    "RsiState",
    "compute_mfi",
    "compute_wpr",
    "compute_stochastic",
]
