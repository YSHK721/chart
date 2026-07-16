"""層名: core 層（純粋計算）。

責務:
    PRO!fit_Oscillator（18 サブ系列 PS レベルカウント複合）の数値計算「概念」だけを
    numpy ＋ 共有層のみで保持する純粋関数層。入出力・描画・pandas を含まない。
    バッファ番号・描画色・別ウィンドウ指定・``IndicatorSetXxx`` は偶有的性質であり
    本層には持ち込まない（依存は常に内向き）。

含む構造:
    compute_rvi            : iRVI MAIN（権威 RVI.mq5・三角加重 1,2,2,1・period 窓和）。
    compute_mard           : iMARD（EMA 固定・WEIGHTED 非対称・ma==0→0 退化ガード）。
    compute_rsi            : iRSI（共有 mql_builtins の再公開）。
    compute_mfi            : iMFI（共有 mql_builtins の再公開）。
    compute_stochastic     : iStochastic %K（共有 mql_builtins の再公開）。
    ps_level_count         : PS_GetLevelCountValue（共有 profit_system の再公開）。
    compute_sigma_levels   : iBandsOnArray σ12（共有 profit_system の再公開）。
    compute_level_count    : 18 系列（IC01..IC05）を順序厳守で加算したレベルカウント。
    compute_oscillator_levels : σ12 水準（compute_sigma_levels の別名）。
    compute_oscillator_full   : 18 系列集計 → σ12 → ±3.29σ クランプを統合した frozen DTO。
    OscillatorResult       : 計算成果の不変 DTO（level_count_clamped / raw_level_count / levels）。

元 MQL4 / PS.mqh 対応（``PRO!fit_Oscillator.mq4`` L168-256 を昇順=古→新へ 1:1 変換）:
    iRSI(inpPeriodA, PRICE_X) × 7（W/T/M/H/L/O/C） → IC01。IC01_W が initialization。
    iStochastic(inpPeriodA,1,1,1,MODE_MAIN/SIGNAL,0) × 2 → IC02（slowing=1/Dperiod=1 で
        両者とも生 %K に帰着＝同一配列を 2 回加算）。
    iMFI(inpPeriodA) × 1 → IC03。
    iRVI(inpPeriodA,0) × 1 → IC04（権威 RVI.mq5 の MAIN）。
    iMARD(inpPeriodB,0,MODE_EMA,PRICE_X) × 7 → IC05。
    各系列を PS_GetLevelCountValue(初期化フラグ, 系列, ExtBufferLevelCount, SIGMA_L6, limit)
        で加算（IC01_W のみ initialization=1）。
    iBandsOnArray(..., 0.67..3.29, mode=1/2) → σ12 水準。
    ExtBufferLevelCount の ±SD_1S6/SD_2S6（=±3.29σ）クランプ → level_count_clamped。

依存:
    標準: __future__, dataclasses, sys, pathlib, typing / 外部: numpy
    共有: common（applied_price, AppliedPrice）, moving_averages（exponential_ma_on_buffer）。
    pandas/描画 import は禁止。
"""

from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping

import numpy as np

# 共有層 import:
#   moving_averages … indicators（parents[2]）配下。
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))  # indicators → moving_averages

from common import AppliedPrice, applied_price  # noqa: E402
from moving_averages import exponential_ma_on_buffer  # noqa: E402
from mql_builtins import (  # noqa: E402,F401  # 正準 iRSI/iMFI/iStochastic（再公開して in-package 参照面を維持）
    compute_mfi,
    compute_rsi,
    compute_stochastic,
)

# PS レベルカウント系プリミティブは共有層 profit_system に集約済み（indicators 配下）。
from profit_system import (  # noqa: E402
    SIGMA_LEVELS,
    compute_sigma_levels,
    ps_level_count,
)

# 元 input の既定値（PRO!fit_Oscillator.mq4: inpPeriodA=6, inpPeriodB=60）。
DEFAULT_PERIOD_A: int = 6
DEFAULT_PERIOD_B: int = 60

# 標準化窓 W（直近 W 本の過去のみで σ 距離を算出＝look-ahead 除去・repaint しない）。
# None で全期間バッチ（従来 1:1・比較用）。日足 ~半年。
DEFAULT_WINDOW: int | None = 120

# RVI の三角加重期間（権威 RVI.mq5: #define TRIANGLE_PERIOD 3）。
_TRIANGLE_PERIOD: int = 3

