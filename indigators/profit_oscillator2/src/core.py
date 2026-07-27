"""層名: core 層（純粋計算）。

PRO!fitOscillator.mq4（funLevelCount 加重複合 ＋ Spearman RCI ＋ σ6・最複雑）の
純粋計算を numpy ＋ 共有層のみで行う層。入出力・描画・pandas を含まない。
既存 profit_oscillator とは完全分離（compute_oscillator2_full / Oscillator2Result /
oscillator2_lc 等の命名で衝突回避）。

含む構造:
    compute_rsi / compute_mfi / compute_wpr / compute_marod : サブオシレーター。
        いずれも共有 mql_builtins の再公開（profit_rmm と同一実装を参照）。
    compute_stochastic     : 生 %K（共有 mql_builtins の再公開）。
    level_count_score      : funLevelCount 4 ケース採点（共有 profit_system の再公開）。
    compute_istoch_main_signal : iStochastic full（main=EMA(rawK,slowing), signal=EMA(main,d)）。
    compute_level_count_rsi_term / combine_level_count_terms : 加重集計の構成要素。
    compute_level_count    : 加重複合レベルカウント（RSI 上書きバグ 1:1 再現）。
    compute_levels2        : σ6 水準（母σ÷N）＋ sub_min/sub_max（×1.5）。
    compute_rci            : Spearman RCI（LC を int 切り捨てしてから順位付け・1:1 再現）。
    compute_oscillator2_full / Oscillator2Result : 統合 frozen DTO。

元 MQL 対応（``PRO!fitOscillator.mq4`` を昇順=古→新へ 1:1 変換）:
    L158-167 iRSI/iMFI/iWPR/iStochastic/iMA → 各 compute_* / compute_istoch_main_signal。
    L172-174 MAROD_HLC/H/L → compute_marod（typical/high/low と EMA(該当price,ma_period)）。
    L177-278 funLevelCount 加重集計（RSI 3 重上書き → RSI_Low 基底のみ）→ compute_level_count。
    L281-295 iBandsOnArray(1.65/1.96/2.58) ＋ INDICATOR_MIN/MAX(×1.5) → compute_levels2。
    L312-403 RCI（RankPrices int 切り捨て・SpearmanRankCorrelation）→ compute_rci。

依存:
    標準: __future__, dataclasses, sys, pathlib / 外部: numpy
    共有: common（applied_price/AppliedPrice/typical_price）,
          moving_averages（ma）。pandas/描画 import は禁止。
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

# 共有ライブラリ moving_averages / mql_builtins（indigators/ 直下）を絶対 import で再利用する。
from moving_averages import ma
from mql_builtins import (  # noqa: F401  # 正準 iWPR/iRSI/iMFI/iStochastic（再公開して in-package 参照面を維持）
    compute_mfi,
    compute_rsi,
    compute_stochastic,
    compute_wpr,
)
from profit_system import (  # noqa: F401  # 正準 funLevelCount/MAROD（再公開して in-package 参照面を維持）
    compute_marod,
    level_count_score,
)

from common import AppliedPrice, applied_price, typical_price

# 元 input の既定値（PRO!fitOscillator.mq4）。
DEFAULT_OSC_PERIOD: int = 6
DEFAULT_STC_SLOW: int = 6
DEFAULT_MA_PERIOD: int = 60
DEFAULT_RCI_PERIOD: int = 12
DEFAULT_DIRECTION: bool = False

# funLevelCount の span 引数は元コードで 100 固定（L181 等の第 2 引数）。
_SPAN: float = 100.0


# =========================================================================== #
# サブオシレーター（compute_wpr/rsi/mfi/stochastic は共有 mql_builtins へ集約済み・
# compute_marod / level_count_score は共有 profit_system へ集約済み。上部で import・再公開。
# 既定 period 定数 DEFAULT_OSC_PERIOD は残置し、呼び出しで period= 明示する。
# =========================================================================== #


# =========================================================================== #
# iStochastic full（main = EMA(rawK, slowing), signal = EMA(main, d_period)）
# =========================================================================== #
def compute_istoch_main_signal(
    high: np.ndarray,
    low: np.ndarray,
    close: np.ndarray,
    *,
    osc_period: int = DEFAULT_OSC_PERIOD,
    stc_slow: int = DEFAULT_STC_SLOW,
) -> tuple[np.ndarray, np.ndarray]:
    """iStochastic の main/signal（二段 EMA）を返す。

    元 L166-167 ``iStochastic(NULL,0,osc_period,stc_slow,stc_slow,MODE_EMA,0,MODE_*,i)``::

        rawK  = compute_stochastic(h,l,c, osc_period)   # 生 %K
        main  = EMA(rawK, slowing=stc_slow)             # MODE_MAIN（slowing 平滑）
        signal= EMA(main, d_period=stc_slow)            # MODE_SIGNAL（D 平滑）

    EMA は共有 ``ma(..., "ema", ...)`` を使用する（in-package 再実装はしない）。

    Args:
        high/low/close: 昇順 HLC（同長）。
        osc_period: Kperiod（>=2）。
        stc_slow: slowing ＝ d_period（両方 stc_slow）。

    Returns:
        (main, signal) の組（各 float64 ndarray・入力と同長）。
    """
    raw_k = compute_stochastic(high, low, close, period=osc_period)
    main = ma(raw_k, "ema", stc_slow)
    signal = ma(main, "ema", stc_slow)
    return main, signal


# =========================================================================== #
# レベルカウント加重集計の構成要素
# =========================================================================== #
def _score_50pivot(osi: float) -> float:
    """50 ピボット採点（OSI<50→case1, OSI>50→case0, ==50→0）。span=100 固定。"""
    if osi < 50.0:
        return level_count_score(osi, _SPAN, 1)
    if osi > 50.0:
        return level_count_score(osi, _SPAN, 0)
    return 0.0


def _score_0pivot(marod: float) -> float:
    """0 ピボット採点（MAROD<0→case2, MAROD>0→case3, ==0→0）。span=100 固定。"""
    if marod < 0.0:
        return level_count_score(marod, _SPAN, 2)
    if marod > 0.0:
        return level_count_score(marod, _SPAN, 3)
    return 0.0


def compute_level_count_rsi_term(
    *,
    rsi_low: np.ndarray,
    rsi_high: np.ndarray,
    rsi_typical: np.ndarray,
) -> np.ndarray:
    """RSI 項を返す（元 L177-207 の 3 重上書きバグ 1:1 再現＝ RSI_Low 基底のみ）。

    元コードは RSI_Typical → RSI_High → RSI_Low の順に ``=``（代入）で 3 回上書きする。
    最終的に残るのは最後の RSI_Low の採点のみ（RSI_Typical/High は消える）。
    rsi_high/rsi_typical は受け取るが結果に寄与しない（discriminating 固定のため引数化）。

    Args:
        rsi_low: RSI(Low) 系列（採点に使用される唯一の系列）。
        rsi_high: RSI(High) 系列（上書きで消える・結果不寄与）。
        rsi_typical: RSI(Typical) 系列（上書きで消える・結果不寄与）。

    Returns:
        score(rsi_low, 50pivot) の系列（rsi_high/rsi_typical は不寄与）。
    """
    rsi_low = np.asarray(rsi_low, dtype=np.float64)
    n = rsi_low.shape[0]
    out = np.zeros(n, dtype=np.float64)
    for i in range(n):
        out[i] = _score_50pivot(float(rsi_low[i]))
    return out


def combine_level_count_terms(
    *,
    rsi_low_score: float,
    wpr_score: float,
    mfi_score: float,
    marod_typical_score: float,
    marod_high_score: float,
    marod_low_score: float,
    stc_signal_score: float,
    stc_main_score: float,
) -> float:
    """サブ採点値を加重 1/2/2/10/10/10/1/1 で合算する（元 L177-278 の係数 1:1）。

    ::

        lc =  1 * rsi_low_score          # RSI_Low 基底（上書き後）
            + 2 * wpr_score
            + 2 * mfi_score
            + 10 * marod_typical_score
            + 10 * marod_high_score
            + 10 * marod_low_score
            + 1 * stc_signal_score
            + 1 * stc_main_score
    """
    return (
        rsi_low_score
        + 2.0 * wpr_score
        + 2.0 * mfi_score
        + 10.0 * marod_typical_score
        + 10.0 * marod_high_score
        + 10.0 * marod_low_score
        + stc_signal_score
        + stc_main_score
    )


# =========================================================================== #
# レベルカウント（加重複合・RSI 上書きバグ 1:1 再現）
# =========================================================================== #
def compute_level_count(
    o: np.ndarray,
    h: np.ndarray,
    l: np.ndarray,
    c: np.ndarray,
    volume: np.ndarray,
    *,
    osc_period: int = DEFAULT_OSC_PERIOD,
    stc_slow: int = DEFAULT_STC_SLOW,
    ma_period: int = DEFAULT_MA_PERIOD,
) -> np.ndarray:
    """加重複合レベルカウント系列を返す（元 L158-278 を昇順で 1:1 再現）。

    サブオシレーター::

        RSI_Low  = compute_rsi(applied_price(LOW), osc_period)   # 基底（上書き後）
        WPR      = compute_wpr(h,l,c, osc_period) + 100
        MFI      = compute_mfi(h,l,c,volume, osc_period)
        MAROD_T  = compute_marod(typical, EMA(typical, ma_period))
        MAROD_H  = compute_marod(high,    EMA(high,    ma_period))
        MAROD_L  = compute_marod(low,     EMA(low,     ma_period))
        STC main/signal = compute_istoch_main_signal(...)

    加重（combine_level_count_terms）::

        lc = score(RSI_Low,50) + 2*score(WPR,50) + 2*score(MFI,50)
           + 10*score(MAROD_T,0) + 10*score(MAROD_H,0) + 10*score(MAROD_L,0)
           + score(STC_signal,50) + score(STC_main,50)

    Args:
        o/h/l/c/volume: 昇順 OHLCV（同長）。
        osc_period/stc_slow/ma_period: 各期間。

    Returns:
        level_count 系列（float64 ndarray, 入力と同長）。
    """
    o = np.asarray(o, dtype=np.float64)
    h = np.asarray(h, dtype=np.float64)
    l = np.asarray(l, dtype=np.float64)
    c = np.asarray(c, dtype=np.float64)
    volume = np.asarray(volume, dtype=np.float64)

    typical = typical_price(h, l, c)
    high_p = applied_price(AppliedPrice.HIGH, o, h, l, c)
    low_p = applied_price(AppliedPrice.LOW, o, h, l, c)

    rsi_low = compute_rsi(low_p, period=osc_period)
    wpr = compute_wpr(h, l, c, period=osc_period) + 100.0
    mfi = compute_mfi(h, l, c, volume, period=osc_period)

    ma_t = ma(typical, "ema", ma_period)
    ma_h = ma(high_p, "ema", ma_period)
    ma_l = ma(low_p, "ema", ma_period)

    marod_t = compute_marod(typical, ma_t)
    marod_h = compute_marod(high_p, ma_h)
    marod_l = compute_marod(low_p, ma_l)

    stc_main, stc_signal = compute_istoch_main_signal(
        h, l, c, osc_period=osc_period, stc_slow=stc_slow
    )

    n = c.shape[0]
    out = np.zeros(n, dtype=np.float64)
    for i in range(n):
        out[i] = combine_level_count_terms(
            rsi_low_score=_score_50pivot(float(rsi_low[i])),
            wpr_score=_score_50pivot(float(wpr[i])),
            mfi_score=_score_50pivot(float(mfi[i])),
            marod_typical_score=_score_0pivot(float(marod_t[i])),
            marod_high_score=_score_0pivot(float(marod_h[i])),
            marod_low_score=_score_0pivot(float(marod_l[i])),
            stc_signal_score=_score_50pivot(float(stc_signal[i])),
            stc_main_score=_score_50pivot(float(stc_main[i])),
        )
    return out


# =========================================================================== #
# σ6 水準（母σ÷N）＋ sub_min/sub_max（×1.5）・クランプ無し
# =========================================================================== #
def compute_levels2(level_count: np.ndarray) -> dict[str, float]:
    """level_count の σ6 水準（母σ÷N）＋ sub_min/sub_max を返す（クランプ無し）。

    ``avg=mean``, ``dev=母σ（÷N）``。元 L281-295 の iBandsOnArray(1.65/1.96/2.58)
    ＋ INDICATOR_MINIMUM/MAXIMUM(×1.5) を 1:1 再現する::

        up_165=avg+1.65dev, up_196=avg+1.96dev, up_258=avg+2.58dev
        dn_165=avg-1.65dev, dn_196=avg-1.96dev, dn_258=avg-2.58dev
        sub_min=(avg-1.96dev)*1.5  (= StdDev[5]*1.5)
        sub_max=(avg+1.96dev)*1.5  (= StdDev[2]*1.5)

    Args:
        level_count: レベルカウント系列。

    Returns:
        σ6 水準辞書（6 要素）＋ sub_min/sub_max。
    """
    x = np.asarray(level_count, dtype=np.float64)
    avg = float(np.mean(x))
    dev = float(np.sqrt(np.mean((x - avg) ** 2)))  # 母標準偏差（÷N）
    up_196 = avg + 1.96 * dev
    dn_196 = avg - 1.96 * dev
    return {
        "up_165": avg + 1.65 * dev,
        "up_196": up_196,
        "up_258": avg + 2.58 * dev,
        "dn_165": avg - 1.65 * dev,
        "dn_196": dn_196,
        "dn_258": avg - 2.58 * dev,
        "sub_min": dn_196 * 1.5,
        "sub_max": up_196 * 1.5,
    }


# =========================================================================== #
# RCI（Spearman・LC を int 切り捨ててから順位付け・1:1 再現）
# =========================================================================== #
def _rank_prices_int(window: list[int], period: int, direction: bool) -> list[float]:
    """元 RankPrices を int 値配列に対し 1:1 再現し R2（各要素の TrueRank）を返す。

    元コードは ``int SortInt[]`` への ArrayCopy / ``int etalon`` 代入で int 切り捨て済。
    本関数は既に int 化された ``window`` を受け取る。同値はタイ平均ランク。
    """
    sort_int = sorted(window, reverse=direction)
    true_ranks: list[float] = [i + 1 for i in range(period)]
    i = 0
    while i < period - 1:
        if sort_int[i] != sort_int[i + 1]:
            i += 1
            continue
        dublicat = sort_int[i]
        k = i + 1
        counter = 1
        average_rank = i + 1
        while k < period:
            if sort_int[k] == dublicat:
                counter += 1
                average_rank += k + 1
                k += 1
            else:
                break
        average_rank = average_rank / counter
        for m in range(i, k):
            true_ranks[m] = average_rank
        i = k
    r2: list[float] = [0.0] * period
    for idx in range(period):
        etalon = window[idx]
        k = 0
        while k < period:
            if etalon == sort_int[k]:
                r2[idx] = true_ranks[k]
                break
            k += 1
    return r2


def compute_rci(
    level_count: np.ndarray,
    *,
    period: int = DEFAULT_RCI_PERIOD,
    direction: bool = DEFAULT_DIRECTION,
    sigma_ref: float,
) -> np.ndarray:
    """Spearman RCI 系列を返す（LC を int 切り捨ててから順位付け・元 L312-403 を 1:1 再現）。

    各昇順バー ``a``::

        a < period-1 -> 0（warm-up）
        a >= period-1:
            w[k] = level_count[a-k]（k=0..period-1。元 resBLC[k]=LC[i+k]）
            int 切り捨て（int(w[k])・0 方向）で順位付け（direction でソート方向、同値タイ平均）。
            spearman = 1 - 6*Σ(R2[k]-(k+1))^2 / (period^3 - period)
            rci[a] = spearman * sigma_ref

    Args:
        level_count: レベルカウント系列。
        period: RCI 期間（>=2）。
        direction: False→昇順ソート / True→降順ソート。
        sigma_ref: Spearman 値の倍率（元 StcLCStdDevArray[5] ＝ compute_levels2 の dn_196）。

    Returns:
        rci 系列（float64 ndarray, 入力と同長）。
    """
    lc = np.asarray(level_count, dtype=np.float64)
    n = lc.shape[0]
    out = np.zeros(n, dtype=np.float64)
    denom = float(period**3 - period)
    for a in range(n):
        if a < period - 1:
            out[a] = 0.0
            continue
        window = [int(lc[a - k]) for k in range(period)]  # int 切り捨て（0 方向）
        r2 = _rank_prices_int(window, period, direction)
        z2 = 0.0
        for k in range(period):
            z2 += (r2[k] - (k + 1)) ** 2
        spearman = 1.0 - 6.0 * z2 / denom
        out[a] = spearman * sigma_ref
    return out


# =========================================================================== #
# 統合 DTO
# =========================================================================== #
@dataclass(frozen=True)
class Oscillator2Result:
    """PRO!fitOscillator の計算成果（数値のみ・描画非依存の不変 DTO）。

    Attributes:
        level_count: 加重複合レベルカウント系列（writeable=False）。
        rci: Spearman RCI 系列（writeable=False）。
        levels: σ6 水準辞書（up_165..dn_258 の 6 要素）。
        sub_min: 別ウィンドウ下限（= dn_196 * 1.5）。
        sub_max: 別ウィンドウ上限（= up_196 * 1.5）。
    """

    level_count: np.ndarray
    rci: np.ndarray
    levels: dict[str, float]
    sub_min: float
    sub_max: float

    def __post_init__(self) -> None:
        for name in ("level_count", "rci"):
            arr = np.asarray(getattr(self, name), dtype=np.float64)
            arr.setflags(write=False)  # DTO は不変
            object.__setattr__(self, name, arr)


def compute_oscillator2_full(
    o: np.ndarray,
    h: np.ndarray,
    l: np.ndarray,
    c: np.ndarray,
    volume: np.ndarray,
    *,
    osc_period: int = DEFAULT_OSC_PERIOD,
    stc_slow: int = DEFAULT_STC_SLOW,
    ma_period: int = DEFAULT_MA_PERIOD,
    rci_period: int = DEFAULT_RCI_PERIOD,
    direction: bool = DEFAULT_DIRECTION,
) -> Oscillator2Result:
    """level_count / rci / σ6 levels / sub_min / sub_max を統合し frozen DTO を返す。

    元 OnCalculate 全体（サブオシレーター → 加重集計 → σ6 → RCI）を 1:1 再現する。
    sigma_ref（RCI 倍率）は ``compute_levels2`` の ``dn_196``（元 StcLCStdDevArray[5]）。

    Args:
        o/h/l/c/volume: 昇順 OHLCV（同長）。
        osc_period/stc_slow/ma_period/rci_period/direction: 各パラメータ。

    Returns:
        Oscillator2Result。

    Raises:
        ValueError: ``osc_period<2`` / ``ma_period<2`` / OHLCV 長不一致。
    """
    if osc_period < 2:
        raise ValueError(f"osc_period は 2 以上である必要があります: {osc_period}")
    if ma_period < 2:
        raise ValueError(f"ma_period は 2 以上である必要があります: {ma_period}")

    o = np.asarray(o, dtype=np.float64)
    h = np.asarray(h, dtype=np.float64)
    l = np.asarray(l, dtype=np.float64)
    c = np.asarray(c, dtype=np.float64)
    volume = np.asarray(volume, dtype=np.float64)
    if not (o.shape == h.shape == l.shape == c.shape == volume.shape):
        raise ValueError(
            f"OHLCV の長さが一致しません: "
            f"{o.shape}/{h.shape}/{l.shape}/{c.shape}/{volume.shape}"
        )

    level_count = compute_level_count(
        o, h, l, c, volume,
        osc_period=osc_period, stc_slow=stc_slow, ma_period=ma_period,
    )
    levels = compute_levels2(level_count)
    rci = compute_rci(
        level_count,
        period=rci_period,
        direction=direction,
        sigma_ref=levels["dn_196"],
    )
    levels6 = {
        "up_165": levels["up_165"],
        "up_196": levels["up_196"],
        "up_258": levels["up_258"],
        "dn_165": levels["dn_165"],
        "dn_196": levels["dn_196"],
        "dn_258": levels["dn_258"],
    }
    return Oscillator2Result(
        level_count=level_count,
        rci=rci,
        levels=levels6,
        sub_min=levels["sub_min"],
        sub_max=levels["sub_max"],
    )
