"""層名: core 層（純粋計算）。

責務:
    PRO!fitRMMMACD（RMM レベルカウント＋MACD連鎖の変種）の純粋計算を numpy ＋
    共有層のみで行う層。入出力・描画・pandas を含まない。

    level_count（4 オシレーター funLevelCount 合算）は profit_rmm/src/core.py の
    level_count 生成パイプライン全体を **verbatim 複製**する（iWPR/iRSI/iMFI/
    oscillator_span/level_count_score・クランプ非対称・funLevelCount4ケース・合算・
    warm-up・iWPR 権威・flat→50/負MF==0→100 を完全保持・ロジック改変禁止）。
    その level_count に MACD 連鎖を適用する。**ただし MFIMACD/RSIMACD とは 2 点が
    異なる**:

        重要差分①: macd[i] = slow[i] - fast[i]（MFIMACD の fast-slow と逆。元 L272
            ``MacdBuffer[i] = SlowEmaBuffer[i] - FastEmaBuffer[i]``）。
        重要差分②: histogram[i] = macd[i] - signal[i]（×2.618 係数なし。元 L280
            ``MacdHistogramBuffer[i] = (MacdBuffer[i] - SignalBuffer[i])``）。

    σ 水準線は無い（元は funIndicatorSet を OnCalculate で呼ばず・水準を出力しない）。

含む構造:
    compute_wpr / compute_marod / compute_rsi / compute_mfi / oscillator_span /
        level_count_score : profit_rmm の level_count 算出部の verbatim 複製。
    compute_rmm_level_count : 上記を採点・合算して level_count を返す（複製）。
    compute_rmmmacd         : level_count → fast/slow EMA → macd(=slow-fast) →
        signal EMA → histogram(=macd-signal・係数なし) を統合した frozen DTO を返す。
    RmmMacdResult           : 計算成果の不変 DTO（σ levels フィールドを持たない）。

元 MQL 対応（``PRO!fitRMMMACD.mq4`` L162-280 を昇順=古→新へ 1:1 変換）:
    level_count 部 = PRO!fitRMM.mq4 と同一（iRSI/iWPR/iMFI/MAROD funLevelCount 合算）。
    iMAOnArray(EMA, FastEMA=4 / SlowEMA=8 / SignalEMA=4) → exponential_ma_on_buffer。
    MACD = Slow - Fast（L272）。Histogram = MACD - Signal（L280・係数なし）。

依存:
    標準: __future__, dataclasses, sys, pathlib / 外部: numpy
    共有: common（typical_price）, moving_averages（exponential_ma_on_buffer）。
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
from mql_builtins import (  # noqa: E402,F401  # 正準 iWPR/iRSI/iMFI（再公開して in-package 参照面を維持）
    compute_mfi,
    compute_rsi,
    compute_wpr,
)
from profit_system import (  # noqa: E402,F401  # 正準 funLevelCount/MAROD（再公開して in-package 参照面を維持）
    compute_marod,
    level_count_score,
)

# 共有ライブラリ common（適用価格）を リポジトリルート経由で再利用する。
sys.path.insert(0, str(Path(__file__).resolve().parents[3]))  # = repo root
from common import typical_price  # noqa: E402

# 元 input の既定値（PRO!fitRMMMACD.mq4）。
DEFAULT_OSC_PERIOD: int = 6
DEFAULT_MA_PERIOD: int = 6
DEFAULT_FAST_EMA: int = 4
DEFAULT_SLOW_EMA: int = 8
DEFAULT_SIGNAL_EMA: int = 4

# compute_wpr / compute_rsi / compute_mfi は共有 mql_builtins へ集約済み（上部で import・再公開）。
# 既定 period 定数 DEFAULT_OSC_PERIOD は本パッケージに残置し、呼び出しで period= 明示する。


# MAROD（compute_marod）は共有 profit_system へ集約済み（上部で import・再公開）。


# ===========================================================================
# σ 統計（母σ÷N・全系列）— oscillator_span 用（profit_rmm の verbatim 複製）
# ===========================================================================
def _series_avg(x: np.ndarray) -> float:
    """系列平均（全系列）。"""
    return float(np.mean(x))


def _series_std(x: np.ndarray) -> float:
    """母標準偏差（÷N・全系列）。"""
    x = np.asarray(x, dtype=np.float64)
    avg = _series_avg(x)
    return float(np.sqrt(np.mean((x - avg) ** 2)))


def oscillator_span(x: np.ndarray, *, clamp: bool) -> float:
    """avg±3σ のスパン（x3p - x3m）を返す（profit_rmm の verbatim 複製）。

    ``clamp=True``（RSI/WPR/MFI）→ x3p=min(100,x3p), x3m=max(0,x3m)。
    ``clamp=False``（MAROD）→ クランプ無し。
    """
    avg = _series_avg(x)
    dev = _series_std(x)
    x3p = avg + 3.0 * dev
    x3m = avg - 3.0 * dev
    if clamp:
        x3p = min(100.0, x3p)
        x3m = max(0.0, x3m)
    return x3p - x3m


# funLevelCount（level_count_score）は共有 profit_system へ集約済み（上部で import・再公開）。


# ===========================================================================
# level_count 合算（profit_rmm/src/core.py compute_rmm の level_count 部の複製）
# ===========================================================================
def compute_rmm_level_count(
    high: np.ndarray,
    low: np.ndarray,
    close: np.ndarray,
    volume: np.ndarray,
    *,
    osc_period: int = DEFAULT_OSC_PERIOD,
    ma_period: int = DEFAULT_MA_PERIOD,
) -> np.ndarray:
    """iRSI / iWPR / iMFI / MAROD を funLevelCount で採点・合算した level_count を返す。

    profit_rmm/src/core.py ``compute_rmm`` の level_count 算出パイプライン全体を
    verbatim 複製する（同一入力で ``compute_rmm(...).level_count`` と bit-for-bit 一致）。

    Args:
        high/low/close/volume: 昇順 OHLCV（同長）。
        osc_period: オシレーター期間（既定 6、>=2）。
        ma_period: EMA 期間（既定 6）。

    Returns:
        level_count 系列（入力と同長・float64）。

    Raises:
        ValueError: ``osc_period < 2``、または HLCV 長不一致。
    """
    if osc_period < 2:
        raise ValueError(f"osc_period は 2 以上である必要があります: {osc_period}")

    high = np.asarray(high, dtype=np.float64)
    low = np.asarray(low, dtype=np.float64)
    close = np.asarray(close, dtype=np.float64)
    volume = np.asarray(volume, dtype=np.float64)
    if not (high.shape == low.shape == close.shape == volume.shape):
        raise ValueError(
            f"HLCV の長さが一致しません: "
            f"{high.shape}/{low.shape}/{close.shape}/{volume.shape}"
        )

    typical = typical_price(high, low, close)
    rsi = compute_rsi(typical, period=osc_period)
    mfi = compute_mfi(high, low, close, volume, period=osc_period)
    wpr = compute_wpr(high, low, close, period=osc_period) + 100.0

    ma = np.zeros(typical.shape[0], dtype=np.float64)
    exponential_ma_on_buffer(typical.shape[0], 0, 0, ma_period, typical, ma)
    marod = compute_marod(typical, ma)

    rsi_span = oscillator_span(rsi, clamp=True)
    wpr_span = oscillator_span(wpr, clamp=True)
    mfi_span = oscillator_span(mfi, clamp=True)
    marod_span = oscillator_span(marod, clamp=False)

    n = close.shape[0]
    level_count = np.zeros(n, dtype=np.float64)
    for i in range(n):
        lc = 0.0
        if rsi[i] < 50.0:
            lc += level_count_score(rsi[i], rsi_span, 1)
        elif rsi[i] > 50.0:
            lc += level_count_score(rsi[i], rsi_span, 0)
        if wpr[i] < 50.0:
            lc += level_count_score(wpr[i], wpr_span, 1)
        elif wpr[i] > 50.0:
            lc += level_count_score(wpr[i], wpr_span, 0)
        if mfi[i] < 50.0:
            lc += level_count_score(mfi[i], mfi_span, 1)
        elif mfi[i] > 50.0:
            lc += level_count_score(mfi[i], mfi_span, 0)
        if marod[i] < 0.0:
            lc += level_count_score(marod[i], marod_span, 2)
        elif marod[i] > 0.0:
            lc += level_count_score(marod[i], marod_span, 3)
        level_count[i] = lc

    return level_count


# ===========================================================================
# 合成（MACD 連鎖・σ 水準なし）
# ===========================================================================
@dataclass(frozen=True)
class RmmMacdResult:
    """PRO!fitRMMMACD の計算成果（数値のみ・描画非依存の不変 DTO）。

    **σ levels フィールドは持たない**（元は水準を出力しない）。

    Attributes:
        level_count: RMM レベルカウント系列（writeable=False）。
        fast: level_count の EMA(FastEMA) 系列（writeable=False）。
        slow: level_count の EMA(SlowEMA) 系列（writeable=False）。
        macd: slow - fast（重要差分①。writeable=False）。
        signal: EMA(macd, SignalEMA)（writeable=False）。
        histogram: macd - signal（重要差分②・係数なし。writeable=False）。
    """

    level_count: np.ndarray
    fast: np.ndarray
    slow: np.ndarray
    macd: np.ndarray
    signal: np.ndarray
    histogram: np.ndarray

    def __post_init__(self) -> None:
        for name in ("level_count", "fast", "slow", "macd", "signal", "histogram"):
            arr = np.asarray(getattr(self, name), dtype=np.float64)
            arr.setflags(write=False)  # DTO は不変
            object.__setattr__(self, name, arr)


def compute_rmmmacd(
    high: np.ndarray,
    low: np.ndarray,
    close: np.ndarray,
    volume: np.ndarray,
    *,
    osc_period: int = DEFAULT_OSC_PERIOD,
    ma_period: int = DEFAULT_MA_PERIOD,
    fast: int = DEFAULT_FAST_EMA,
    slow: int = DEFAULT_SLOW_EMA,
    signal: int = DEFAULT_SIGNAL_EMA,
) -> RmmMacdResult:
    """level_count → fast/slow EMA → macd(=slow-fast) → signal EMA →
    histogram(=macd-signal・係数なし) を統合し RmmMacdResult（frozen DTO）を返す。

    計算順序（元 MQL の 1:1 再現）::

        1. level_count = compute_rmm_level_count(...)   # profit_rmm 複製
        2. fast = EMA(level_count, fast) ; slow = EMA(level_count, slow)  # 共有
        3. macd[i] = slow[i] - fast[i]                  # 重要差分①（L272）
        4. signal = EMA(macd, signal)
        5. histogram[i] = macd[i] - signal[i]           # 重要差分②（L280・係数なし）

    σ 水準は算出しない（元は水準を出力しない）。

    Args:
        high/low/close/volume: 昇順 OHLCV（同長）。
        osc_period: オシレーター期間（既定 6, >=2）。
        ma_period: EMA 期間（既定 6）。
        fast: FastEMA 期間（既定 4）。
        slow: SlowEMA 期間（既定 8）。
        signal: SignalEMA 期間（既定 4）。

    Returns:
        RmmMacdResult（level_count/fast/slow/macd/signal/histogram）。

    Raises:
        ValueError: ``osc_period < 2`` または HLCV 長不一致（compute_rmm_level_count 経由）。
    """
    level_count = compute_rmm_level_count(
        high, low, close, volume, osc_period=osc_period, ma_period=ma_period
    )
    n = level_count.shape[0]

    fast_buf = np.zeros(n, dtype=np.float64)
    exponential_ma_on_buffer(n, 0, 0, fast, level_count, fast_buf)
    slow_buf = np.zeros(n, dtype=np.float64)
    exponential_ma_on_buffer(n, 0, 0, slow, level_count, slow_buf)

    macd = slow_buf - fast_buf  # 重要差分①: Slow - Fast（元 L272）

    signal_buf = np.zeros(n, dtype=np.float64)
    exponential_ma_on_buffer(n, 0, 0, signal, macd, signal_buf)

    histogram = macd - signal_buf  # 重要差分②: 係数なし（元 L280）

    return RmmMacdResult(
        level_count=level_count,
        fast=fast_buf,
        slow=slow_buf,
        macd=macd,
        signal=signal_buf,
        histogram=histogram,
    )
