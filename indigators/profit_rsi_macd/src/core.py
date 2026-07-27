"""層名: core 層（純粋計算）。

責務:
    PRO!fitRSIMACD の price=Typical → iRSI(RSIperiod) → EMA(FastEMA)/EMA(SlowEMA)
    → macd=fast-slow → signal=EMA(macd,SignalEMA) → histogram=2.618*(macd-signal)
    → σ7水準（histogram 全系列・母σ÷N）を numpy 配列のみで行う純粋関数層。
    入出力・描画・pandas を含まない。iRSI（権威 Wilder）と σ統計は本パッケージ内に
    閉じる（in-package 確定）。EMA 平滑は共有 moving_averages を、適用価格（Typical）は
    共有 common を再利用する（in-package 再実装はしない）。

含む構造:
    compute_rsi           : 昇順 価格系列から iRSI 系列（warm-up 0）を算出。
        共有 mql_builtins の compute_rsi を再公開（権威 Wilder・flat→50・是正済み）。
    compute_rsimacd       : Typical 価格 → iRSI → fast/slow EMA → macd → signal EMA
        → histogram(2.618 係数) → σ7水準 を統合した frozen DTO を返す。
    compute_rsimacd_levels: histogram（係数適用後）全系列の avg ± 1/2/3σ
        （母σ・÷N）＋ mid50=50。
    RsiMacdResult         : 計算成果の不変 DTO（rsi/fast/slow/macd/signal/
        histogram は writeable=False, levels）。中間 rsi/fast/slow も保持。

元 MQL 対応（``PRO!fitRSIMACD.mq4`` を昇順=古→新へ 1:1 変換）:
    PRICE_TYPICAL 固定            → typical_price（共有 common 再利用）。
    iRSI(RSIperiod=13)            → compute_rsi（profit_rsi と同一ロジック・権威 Wilder）。
    iMAOnArray(EMA, FastEMA=4)    → moving_averages.ma(..., "ema", ...)（共有再利用）。
    iMAOnArray(EMA, SlowEMA=8)    → moving_averages.ma(..., "ema", ...)（共有再利用）。
    MACD = fast - slow            → macd[i] = fast[i] - slow[i]。
    Signal = iMAOnArray(MACD,EMA,SignalEMA=4) → moving_averages.ma(..., "ema", ...)。
    Histogram = 2.618*(MACD-Signal) → histogram[i] = 2.618*(macd[i]-signal[i])。
    σ7水準（iBandsOnArray 相当・全系列）→ compute_rsimacd_levels。中心=全平均、
        偏差=母標準偏差（÷N・warm-up 0 込み）。histogram（=2.618 適用後）に掛かる。

依存（PORTING_GUIDE §8）:
    標準: __future__, dataclasses, sys, pathlib / 外部: numpy
    共有: moving_averages（ma）, common（typical_price）。
    pandas/描画 import は禁止。
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

# 共有ライブラリ moving_averages / mql_builtins（indigators/ 直下）を絶対 import で再利用する。
from moving_averages import ma
from mql_builtins import compute_rsi  # noqa: F401  # 正準 iRSI（再公開して in-package 参照面を維持）

from common import typical_price

# 元 extern の既定値（PRO!fitRSIMACD.mq4: RSIperiod=13, Fast=4, Slow=8, Signal=4）。
DEFAULT_RSI_PERIOD: int = 13
DEFAULT_FAST_EMA: int = 4
DEFAULT_SLOW_EMA: int = 8
DEFAULT_SIGNAL_EMA: int = 4

# Histogram 係数（元 MQL ``Histogram = 2.618*(MACD-Signal)``）。
_HIST_COEFFICIENT: float = 2.618


# compute_rsi（iRSI Wilder）は共有 mql_builtins へ集約済み（上部で import・再公開）。
# 既定 period 定数 DEFAULT_RSI_PERIOD は本パッケージに残置し、呼び出しで period= 明示する。


def compute_rsimacd_levels(histogram: np.ndarray) -> dict[str, float]:
    """histogram（係数適用後）全系列の avg ± 1/2/3σ（母σ・÷N）＋ mid50=50 を返す。

    中心は全系列平均、偏差は母標準偏差（÷N）。**warm-up の 0 を除外せず全系列で
    算出する**（元挙動の 1:1 再現）。σ/avg は histogram（=2.618 適用後）に掛かる。

    Args:
        histogram: histogram 系列（warm-up 0 を含む全系列・2.618 適用後）。

    Returns:
        ``{"p1","p2","p3","m1","m2","m3","mid50"}``::

            p1=avg+σ, p2=avg+2σ, p3=avg+3σ
            m1=avg-σ, m2=avg-2σ, m3=avg-3σ, mid50=50.0
    """
    x = np.asarray(histogram, dtype=np.float64)
    avg = float(np.mean(x))
    # 母標準偏差（÷N）。元 iStdDevOnArray(MODE_SMA) は MT4 標準の population
    # 標準偏差（sqrt(Σ(x-ma)²/period)・÷N）であり ÷(N-1) の不偏推定ではない。
    sigma = float(np.sqrt(np.mean((x - avg) ** 2)))
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
class RsiMacdResult:
    """PRO!fitRSIMACD の計算成果（数値のみ・描画非依存の不変 DTO）。

    中間 rsi/fast/slow も検証用に保持する（描画には histogram/macd/signal のみ
    使用する想定だが、1:1 再現検証のため全段を露出する）。

    Attributes:
        rsi: iRSI 系列（warm-up 0。writeable=False）。
        fast: iRSI の EMA(FastEMA) 系列（writeable=False）。
        slow: iRSI の EMA(SlowEMA) 系列（writeable=False）。
        macd: fast - slow（writeable=False）。
        signal: EMA(macd, SignalEMA)（writeable=False）。
        histogram: 2.618 * (macd - signal)（writeable=False）。
        levels: σ 水準辞書（p1/p2/p3/m1/m2/m3/mid50 の 7 要素）。
    """

    rsi: np.ndarray
    fast: np.ndarray
    slow: np.ndarray
    macd: np.ndarray
    signal: np.ndarray
    histogram: np.ndarray
    levels: dict[str, float]

    def __post_init__(self) -> None:
        for name in ("rsi", "fast", "slow", "macd", "signal", "histogram"):
            arr = np.asarray(getattr(self, name), dtype=np.float64)
            arr.setflags(write=False)  # DTO は不変（profit_rsi/profit_mfi_macd 準拠）
            object.__setattr__(self, name, arr)


def compute_rsimacd(
    open_: np.ndarray,
    high: np.ndarray,
    low: np.ndarray,
    close: np.ndarray,
    *,
    rsi_period: int = DEFAULT_RSI_PERIOD,
    fast: int = DEFAULT_FAST_EMA,
    slow: int = DEFAULT_SLOW_EMA,
    signal: int = DEFAULT_SIGNAL_EMA,
) -> RsiMacdResult:
    """Typical 価格 → iRSI → fast/slow EMA → macd → signal EMA → histogram → σ7水準。

    計算順序（元 MQL の 1:1 再現）::

        1. price = typical_price(high, low, close) = (high+low+close)/3
           rsi = compute_rsi(price, period=rsi_period)
        2. fast = EMA(rsi, fast) ; slow = EMA(rsi, slow)   # 共有 on_buffer
        3. macd[i] = fast[i] - slow[i]
        4. signal = EMA(macd, signal)
        5. histogram[i] = 2.618 * (macd[i] - signal[i])
        6. σ7水準 = compute_rsimacd_levels(histogram)      # 係数適用後・母σ÷N

    ``open_`` は引数 OHLC 契約の対称性のために受け取るが、PRICE_TYPICAL 固定のため
    計算には使用しない（長さ整合チェックには使用する）。

    Args:
        open_/high/low/close: 昇順 OHLC（同長）。open は計算に未使用（Typical 固定）。
        rsi_period: RSI 期間（既定 13, >=2）。
        fast: FastEMA 期間（既定 4）。
        slow: SlowEMA 期間（既定 8）。
        signal: SignalEMA 期間（既定 4）。

    Returns:
        RsiMacdResult（rsi/fast/slow/macd/signal/histogram/levels(7 要素)）。

    Raises:
        ValueError: ``rsi_period < 2``、または OHLC 長不一致。
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

    price = typical_price(high, low, close)  # PRICE_TYPICAL 固定（共有 common）
    rsi = compute_rsi(price, period=rsi_period)

    fast_buf = ma(rsi, "ema", fast)
    slow_buf = ma(rsi, "ema", slow)

    macd = fast_buf - slow_buf

    signal_buf = ma(macd, "ema", signal)

    histogram = _HIST_COEFFICIENT * (macd - signal_buf)

    levels = compute_rsimacd_levels(histogram)
    return RsiMacdResult(
        rsi=rsi,
        fast=fast_buf,
        slow=slow_buf,
        macd=macd,
        signal=signal_buf,
        histogram=histogram,
        levels=levels,
    )
