"""層名: core 層（純粋計算）。

責務:
    PRO!fitSTC（実体 PRO!fitOscillator）の計算を numpy 配列のみで行う純粋関数層。
    入出力・描画・pandas を含まない。新規プリミティブ（Stochastic %K・σ帯）は
    本パッケージ内に閉じるが、描画・pandas 非依存の独立関数として保ち、将来
    common/ へシグネチャ不変で昇格可能な形にする（依頼 YAGNI 確定）。

含む構造:
    compute_stochastic : 直近 period 本の高安レンジに対する終値位置 %K（生・fast）。
    compute_osc_levels : オシレーター全系列の Bollinger 水準（平均 ± dev×母σ）。
    compute_stc        : 上記を統合した frozen DTO（StcResult）を返す。
    StcResult          : 計算成果の不変 DTO（oscillator/levels/sub_min/sub_max）。

元 MQL 対応（``PRO!fitSTC.mq4`` を昇順=古→新へ 1:1 変換）:
    L104 ExtBufferOscillator[i] =
        iStochastic(NULL,0,inpPeriodOscillator,1,1,MODE_EMA,0,MODE_MAIN,i)
        → compute_stochastic / StcResult.oscillator。
        slowing=1 / Dperiod=1 のため MODE_EMA 平滑は恒等（vestigial・平滑非実装）。
        price_field=0 は Low/High でレンジを取る。warm-up（i<period-1）は元 iStochastic
        既定どおり 0（NaN ではない）。ゼロ割（HH==LL）も 0（spec 確定）。
    L108-111 iBandsOnArray(osc, 0, rates_total, dev, 0, mode, 0)
        → compute_osc_levels。period=rates_total はバンド期間＝全系列 N。
        中心=全平均（warm-up の 0 込み）、偏差=母標準偏差（÷N・warm-up の 0 込み）。
        P1/P2=MODE_UPPER(dev=1.00/1.96)、M1/M2=MODE_LOWER(dev=1.00/1.96)。
    L117-118 IndicatorSetDouble(INDICATOR_MINIMUM=StdDev[4]=M2,
                                INDICATOR_MAXIMUM=StdDev[2]=P2)
        → sub_min=M2, sub_max=P2。

依存（PORTING_GUIDE §8）:
    標準: __future__, dataclasses / 外部: numpy のみ
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

# 共有ライブラリ mql_builtins（indigators/ 直下）を絶対 import で再利用する。
from mql_builtins import compute_stochastic  # noqa: F401  # 正準 iStochastic 生 %K（再公開して in-package 参照面を維持）

# 元 input inpPeriodOscillator の既定値（PRO!fitSTC.mq4 L25）。
DEFAULT_PERIOD: int = 70

# iBandsOnArray の deviation 引数（L108-111）。
_DEV_1: float = 1.00
_DEV_196: float = 1.96

# compute_stochastic（iStochastic 生 %K）は共有 mql_builtins へ集約済み（上部で import・再公開）。
# 既定 period 定数 DEFAULT_PERIOD は本パッケージに残置し、呼び出しで period= 明示する。


def compute_osc_levels(oscillator: np.ndarray) -> dict[str, float]:
    """オシレーター全系列の Bollinger 水準（平均 ± dev×母σ）を返す。

    元 ``iBandsOnArray(osc, 0, rates_total, dev, 0, mode, 0)`` 相当。中心は全系列
    平均、偏差は母標準偏差（÷N）。**warm-up の 0 を除外せず全系列で算出する**
    （元挙動の 1:1 再現。除外は禁止）。

    Args:
        oscillator: %K 系列（warm-up の 0 を含む全系列）。

    Returns:
        ``{"P1", "P2", "M1", "M2"}``::

            P1 = mean + 1.00*std,  P2 = mean + 1.96*std
            M1 = mean - 1.00*std,  M2 = mean - 1.96*std
    """
    x = np.asarray(oscillator, dtype=np.float64)
    mean = float(np.mean(x))
    std = float(np.sqrt(np.mean((x - mean) ** 2)))  # 母標準偏差（÷N, MT4 iBands 準拠）
    return {
        "P1": mean + _DEV_1 * std,
        "P2": mean + _DEV_196 * std,
        "M1": mean - _DEV_1 * std,
        "M2": mean - _DEV_196 * std,
    }


@dataclass(frozen=True)
class StcResult:
    """PRO!fitSTC の計算成果（数値のみ・描画非依存の不変 DTO）。

    Attributes:
        oscillator: 生 %K 系列（描画対象, N,。warm-up は 0）。
        levels: σ 水準辞書（P1/P2/M1/M2）。
        sub_min: 別ウィンドウ下限（= M2。元 INDICATOR_MINIMUM）。
        sub_max: 別ウィンドウ上限（= P2。元 INDICATOR_MAXIMUM）。
    """

    oscillator: np.ndarray
    levels: dict[str, float]
    sub_min: float
    sub_max: float

    def __post_init__(self) -> None:
        arr = np.asarray(self.oscillator, dtype=np.float64)
        arr.setflags(write=False)  # DTO は不変（profit_adx_needle 準拠）
        object.__setattr__(self, "oscillator", arr)


def compute_stc(
    high: np.ndarray,
    low: np.ndarray,
    close: np.ndarray,
    *,
    period: int = DEFAULT_PERIOD,
) -> StcResult:
    """%K と σ 水準を統合し StcResult（frozen DTO）として返す。

    元 OnCalculate の全体（オシレーター算出 → iBandsOnArray 4 本 →
    INDICATOR_MINIMUM/MAXIMUM 設定）を再現する。

    Args:
        high/low/close: OHLC の高値/安値/終値（昇順・同長）。
        period: 期間（既定 70。元 inpPeriodOscillator）。

    Returns:
        StcResult（oscillator / levels(P1/P2/M1/M2) / sub_min(=M2) / sub_max(=P2)）。

    Raises:
        ValueError: ``period < 2`` または high/low/close 長不一致。
    """
    oscillator = compute_stochastic(high, low, close, period=period)
    levels = compute_osc_levels(oscillator)
    return StcResult(
        oscillator=oscillator,
        levels=levels,
        sub_min=levels["M2"],
        sub_max=levels["P2"],
    )
