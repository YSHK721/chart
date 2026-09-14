"""層名: core 層（純粋計算）。

責務:
    PRO!fitHLBand（高安レンジの母σ帯を最新足の High/L へ投影する overlay 指標）の
    計算を numpy 配列のみで行う純粋関数層。入出力・描画・pandas を含まない。新規
    統計（レンジ平均・母σ・σ帯・H/L 投影）は本パッケージ内に閉じる（in-package
    確定）が、numpy のみの独立関数として保つ。

含む構造:
    compute_range       : range[i] = high[i] - low[i]（全 i・warm-up/NaN なし）。
    compute_range_stats : 全系列の平均・母σ（÷N）と σ 帯（b165/b196/b258）。
    compute_hl_bands    : 最新 H/L（昇順 last）へ σ 帯を投影した overlay 8 本。
    compute_hlband      : 上記を統合した frozen DTO（HLBandResult）。
    RangeStats          : avg/sigma/b165/b196/b258 の不変 DTO。
    HLPriceBands        : overlay 8 本（High 側=減算 / Low 側=加算）の不変 DTO。
    HLBandResult        : range/stats/bands/sub_min/sub_max の統合不変 DTO。

元 MQL 対応（``PRO!fitHLBand.mq4`` を昇順=古→新へ 1:1 変換）:
    L61 ExtVOLBuffer[i] = high[i] - low[i]
        → compute_range。warm-up なし・全 i 定義。
    L67 StDev[0] = iHigh(NULL,0,0) - iMAOnArray(ExtVOLBuffer,0,rates_total,0,MODE_SMA,0)
        → 平均 avg（全系列 SMA, period=rates_total）。
    L68-70 StDev[1..3] = iHigh(NULL,0,0) - iBandsOnArray(...,{1.65,1.96,2.58},0,1,0)
        → High 側 overlay（減算）。iBandsOnArray の中心=全平均・偏差=母σ（÷N）。
    L71 StDev[4] = iLow(NULL,0,0) + iMAOnArray(...)
    L72-74 StDev[5..7] = iLow(NULL,0,0) + iBandsOnArray(...,{1.65,1.96,2.58},0,1,0)
        → Low 側 overlay（加算）。
        iHigh(NULL,0,0)/iLow(NULL,0,0) は shift=0=最新足 → 昇順では high[-1]/low[-1]。
    L102 IndicatorSetDouble(INDICATOR_MINIMUM, 0)              → sub_min = 0.0。
    L103 IndicatorSetDouble(INDICATOR_MAXIMUM, StcLCStdDevArray[2]*2)
        StcLCStdDevArray[2] = iBandsOnArray(...,1.96,0,1,0) = b196 → sub_max = b196*2。

依存（PORTING_GUIDE §8）:
    標準: __future__, dataclasses / 外部: numpy のみ
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

# iBandsOnArray の deviation 引数（L68-70, L72-74）。
_DEV_165: float = 1.65
_DEV_196: float = 1.96
_DEV_258: float = 2.58


def compute_range(high: np.ndarray, low: np.ndarray) -> np.ndarray:
    """``range[i] = high[i] - low[i]`` を全 i で返す（warm-up/NaN なし）。

    元 ``ExtVOLBuffer[i] = high[i] - low[i]``（L61）に対応する。

    Args:
        high: 高値配列（昇順, 古→新）。
        low: 安値配列（昇順, 同長）。

    Returns:
        レンジ配列（入力と同長, float64）。

    Raises:
        ValueError: high/low の長さが一致しない。
    """
    high = np.asarray(high, dtype=np.float64)
    low = np.asarray(low, dtype=np.float64)
    if high.shape != low.shape:
        raise ValueError(
            f"high/low の長さが一致しません: {high.shape}/{low.shape}"
        )
    return high - low


@dataclass(frozen=True)
class RangeStats:
    """レンジ系列の全系列統計（平均・母σ・σ 帯）の不変 DTO。

    Attributes:
        avg: 全系列 SMA（``np.mean(range_)``。元 iMAOnArray）。
        sigma: 母標準偏差（÷N。``np.sqrt(np.mean((range_-avg)**2))``。元 iBandsOnArray）。
        b165: ``avg + 1.65*sigma``。
        b196: ``avg + 1.96*sigma``。
        b258: ``avg + 2.58*sigma``。
    """

    avg: float
    sigma: float
    b165: float
    b196: float
    b258: float


def compute_range_stats(range_: np.ndarray) -> RangeStats:
    """レンジ系列の平均・母σ（÷N）と σ 帯を RangeStats として返す。

    元 ``iMAOnArray(..., MODE_SMA)`` の中心（全系列平均）と
    ``iBandsOnArray(..., dev, 0, 1, 0)`` の母標準偏差（÷N, 全系列）に対応する。

    Args:
        range_: レンジ系列（全系列）。

    Returns:
        RangeStats（avg/sigma/b165/b196/b258）。
    """
    x = np.asarray(range_, dtype=np.float64)
    avg = float(np.mean(x))
    sigma = float(np.sqrt(np.mean((x - avg) ** 2)))  # 母σ（÷N, MT4 iBands 準拠）
    return RangeStats(
        avg=avg,
        sigma=sigma,
        b165=avg + _DEV_165 * sigma,
        b196=avg + _DEV_196 * sigma,
        b258=avg + _DEV_258 * sigma,
    )


@dataclass(frozen=True)
class HLPriceBands:
    """最新 H/L へ σ 帯を投影した overlay 8 本の不変 DTO。

    High 側は最新 High からの減算（下へ投影）、Low 側は最新 Low への加算
    （上へ投影）。元 ``iHigh(NULL,0,0)-...`` / ``iLow(NULL,0,0)+...``（L67-74）。

    Attributes:
        high_avg: ``H_last - avg``。
        high_b165: ``H_last - b165``。
        high_b196: ``H_last - b196``。
        high_b258: ``H_last - b258``。
        low_avg: ``L_last + avg``。
        low_b165: ``L_last + b165``。
        low_b196: ``L_last + b196``。
        low_b258: ``L_last + b258``。
    """

    high_avg: float
    high_b165: float
    high_b196: float
    high_b258: float
    low_avg: float
    low_b165: float
    low_b196: float
    low_b258: float


def compute_hl_bands(
    high: np.ndarray, low: np.ndarray, stats: RangeStats
) -> HLPriceBands:
    """最新足の High/Low（昇順 last）へ σ 帯を投影した overlay 8 本を返す。

    ``H_last = high[-1]``, ``L_last = low[-1]``（元 iHigh/iLow の shift=0=最新足。
    昇順では末尾）。High 側=減算・Low 側=加算（L67-74）。

    Args:
        high: 高値配列（昇順）。
        low: 安値配列（昇順）。
        stats: ``compute_range_stats`` の結果。

    Returns:
        HLPriceBands（overlay 8 本）。
    """
    high = np.asarray(high, dtype=np.float64)
    low = np.asarray(low, dtype=np.float64)
    h_last = float(high[-1])  # 昇順 last = 最新足（iHigh(NULL,0,0)）
    l_last = float(low[-1])  # 昇順 last = 最新足（iLow(NULL,0,0)）
    return HLPriceBands(
        high_avg=h_last - stats.avg,
        high_b165=h_last - stats.b165,
        high_b196=h_last - stats.b196,
        high_b258=h_last - stats.b258,
        low_avg=l_last + stats.avg,
        low_b165=l_last + stats.b165,
        low_b196=l_last + stats.b196,
        low_b258=l_last + stats.b258,
    )


@dataclass(frozen=True)
class HLBandResult:
    """PRO!fitHLBand の計算成果（数値のみ・描画非依存の統合不変 DTO）。

    Attributes:
        range: レンジ系列（N,。writeable=False）。
        stats: レンジ統計（RangeStats）。
        bands: overlay 8 本（HLPriceBands）。
        sub_min: 別ウィンドウ下限（= 0.0。元 INDICATOR_MINIMUM）。
        sub_max: 別ウィンドウ上限（= b196*2。元 INDICATOR_MAXIMUM）。
    """

    range: np.ndarray
    stats: RangeStats
    bands: HLPriceBands
    sub_min: float
    sub_max: float

    def __post_init__(self) -> None:
        arr = np.asarray(self.range, dtype=np.float64)
        arr.setflags(write=False)  # DTO は不変（profit_stc 準拠）
        object.__setattr__(self, "range", arr)


def compute_hlband(high: np.ndarray, low: np.ndarray) -> HLBandResult:
    """レンジ・統計・overlay 8 本を統合し HLBandResult（frozen DTO）として返す。

    元 OnCalculate 全体（range 算出 → 全系列平均/母σ帯 → 最新 H/L への投影 →
    INDICATOR_MINIMUM/MAXIMUM 設定）を再現する。重複計算をしない。

    Args:
        high: 高値配列（昇順）。
        low: 安値配列（昇順・同長）。

    Returns:
        HLBandResult（range/stats/bands/sub_min=0.0/sub_max=b196*2）。

    Raises:
        ValueError: high/low の長さが一致しない。
    """
    range_ = compute_range(high, low)
    stats = compute_range_stats(range_)
    bands = compute_hl_bands(high, low, stats)
    return HLBandResult(
        range=range_,
        stats=stats,
        bands=bands,
        sub_min=0.0,
        sub_max=stats.b196 * 2,
    )
