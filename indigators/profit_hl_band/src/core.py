"""層名: core 層（純粋計算）。

責務:
    PRO!fit_HLBand（メインチャート overlay の上下 8 バンド価格線を出す距離指標・
    アンダースコア版）の計算を numpy 配列のみで行う純粋関数層。入出力・描画・
    pandas を含まない。High-Close / Low-Close の絶対距離系列を起点とし、全系列の
    平均 + dev·母σ（÷N）を起点終値 close[-2] に加減算して 8 バンドを得る。

含む構造:
    HL_BAND_DEVS     : 固定偏差 (0.67, 1.65, 1.96, 2.58)。
    compute_distances: dist_high=|H-C|, dist_low=|L-C|（全 i・warm-up/NaN なし）。
    band_upper       : mean(dist) + dev*母σ(dist)（÷N 全系列）。
    compute_hl_band  : close_ref=close[-2] へ 8 バンドを投影した frozen DTO。
    HlBandResult     : dist_high/dist_low/close_ref/levels の不変 DTO。

元 MQL 対応（``PRO!fit_HLBand.mq4`` を昇順=古→新へ 1:1 変換）:
    L205 ResBufferDivisionOpenHigh[i]=MathAbs(iHigh(i)-iClose(i)) → compute_distances dist_high
    L206 ResBufferDivisionOpenLow[i] =MathAbs(iLow(i)-iClose(i))  → compute_distances dist_low
    L220-223 StdDevArray[1..4]=iClose(1)+iBandsOnArray(OpenHigh,dev,0,1,0)
        → up_k = close_ref + band_upper(dist_high, dev_k)（加算・mode1=upper）。
    L224-227 StdDevArray[5..8]=iClose(1)-iBandsOnArray(OpenLow,dev,0,1,0)
        → dn_k = close_ref - band_upper(dist_low, dev_k)（減算）。
    iClose(inpSymbol,inpTimeFrame,1) = 系列 index 1 = 昇順 close[-2]  → close_ref。
    iBandsOnArray(...,dev,0,1,0) の中心=全系列平均・偏差=母σ（÷N）          → band_upper。
    input は inpSymbol/inpTimeFrame のみ（計算 period 無し）。

依存:
    標準: __future__, dataclasses / 外部: numpy のみ
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

# iBandsOnArray の deviation 引数（L220-227）。元 define SIGMA_L1/L3/L4/L5。
HL_BAND_DEVS: tuple[float, float, float, float] = (0.67, 1.65, 1.96, 2.58)

# levels 辞書のキー接尾辞（dev → キー）。
_DEV_SUFFIX: dict[float, str] = {0.67: "067", 1.65: "165", 1.96: "196", 2.58: "258"}


def compute_distances(
    high: np.ndarray, low: np.ndarray, close: np.ndarray
) -> tuple[np.ndarray, np.ndarray]:
    """``dist_high=|high-close|``, ``dist_low=|low-close|`` を全 i で返す。

    元 ``MathAbs(iHigh(i)-iClose(i))`` / ``MathAbs(iLow(i)-iClose(i))``（L205-206）。

    Args:
        high: 高値配列（昇順, 古→新）。
        low: 安値配列（昇順, 同長）。
        close: 終値配列（昇順, 同長）。

    Returns:
        ``(dist_high, dist_low)`` のタプル（入力と同長, float64）。

    Raises:
        ValueError: high/low/close の長さが一致しない。
    """
    high = np.asarray(high, dtype=np.float64)
    low = np.asarray(low, dtype=np.float64)
    close = np.asarray(close, dtype=np.float64)
    if not (high.shape == low.shape == close.shape):
        raise ValueError(
            f"high/low/close の長さが一致しません: "
            f"{high.shape}/{low.shape}/{close.shape}"
        )
    return np.abs(high - close), np.abs(low - close)


def band_upper(dist: np.ndarray, dev: float) -> float:
    """``mean(dist) + dev * 母σ(dist)`` を返す（全系列・母σ÷N）。

    元 ``iBandsOnArray(dist, 0, length, dev, 0, 1, 0)``（mode1=upper）に対応する。
    中心は全系列平均、偏差は母標準偏差（÷N）。

    Args:
        dist: 距離系列（全系列）。
        dev: 偏差係数。

    Returns:
        ``mean + dev*sigma`` のスカラ。
    """
    x = np.asarray(dist, dtype=np.float64)
    mean = float(np.mean(x))
    sigma = float(np.sqrt(np.mean((x - mean) ** 2)))  # 母σ（÷N, MT4 iBands 準拠）
    return mean + dev * sigma


@dataclass(frozen=True)
class HlBandResult:
    """PRO!fit_HLBand の計算成果（数値のみ・描画非依存の不変 DTO）。

    Attributes:
        dist_high: |H-C| 系列（N,。writeable=False）。
        dist_low: |L-C| 系列（N,。writeable=False）。
        close_ref: 起点終値（= close[-2]。元 iClose(...,1)）。
        levels: 8 バンド辞書 ``{"up_067","up_165","up_196","up_258",
            "dn_067","dn_165","dn_196","dn_258"}``。
    """

    dist_high: np.ndarray
    dist_low: np.ndarray
    close_ref: float
    levels: dict[str, float]

    def __post_init__(self) -> None:
        for name in ("dist_high", "dist_low"):
            arr = np.asarray(getattr(self, name), dtype=np.float64)
            arr.setflags(write=False)  # DTO は不変（profit_hlband 準拠）
            object.__setattr__(self, name, arr)


def compute_hl_band(
    high: np.ndarray, low: np.ndarray, close: np.ndarray
) -> HlBandResult:
    """距離系列・起点 close[-2]・8 バンドを統合し HlBandResult として返す。

    元 OnCalculate 全体（距離算出 → 全系列平均/母σ帯 → close[-2] への加減算投影）を
    再現する。up_k=close_ref+band_upper(dist_high,dev_k)（加算）、
    dn_k=close_ref-band_upper(dist_low,dev_k)（減算）。

    Args:
        high: 高値配列（昇順）。
        low: 安値配列（昇順・同長）。
        close: 終値配列（昇順・同長）。

    Returns:
        HlBandResult（dist_high/dist_low/close_ref/levels）。

    Raises:
        ValueError: high/low/close の長さが一致しない、または N<2（close[-2] 不在）。
    """
    dist_high, dist_low = compute_distances(high, low, close)
    close = np.asarray(close, dtype=np.float64)
    if close.shape[0] < 2:
        raise ValueError(
            f"N>=2 が必要です（close[-2] 起点）。len(close)={close.shape[0]}"
        )
    close_ref = float(close[-2])  # 昇順 index 1 from end = iClose(...,1)
    levels: dict[str, float] = {}
    for dev in HL_BAND_DEVS:
        suffix = _DEV_SUFFIX[dev]
        levels[f"up_{suffix}"] = close_ref + band_upper(dist_high, dev)  # 加算
        levels[f"dn_{suffix}"] = close_ref - band_upper(dist_low, dev)  # 減算
    return HlBandResult(
        dist_high=dist_high,
        dist_low=dist_low,
        close_ref=close_ref,
        levels=levels,
    )
