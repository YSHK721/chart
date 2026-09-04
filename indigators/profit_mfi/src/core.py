"""層名: core 層（純粋計算）。

責務:
    PRO!fitMFI の iMFI（Money Flow Index）算出・EMA 平滑・σ 水準算出を numpy
    配列のみで行う純粋関数層。入出力・描画・pandas を含まない。新規プリミティブ
    iMFI と σ 統計は本パッケージ内に閉じる（in-package 確定）。EMA 平滑は共有
    moving_averages を再利用する（in-package 再実装はしない）。

含む構造:
    compute_mfi        : 昇順 OHLCV から iMFI 系列（warm-up 0）を算出。
    compute_mfi_levels : EMA 系列全体の avg ± 1/2/3σ（母σ・÷N）＋ mid50=50。
    compute_mfi_full   : iMFI ＋ EMA 平滑 ＋ σ 水準を統合した frozen DTO。
    MfiResult          : 計算成果の不変 DTO（mfi/ma writeable=False, levels）。

元 MQL 対応（``PRO!fitMFI.mq4`` を昇順=古→新へ 1:1 変換）:
    iMFI(period)         → compute_mfi。TP=(H+L+C)/3, MF=TP*Volume。窓内で
        TP[j]>TP[j-1]→正MF, TP[j]<TP[j-1]→負MF, 等しければ加算しない（§4.4 非対称）。
        MFI=100*正MF/(正MF+負MF)。warm-up（i<period）は 0（元 iMFI/SetIndexDrawBegin）。
    iMAOnArray(EMA, ma_period) → moving_averages.ma(..., "ema", ...)（共有再利用。
        中身は exponential_ma_on_buffer と bit 等価）。warm-up 0 を含めて通す
        （元 iMAOnArray と同じ）。ma_period<=1 は未計算 0 返し。
    σ 水準（全系列 iBandsOnArray 相当） → compute_mfi_levels。中心=全平均、
        偏差=母標準偏差（÷N・warm-up 0 込み）。

依存（PORTING_GUIDE §8）:
    標準: __future__, dataclasses, sys, pathlib / 外部: numpy
    共有: moving_averages（ma）。pandas/描画 import は禁止。
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

# 共有ライブラリ moving_averages / mql_builtins（indigators/ 直下）を絶対 import で再利用する。
from moving_averages import ma
from mql_builtins import compute_mfi  # noqa: F401  # 正準 iMFI（再公開して in-package 参照面を維持）

# 元 input の既定値（PORTING_GUIDE / 依頼仕様）。
DEFAULT_MFI_PERIOD: int = 14
DEFAULT_MA_PERIOD: int = 5

# compute_mfi（iMFI）は共有 mql_builtins へ集約済み（上部で import・再公開）。
# 既定 period 定数 DEFAULT_MFI_PERIOD は本パッケージに残置し、呼び出しで period= 明示する。


def compute_mfi_levels(ma_series: np.ndarray) -> dict[str, float]:
    """EMA 系列全体の avg ± 1/2/3σ（母σ・÷N）＋ mid50=50 を返す。

    中心は全系列平均、偏差は母標準偏差（÷N）。**warm-up の 0 を除外せず全系列で
    算出する**（元挙動の 1:1 再現）。

    Args:
        ma_series: EMA 系列（warm-up 0 を含む全系列）。

    Returns:
        ``{"p1","p2","p3","m1","m2","m3","mid50"}``::

            p1=avg+σ, p2=avg+2σ, p3=avg+3σ
            m1=avg-σ, m2=avg-2σ, m3=avg-3σ, mid50=50.0
    """
    x = np.asarray(ma_series, dtype=np.float64)
    avg = float(np.mean(x))
    sigma = float(np.sqrt(np.mean((x - avg) ** 2)))  # 母標準偏差（÷N）
    return {
        "p1": avg + sigma,
        "p2": avg + 2.0 * sigma,
        "p3": avg + 3.0 * sigma,
        "m1": avg - sigma,
        "m2": avg - 2.0 * sigma,
        "m3": avg - 3.0 * sigma,
        "mid50": 50.0,
    }


@dataclass(frozen=True)
class MfiResult:
    """PRO!fitMFI の計算成果（数値のみ・描画非依存の不変 DTO）。

    Attributes:
        mfi: iMFI 系列（warm-up 0。writeable=False）。
        ma: iMFI の EMA 平滑系列（writeable=False）。
        levels: σ 水準辞書（p1/p2/p3/m1/m2/m3/mid50 の 7 要素）。
    """

    mfi: np.ndarray
    ma: np.ndarray
    levels: dict[str, float]

    def __post_init__(self) -> None:
        mfi = np.asarray(self.mfi, dtype=np.float64)
        ma_arr = np.asarray(self.ma, dtype=np.float64)
        mfi.setflags(write=False)  # DTO は不変（profit_stc 準拠）
        ma_arr.setflags(write=False)
        object.__setattr__(self, "mfi", mfi)
        object.__setattr__(self, "ma", ma_arr)


def compute_mfi_full(
    high: np.ndarray,
    low: np.ndarray,
    close: np.ndarray,
    volume: np.ndarray,
    *,
    mfi_period: int = DEFAULT_MFI_PERIOD,
    ma_period: int = DEFAULT_MA_PERIOD,
) -> MfiResult:
    """iMFI ＋ EMA 平滑 ＋ σ 水準を統合し MfiResult（frozen DTO）として返す。

    iMFI を ``compute_mfi`` で算出し、その出力（warm-up 0 込み）を共有
    ``ma(..., "ema", ma_period)`` で EMA(ma_period) 化する。σ 水準は EMA 系列
    全体から ``compute_mfi_levels`` で算出する。

    Args:
        high/low/close/volume: 昇順 OHLCV（同長）。
        mfi_period: MFI 期間（既定 14）。
        ma_period: EMA 期間（既定 5）。ma_period<=1 は共有関数の挙動に従い
            未計算（buffer は 0 のまま）。

    Returns:
        MfiResult（mfi / ma / levels(7 要素)）。

    Raises:
        ValueError: ``mfi_period < 2`` または OHLCV 長不一致（compute_mfi 経由）。
    """
    mfi = compute_mfi(high, low, close, volume, period=mfi_period)
    ma_values = ma(mfi, "ema", ma_period)
    levels = compute_mfi_levels(ma_values)
    return MfiResult(mfi=mfi, ma=ma_values, levels=levels)