# IC01 / IC05 の適用価格 7 系統の処理順（元 OnCalculate の呼び出し順 W→T→M→H→L→O→C）。
_APPLIED_PRICE_ORDER: tuple[AppliedPrice, ...] = (
    AppliedPrice.WEIGHTED,
    AppliedPrice.TYPICAL,
    AppliedPrice.MEDIAN,
    AppliedPrice.HIGH,
    AppliedPrice.LOW,
    AppliedPrice.OPEN,
    AppliedPrice.CLOSE,
)


# ============================================================ iRVI（権威 RVI.mq5）
def compute_rvi(
    open_: np.ndarray,
    high: np.ndarray,
    low: np.ndarray,
    close: np.ndarray,
    *,
    period: int,
) -> np.ndarray:
    """iRVI MAIN を権威 ``RVI.mq5`` で 1:1 再現する（三角加重・period 窓和）。

    各バー j について（昇順=古→新, j-3>=0 必要）::

        value_up[j]  = (close[j]-open[j]) + 2*(close[j-1]-open[j-1])
                       + 2*(close[j-2]-open[j-2]) + (close[j-3]-open[j-3])
        value_down[j]= (high[j]-low[j]) + 2*(high[j-1]-low[j-1])
                       + 2*(high[j-2]-low[j-2]) + (high[j-3]-low[j-3])

    バー i について窓 ``[i-period+1 .. i]`` の和を取り::

        sum_down != 0 -> RVI[i] = sum_up / sum_down
        sum_down == 0 -> RVI[i] = sum_up

    warm-up（i < period+2）は 0。最初の実値は i=period+2（権威 RVI.mq5 の start）。

    Args:
        open_/high/low/close: OHLC 各系列（昇順・同長）。
        period: RVI 期間（>=2。元 inpPeriodA）。

    Returns:
        iRVI MAIN 系列（同長, float64）。warm-up は 0。

    Raises:
        ValueError: ``period < 2`` または OHLC 長不一致。
    """
    if period < 2:
        raise ValueError(f"period は 2 以上である必要があります: {period}")
    o = np.asarray(open_, dtype=np.float64)
    h = np.asarray(high, dtype=np.float64)
    l = np.asarray(low, dtype=np.float64)
    c = np.asarray(close, dtype=np.float64)
    if not (o.size == h.size == l.size == c.size):
        raise ValueError(
            f"OHLC の長さが不一致です: {[o.size, h.size, l.size, c.size]}"
        )

    n = o.size
    co = c - o  # close - open
    hl = h - l  # high - low
    out = np.zeros(n, dtype=np.float64)

    def _value_up(j: int) -> float:
        return co[j] + 2.0 * co[j - 1] + 2.0 * co[j - 2] + co[j - 3]

    def _value_down(j: int) -> float:
        return hl[j] + 2.0 * hl[j - 1] + 2.0 * hl[j - 2] + hl[j - 3]

    # 権威 RVI.mq5: 計算開始 start = period + 2（= (period-1)+TRIANGLE_PERIOD）。
    # main ループが index period+2 を上書きするため最初の実値は i=period+2。
    # warm-up（i < period+2）は 0。i>=period+2 で窓内 min j=i-period+1>=3 ゆえ j-3>=0。
    for i in range(period + 2, n):
        sum_up = 0.0
        sum_down = 0.0
        for j in range(i - period + 1, i + 1):
            if j - 3 < 0:  # j-3>=0 必要（先頭は計算不可）
                continue
            sum_up += _value_up(j)
            sum_down += _value_down(j)
        out[i] = sum_up / sum_down if sum_down != 0.0 else sum_up
    return out


