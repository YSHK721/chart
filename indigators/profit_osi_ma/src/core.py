"""層名: core 層（純粋計算）。

責務:
    PRO!fit_OSI_MA の MAKairi（移動平均乖離率）を numpy 配列のみで計算する純粋
    関数層。入出力・描画・pandas を含まない。MA は共有ライブラリ moving_averages
    の on_buffer 系を再利用する。

元 MQL 対応:
    ``PRO!fit_OSI_MA.mq4`` の以下を昇順（古→新）へ変換して 1:1 再現:
        ma = iMA(NULL,0,MAPeriod,0,MAMode,PRICE_CLOSE,i);
        if (ma != 0) MAKairi[i] = (Close[i+1] - ma) / ma * 100;
    → kairi[a] = (close[a-1] - ma_a) / ma_a * 100（close[a-1] は 1 本古い終値）。

依存（PORTING_GUIDE §8）:
    標準: __future__ / 外部: numpy / 共有: moving_averages（on_buffer 系）
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np

# 共有ライブラリ moving_averages を indicators/ パス経由で再利用する。
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))  # = indicators/
from moving_averages import (  # noqa: E402
    exponential_ma_on_buffer,
    linear_weighted_ma_on_buffer,
    simple_ma_on_buffer,
    smoothed_ma_on_buffer,
)

# MAMode → MA 種別（元 MQL の MODE_SMA/EMA/SMMA/LWMA に対応）。
MA_MODES = {0: "SMA", 1: "EMA", 2: "SMMA", 3: "LWMA"}
DEFAULT_MA_MODE = 1   # EMA
DEFAULT_MA_PERIOD = 21

# MAMode → on_buffer 関数の対応。
_MA_BUFFER_FUNCS = {
    0: simple_ma_on_buffer,
    1: exponential_ma_on_buffer,
    2: smoothed_ma_on_buffer,
    3: linear_weighted_ma_on_buffer,
}


def compute_osi_ma(close, *, ma_mode: int = 1, ma_period: int = 21):
    """終値 MA からの乖離率 MAKairi を昇順 numpy 配列で返す。

    kairi[a] = (close[a-1] - ma_a) / ma_a * 100。NaN 条件: a==0 /
    ma_a==0 / ma_a が NaN（MA 未確定）。

    Args:
        close: 終値配列（昇順, 古→新）。
        ma_mode: MA 種別（0=SMA,1=EMA,2=SMMA,3=LWMA）。
        ma_period: MA 期間（>0）。

    Returns:
        kairi 配列（close と同長, float64）。

    Raises:
        ValueError: ``ma_mode`` が 0〜3 外、または ``ma_period<=0``。
    """
    if ma_mode not in _MA_BUFFER_FUNCS:
        raise ValueError(f"未知の MAMode: {ma_mode}（許容: 0..3）")
    if ma_period <= 0:
        raise ValueError(f"ma_period は正である必要があります: {ma_period}")

    close = np.asarray(close, dtype=np.float64)
    n = close.shape[0]
    if n == 0:
        return np.empty(0, dtype=np.float64)

    # --- MA バッファを共有 on_buffer で計算。
    #     未確定区間の扱いは MA 種別で異なる: SMA/SMMA/LWMA は 0.0 が入り、
    #     EMA は seed=price[begin] から全区間に非ゼロ値が入る。いずれも
    #     下の ma==0 / NaN ガードで未確定・ゼロ除算を NaN へ落とす。
    ma = np.full(n, np.nan, dtype=np.float64)
    _MA_BUFFER_FUNCS[ma_mode](n, 0, 0, ma_period, close, ma)

    # --- 乖離率: kairi[a] = (close[a-1] - ma_a) / ma_a * 100。
    kairi = np.full(n, np.nan, dtype=np.float64)
    for a in range(1, n):  # a==0 は close[a-1] 不在で NaN（スキップ）。
        ma_a = ma[a]
        if ma_a == 0.0 or np.isnan(ma_a):  # ゼロ除算ガード / MA 未確定。
            continue
        kairi[a] = (close[a - 1] - ma_a) / ma_a * 100.0
    return kairi
