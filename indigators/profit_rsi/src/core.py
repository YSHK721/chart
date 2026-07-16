"""層名: core 層（純粋計算）。

責務:
    PRO!fitRSI の iRSI（Wilder Relative Strength Index）算出・EMA 平滑・σ 水準算出を
    numpy 配列のみで行う純粋関数層。入出力・描画・pandas を含まない。iRSI は共有
    mql_builtins へ集約済み（import 再公開で in-package 参照面を維持）。σ 統計のみ
    本パッケージ内に閉じる。EMA 平滑は共有 moving_averages を再利用する（in-package
    再実装はしない）。適用価格の選択は共有 common を再利用する。

含む構造:
    compute_rsi        : 昇順 価格系列から iRSI 系列（warm-up 0）を算出（純粋関数）。
    APPLY_TO_PRICE     : 独自 Apply 値 → common.AppliedPrice の写像（core 入口の定数）。
    compute_rsi_levels : **生 RSI 系列** 全体の avg ± 1/2/3σ（母σ・÷N）＋ mid50=50。
    compute_rsi_full   : OHLC ＋ apply を入口に iRSI ＋ EMA 平滑 ＋ σ 水準を統合した frozen DTO。
    RsiResult          : 計算成果の不変 DTO（rsi/ma writeable=False, levels）。

元 MQL 対応（``PRO!fitRSI.mq4`` ＋ 標準 ``iRSI``（``RSI.mq5``）を昇順=古→新へ 1:1 変換）:
    iRSI(period, applied) → compute_rsi。diff=price[i]-price[i-1]。
        seed（i==period）: pos/neg = period 本の up/down 平均。
        main（i>period）  : Wilder 平滑 pos[i]=(pos[i-1]*(period-1)+up)/period。
        RSI: neg!=0 → 100-100/(1+pos/neg); neg==0&pos!=0 → 100; neg==0&pos==0 → 50。
        warm-up（i<period）は 0（元 iRSI/SetIndexDrawBegin 既定）。
        rates_total<=period は全 0（元 RSI.mq5 の早期 return）。
    Apply（独自 input） → APPLY_TO_PRICE で common.AppliedPrice へ写像し
        applied_price(kind, o,h,l,c) で価格系列を選択。既定 Apply=5 → TYPICAL。
    iMAOnArray(EMA, ma_period) → exponential_ma_on_buffer（共有再利用）。warm-up 0
        を含めて通す（元 iMAOnArray と同じ）。ma_period<=1 は未計算 0 返し。
    σ 水準（全系列 iStdDevOnArray 相当）→ compute_rsi_levels（**生 RSI 系列に掛ける**）。
        中心=全平均、偏差=母標準偏差（÷N・warm-up 0 込み）。mid50=50（元の固定 50 水準）。

依存（PORTING_GUIDE §8）:
    標準: __future__, dataclasses, sys, pathlib / 外部: numpy
    共有: moving_averages（exponential_ma_on_buffer）, common（applied_price, AppliedPrice）。
    pandas/描画 import は禁止。
"""

from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path

import numpy as np

# 共有ライブラリ moving_averages / mql_builtins を indicators/ パス経由で再利用する。
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))  # = indicators/
from moving_averages import exponential_ma_on_buffer  # noqa: E402
from mql_builtins import compute_rsi  # noqa: E402,F401  # 正準 iRSI（再公開して in-package 参照面を維持）

from common import AppliedPrice, applied_price  # noqa: E402

# 元 input の既定値（PRO!fitRSI.mq4: InpRSIPeriod=6, InpMAPeriod=5, Apply=5）。
DEFAULT_RSI_PERIOD: int = 6
DEFAULT_MA_PERIOD: int = 5
DEFAULT_APPLY: int = 5  # 5 -> PRICE_TYPICAL（元 input 既定）

# 独自 Apply 値 -> common.AppliedPrice の写像（PRO!fitRSI.mq4 独自仕様）。
# 1:OPEN, 2:HIGH, 3:LOW, 4:MEDIAN, 5:TYPICAL, 6:WEIGHTED, それ以外:CLOSE。
_APPLY_MAP: dict[int, AppliedPrice] = {
    1: AppliedPrice.OPEN,
    2: AppliedPrice.HIGH,
    3: AppliedPrice.LOW,
    4: AppliedPrice.MEDIAN,
    5: AppliedPrice.TYPICAL,
    6: AppliedPrice.WEIGHTED,
}


def APPLY_TO_PRICE(apply: int) -> AppliedPrice:
    """独自 Apply 値を common.AppliedPrice へ写像する（既定外は CLOSE）。"""
    return _APPLY_MAP.get(apply, AppliedPrice.CLOSE)