# ============================================================ iMARD（EMA 固定）
def compute_mard(
    open_: np.ndarray,
    high: np.ndarray,
    low: np.ndarray,
    close: np.ndarray,
    *,
    period: int,
    applied: AppliedPrice,
) -> np.ndarray:
    """iMARD を元 PS.mqh ``iMARD``（EMA 固定）で 1:1 再現する。

    分子 ``num_price``:
        ``applied==WEIGHTED`` のとき ``(open_+high+low+close)/4``（iMARD 独自 WEIGHTED
        = OHLC 平均, PS.mqh L1316）、それ以外は ``applied_price(applied, o,h,l,c)``（common 標準）。
    分母 ``ma``:
        ``EMA(applied_price(applied, o,h,l,c), period)``（common 標準。WEIGHTED は (H+L+2C)/4）。

    ``res[i] = (num_price[i] - ma[i]) / ma[i]``。``ma[i]==0`` のとき ``res[i]=0``
    （退化ガード。元はガード無いが warm-up EMA は非 0 のため 1:1 で 0 のみ回避）。

    ※WEIGHTED のみ分子/分母が非対称（分子 (O+H+L+C)/4・分母 EMA((H+L+2C)/4)）。
      これは原挙動の 1:1 再現。他 6 価格は対称。

    Args:
        open_/high/low/close: OHLC 各系列（昇順・同長）。
        period: EMA 期間（>=2。元 inpPeriodB）。
        applied: 適用価格種別（common.AppliedPrice）。

    Returns:
        iMARD 系列（同長, float64）。

    Raises:
        ValueError: ``period < 2`` または OHLC 長不一致。
    """
    if period < 2:
        raise ValueError(f"period は 2 以上である必要があります: {period}")
    o = np.asarray(open_, dtype=np.float64)
    h = np.asarray(high, dtype=np.float64)
    l = np.asarray(low, dtype=np.float64)
    c = np.asarray(close, dtype=np.float64)
    if not (o.size == h.size == l.size == c.size):
        raise ValueError(
            f"OHLC の長さが不一致です: {[o.size, h.size, l.size, c.size]}"
        )

    if applied == AppliedPrice.WEIGHTED:
        num_price = (o + h + l + c) / 4.0  # iMARD 独自 WEIGHTED（PS.mqh L1316）
    else:
        num_price = applied_price(applied, o, h, l, c)

    denom_price = applied_price(applied, o, h, l, c)  # common 標準（分母の iMA 入力）
    ma = np.zeros(denom_price.size, dtype=np.float64)
    exponential_ma_on_buffer(denom_price.size, 0, 0, period, denom_price, ma)

    out = np.zeros(num_price.size, dtype=np.float64)
    nonzero = ma != 0.0
    out[nonzero] = (num_price[nonzero] - ma[nonzero]) / ma[nonzero]
    return out


# compute_rsi / compute_mfi / compute_stochastic は共有 mql_builtins へ集約済み
# （上部で import・再公開）。呼び出しは従来どおり period= 明示渡しを維持する。


# ===================================================== 18 系列集計 / 別名 / 一括計算
def compute_level_count(
    open_: np.ndarray,
    high: np.ndarray,
    low: np.ndarray,
    close: np.ndarray,
    volume: np.ndarray,
    *,
    period_a: int = DEFAULT_PERIOD_A,
    period_b: int = DEFAULT_PERIOD_B,
    window: int | None = DEFAULT_WINDOW,
    freeze_last: bool = False,
) -> np.ndarray:
    """18 系列（IC01..IC05）を順序厳守で加算したレベルカウント系列を返す。

    元 OnCalculate L168-256 の 18 回の ``PS_GetLevelCountValue`` 呼び出しを再現する。
    順序: IC01 W→T→M→H→L→O→C（RSI 7）→ IC02 main→signal（生 %K 2）→ IC03（MFI 1）→
    IC04（RVI 1）→ IC05 W→T→M→H→L→O→C（MARD 7）。IC01_W のみ initialization=True。

    Args:
        open_/high/low/close/volume: OHLCV 各系列（昇順・同長）。
        period_a: オシレーター期間（既定 6。RSI/Stoch/MFI/RVI）。
        period_b: MARD 期間（既定 60）。
        window: 標準化窓 W（直近本数。既定 120＝因果。None で全期間バッチ）。

    Returns:
        レベルカウント系列（同長, float64）。因果窓時は warm-up が NaN（非描画）。
    """
    o = np.asarray(open_, dtype=np.float64)
    h = np.asarray(high, dtype=np.float64)
    l = np.asarray(low, dtype=np.float64)
    c = np.asarray(close, dtype=np.float64)
    v = np.asarray(volume, dtype=np.float64)

    level_count: np.ndarray | None = None
    first = True

    # IC01: RSI × 7（applied price 順 W→T→M→H→L→O→C）。IC01_W が initialization。
    for kind in _APPLIED_PRICE_ORDER:
        rsi = compute_rsi(applied_price(kind, o, h, l, c), period=period_a)
        level_count = ps_level_count(rsi, level_count, initialization=first, window=window, freeze_last=freeze_last)
        first = False

    # IC02: Stochastic main / signal（slowing=1/Dperiod=1 で両者とも生 %K）。
    stoch = compute_stochastic(h, l, c, period=period_a)
    level_count = ps_level_count(stoch, level_count, initialization=False, window=window, freeze_last=freeze_last)  # main
    level_count = ps_level_count(stoch, level_count, initialization=False, window=window, freeze_last=freeze_last)  # signal

    # IC03: MFI × 1。
    mfi = compute_mfi(h, l, c, v, period=period_a)
    level_count = ps_level_count(mfi, level_count, initialization=False, window=window, freeze_last=freeze_last)

    # IC04: RVI × 1。
    rvi = compute_rvi(o, h, l, c, period=period_a)
    level_count = ps_level_count(rvi, level_count, initialization=False, window=window, freeze_last=freeze_last)

    # IC05: MARD × 7（applied price 順 W→T→M→H→L→O→C）。
    for kind in _APPLIED_PRICE_ORDER:
        mard = compute_mard(o, h, l, c, period=period_b, applied=kind)
        level_count = ps_level_count(mard, level_count, initialization=False, window=window, freeze_last=freeze_last)

    assert level_count is not None
    return level_count


def compute_oscillator_levels(level_count: np.ndarray) -> Mapping[str, float]:
    """σ12 水準線（= ``compute_sigma_levels`` の別名）。複製元のキー名を保持する。"""
    return compute_sigma_levels(level_count)


@dataclass(frozen=True)
class OscillatorResult:
    """PRO!fit_Oscillator の計算成果（数値のみ・描画非依存の不変 DTO）。

    Attributes:
        level_count_clamped: ±3.29σ でクランプしたレベルカウント（描画対象, N,）。
        raw_level_count: クランプ前のレベルカウント系列（N,）。
        levels: σ12 水準線（up_*/dn_*）。
    """

    level_count_clamped: np.ndarray
    raw_level_count: np.ndarray
    levels: Mapping[str, float]

    def __post_init__(self) -> None:
        for name in ("level_count_clamped", "raw_level_count"):
            arr = np.asarray(getattr(self, name), dtype=np.float64)
            arr.setflags(write=False)  # DTO は不変
            object.__setattr__(self, name, arr)


def compute_oscillator_full(
    open_: np.ndarray,
    high: np.ndarray,
    low: np.ndarray,
    close: np.ndarray,
    volume: np.ndarray,
    *,
    period_a: int = DEFAULT_PERIOD_A,
    period_b: int = DEFAULT_PERIOD_B,
    window: int | None = DEFAULT_WINDOW,
    freeze_last: bool = False,
) -> OscillatorResult:
    """18 系列レベルカウント（クランプ済み）を一括算出する。

    元 OnCalculate の全体（18 系列集計 → σ12 水準 → ±3.29σ クランプ）を再現する。
    既定は因果ローリング窓（``window=DEFAULT_WINDOW``）で標準化し repaint しない。

    Args:
        open_/high/low/close/volume: OHLCV 各系列（昇順・同長）。
        period_a: オシレーター期間（既定 6）。
        period_b: MARD 期間（既定 60）。
        window: 標準化窓 W（既定 120＝因果。None で全期間バッチ）。

    Returns:
        OscillatorResult（level_count_clamped / raw_level_count / levels）。
        因果窓時は warm-up（先頭 window-1）が NaN（非描画）。

    Raises:
        ValueError: OHLCV 長不一致、または period_a<2 / period_b<2。
    """
    o = np.asarray(open_, dtype=np.float64)
    h = np.asarray(high, dtype=np.float64)
    l = np.asarray(low, dtype=np.float64)
    c = np.asarray(close, dtype=np.float64)
    v = np.asarray(volume, dtype=np.float64)
    if not (o.size == h.size == l.size == c.size == v.size):
        raise ValueError(
            f"OHLCV の長さが不一致です: {[o.size, h.size, l.size, c.size, v.size]}"
        )
    if period_a < 2:
        raise ValueError(f"period_a は 2 以上である必要があります: {period_a}")
    if period_b < 2:
        raise ValueError(f"period_b は 2 以上である必要があります: {period_b}")

    raw = compute_level_count(o, h, l, c, v, period_a=period_a, period_b=period_b, window=window, freeze_last=freeze_last)
    levels = compute_oscillator_levels(raw)
    upper = levels["up_329"]
    lower = levels["dn_329"]
    clamped = np.clip(raw, lower, upper)  # NaN（warm-up）は NaN のまま温存
    return OscillatorResult(
        level_count_clamped=clamped,
        raw_level_count=raw,
        levels=levels,
    )