# compute_rsi（iRSI Wilder）は共有 mql_builtins へ集約済み（上部で import・再公開）。
# 既定 period 定数 DEFAULT_RSI_PERIOD は本パッケージに残置し、呼び出しで period= 明示する。


def compute_rsi_levels(rsi_series: np.ndarray) -> dict[str, float]:
    """**生 RSI 系列** 全体の avg ± 1/2/3σ（母σ・÷N）＋ mid50=50 を返す。

    中心は全系列平均、偏差は母標準偏差（÷N）。**warm-up の 0 を除外せず全系列で
    算出する**（元挙動の 1:1 再現）。mid50 は元の固定 50 水準。σ 水準は EMA 平滑後の
    ma ではなく **生 RSI 系列** に掛ける（PRO!fitRSI 確定仕様）。

    Args:
        rsi_series: 生 RSI 系列（warm-up 0 を含む全系列）。

    Returns:
        ``{"p1","p2","p3","m1","m2","m3","mid50"}``::

            p1=avg+σ, p2=avg+2σ, p3=avg+3σ
            m1=avg-σ, m2=avg-2σ, m3=avg-3σ, mid50=50.0
    """
    x = np.asarray(rsi_series, dtype=np.float64)
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
class RsiResult:
    """PRO!fitRSI の計算成果（数値のみ・描画非依存の不変 DTO）。

    Attributes:
        rsi: iRSI 系列（warm-up 0。writeable=False）。
        ma: iRSI の EMA 平滑系列（writeable=False）。
        levels: σ 水準辞書（p1/p2/p3/m1/m2/m3/mid50 の 7 要素）。
    """

    rsi: np.ndarray
    ma: np.ndarray
    levels: dict[str, float]

    def __post_init__(self) -> None:
        rsi = np.asarray(self.rsi, dtype=np.float64)
        ma = np.asarray(self.ma, dtype=np.float64)
        rsi.setflags(write=False)  # DTO は不変（profit_mfi/profit_stc 準拠）
        ma.setflags(write=False)
        object.__setattr__(self, "rsi", rsi)
        object.__setattr__(self, "ma", ma)


def compute_rsi_full(
    open_: np.ndarray,
    high: np.ndarray,
    low: np.ndarray,
    close: np.ndarray,
    *,
    rsi_period: int = DEFAULT_RSI_PERIOD,
    apply: int = DEFAULT_APPLY,
    ma_period: int = DEFAULT_MA_PERIOD,
) -> RsiResult:
    """OHLC ＋ apply から iRSI ＋ EMA 平滑 ＋ σ 水準を統合し RsiResult を返す。

    ``apply`` を ``APPLY_TO_PRICE`` で common.AppliedPrice へ写像し、共有
    ``applied_price`` で価格系列を選択する。iRSI を ``compute_rsi`` で算出し、その出力
    （warm-up 0 込み）を共有 ``exponential_ma_on_buffer`` で EMA(ma_period) 化する。
    σ 水準は **生 RSI 系列**（ma ではない）全体から ``compute_rsi_levels`` で算出する。

    Args:
        open_/high/low/close: 昇順（古→新）の OHLC 配列（同長）。
        rsi_period: RSI 期間（既定 6）。
        apply: 適用価格選択（既定 5 -> TYPICAL。それ以外 -> CLOSE）。
        ma_period: EMA 期間（既定 5）。ma_period<=1 は共有関数の挙動に従い
            未計算（buffer は 0 のまま）。

    Returns:
        RsiResult（rsi / ma / levels(7 要素・生 RSI 由来)）。

    Raises:
        ValueError: OHLC 長不一致、または ``rsi_period < 2``（compute_rsi 経由）。
    """
    open_ = np.asarray(open_, dtype=np.float64)
    high = np.asarray(high, dtype=np.float64)
    low = np.asarray(low, dtype=np.float64)
    close = np.asarray(close, dtype=np.float64)
    if not (open_.shape == high.shape == low.shape == close.shape):
        raise ValueError(
            f"OHLC の長さが一致しません: "
            f"{open_.shape}/{high.shape}/{low.shape}/{close.shape}"
        )

    kind = APPLY_TO_PRICE(apply)
    price = applied_price(kind, open_, high, low, close)
    rsi = compute_rsi(price, period=rsi_period)
    ma = np.zeros(rsi.shape[0], dtype=np.float64)
    exponential_ma_on_buffer(rsi.shape[0], 0, 0, ma_period, rsi, ma)
    levels = compute_rsi_levels(rsi)  # 生 RSI 系列に掛ける（ma ではない）
    return RsiResult(rsi=rsi, ma=ma, levels=levels)
