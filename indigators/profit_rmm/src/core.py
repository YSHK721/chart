"""層名: core 層（純粋計算）。

責務:
    PRO!fitRMM（複合レベルカウント指標）の純粋計算を numpy ＋ 共有層のみで行う層。
    入出力・描画・pandas を含まない。iRSI / iMFI / iWPR は共有 mql_builtins、採点
    （funLevelCount）・MAROD は共有 profit_system を import 再公開して in-package
    参照面を維持する。σ 統計（oscillator_span / compute_rmm_levels）のみ本パッケージ
    内に閉じる。EMA 平滑・typical_price は共有層を再利用する（in-package 再実装は
    しない）。

含む構造:
    compute_wpr        : 生 Williams %R（-100..0）。権威 WPR.mq5 準拠（warm-up i<period-1）。
    compute_marod      : (typical-ma)/ma*100（float 精度）。
    compute_rsi        : iRSI（共有 mql_builtins.compute_rsi の再公開）。
    compute_mfi        : iMFI（共有 mql_builtins.compute_mfi の再公開）。
    _series_avg/_series_std : 系列平均・母標準偏差（÷N・全系列）。
    oscillator_span    : avg±3σ のスパン（clamp で [0,100] クランプ・MAROD は非クランプ）。
    level_count_score  : funLevelCount 4 ケース（ゼロ割ガードなし 1:1）。
    compute_rmm        : 合成（iRSI/iWPR/iMFI/MAROD を採点・合算）。
    compute_rmm_levels : level_count の σ6 水準（母σ÷N）。
    RmmResult          : 計算成果の不変 DTO（全 ndarray writeable=False, frozen）。

元 MQL 対応（``PRO!fitRMM.mq4`` ＋ 標準 ``iWPR``（WPR.mq5）を昇順=古→新へ 1:1 変換）:
    iRSI / iMFI → compute_rsi / compute_mfi（共有 mql_builtins の再公開）。
    iWPR(period) → compute_wpr。WPR.mq5: warm-up [0..period-2]=0、最初の有効値は
        i=period-1（iRSI/iMFI の i<period とは 1 本ズレる）。maxH/minL は直近 period 本
        （現バー含む）の最大/最小。maxH!=minL → -(maxH-close)*100/(maxH-minL)、
        maxH==minL → wpr[i-1]（前値）。n<period → 全 0。
    funLevelCount → level_count_score（4 ケース・ゼロ割ガードなし）。
    iMAOnArray(EMA) → exponential_ma_on_buffer（共有再利用）。
    typical_price → common.typical_price（共有再利用）。

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

# 元 input の既定値（PRO!fitRMM.mq4）。
DEFAULT_OSC_PERIOD: int = 6
DEFAULT_MA_PERIOD: int = 6

# compute_wpr / compute_rsi / compute_mfi は共有 mql_builtins へ集約済み（上部で import・再公開）。
# 既定 period 定数 DEFAULT_OSC_PERIOD は本パッケージに残置し、呼び出しで period= 明示する。


# compute_marod は共有 profit_system へ集約済み（上部で import・再公開）。


# ===========================================================================
# σ 統計（母σ÷N・全系列）
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
    """avg±3σ のスパン（x3p - x3m）を返す。

    ``avg=_series_avg(x)``, ``dev=_series_std(x)``, ``x3p=avg+3*dev``,
    ``x3m=avg-3*dev``。``clamp=True``（RSI/WPR/MFI）→ x3p=min(100,x3p),
    x3m=max(0,x3m)。``clamp=False``（MAROD）→ クランプ無し。

    Args:
        x: 対象オシレーター系列。
        clamp: True で [0,100] クランプ、False で素値。

    Returns:
        x3p - x3m（float）。
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


def _score_pivot50(osi: np.ndarray, span: float) -> np.ndarray:
    """50 基準オシレーターの採点を要素単位でベクトル化する（``level_count_score`` 同値）。

    ``level_count_score`` の case0（osi>50）と case1（osi<50）は
    ``((osi-50)/r)/100`` と ``-((50-osi)/r)/100`` であり、``a-b == -(b-a)`` が IEEE754 で
    厳密成立するため両ケースはビット的に同一値。よって基準点（osi==50）のみ寄与 0
    （元ループは分岐に入らず加算しない）とすれば、丸め無しの本採点をビット一致のまま
    ベクトル化できる（乱数掃引で実証済み）。

    Args:
        osi: オシレーター系列。
        span: 当該オシレーターのスパン（スカラ）。

    Returns:
        各要素の採点（osi==50 は 0.0）。
    """
    r = (span - 50.0) / 200.0
    return np.where(osi == 50.0, 0.0, ((osi - 50.0) / r) / 100.0)


def _score_pivot0(osi: np.ndarray, span: float) -> np.ndarray:
    """0 基準オシレーター（MAROD）の採点を要素単位でベクトル化する。

    ``level_count_score`` の case2（osi<0）と case3（osi>0）は同様に符号反転でビット同値
    （``((osi-r)/r)/100``）。基準点（osi==0）は元ループが加算しないため 0.0 とする。

    Args:
        osi: MAROD 系列。
        span: MAROD のスパン（スカラ）。

    Returns:
        各要素の採点（osi==0 は 0.0）。
    """
    r = (span / 2.0) / 200.0
    return np.where(osi == 0.0, 0.0, ((osi - r) / r) / 100.0)


# ===========================================================================
# 合成
# ===========================================================================
@dataclass(frozen=True)
class RmmResult:
    """PRO!fitRMM の計算成果（数値のみ・描画非依存の不変 DTO）。

    Attributes:
        level_count: 合算レベルカウント系列（writeable=False）。
        rsi: iRSI 系列（writeable=False）。
        wpr: iWPR 系列（+100 済み。writeable=False）。
        mfi: iMFI 系列（writeable=False）。
        marod: MAROD 系列（writeable=False）。
        lc_levels: level_count の σ6 水準辞書（up_1s..dn_3s の 6 要素）。
    """

    level_count: np.ndarray
    rsi: np.ndarray
    wpr: np.ndarray
    mfi: np.ndarray
    marod: np.ndarray
    lc_levels: dict[str, float]

    def __post_init__(self) -> None:
        for name in ("level_count", "rsi", "wpr", "mfi", "marod"):
            arr = np.asarray(getattr(self, name), dtype=np.float64)
            arr.setflags(write=False)  # DTO は不変
            object.__setattr__(self, name, arr)


def compute_rmm(
    high: np.ndarray,
    low: np.ndarray,
    close: np.ndarray,
    volume: np.ndarray,
    *,
    osc_period: int = DEFAULT_OSC_PERIOD,
    ma_period: int = DEFAULT_MA_PERIOD,
) -> RmmResult:
    """iRSI / iWPR / iMFI / MAROD を採点・合算し RmmResult（frozen DTO）を返す。

    元 OnCalculate の funLevelCount 合算を昇順で 1:1 再現する::

        typical = typical_price(H,L,C)
        rsi = compute_rsi(typical, period=osc_period)
        mfi = compute_mfi(H,L,C,V, period=osc_period)
        wpr = compute_wpr(H,L,C, osc_period) + 100.0
        ma  = EMA(typical, ma_period)（exponential_ma_on_buffer）
        marod = compute_marod(typical, ma)
        span: rsi/wpr/mfi は clamp=True、marod は clamp=False。
        各バー i:
            RSI: rsi[i]<50->score(rsi[i],rsi_span,1); >50->case0; ==50->+0
            WPR: wpr[i]<50->case1; >50->case0; ==50->+0
            MFI: mfi[i]<50->case1; >50->case0; ==50->+0
            MAROD: marod[i]<0->score(marod[i],marod_span,2); >0->case3; ==0->+0
            level_count[i] = 上記 4 採点の和
        warm-up バーも採点に含める（1:1 再現）。

    Args:
        high/low/close/volume: 昇順 OHLCV（同長）。
        osc_period: オシレーター期間（既定 6、>=2）。
        ma_period: EMA 期間（既定 6）。

    Returns:
        RmmResult（level_count / rsi / wpr / mfi / marod / lc_levels）。

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

    # 旧: for i in range(n) で 4 オシレーターを level_count_score 採点・加算（O(4n)）。
    # 加算順（rsi→wpr→mfi→marod）を左結合で保持し、各採点を _score_pivot50/0 で
    # ベクトル化（0.0 加算は IEEE754 で恒等のため未加算ケースとも一致）。
    level_count = (
        _score_pivot50(rsi, rsi_span)
        + _score_pivot50(wpr, wpr_span)
        + _score_pivot50(mfi, mfi_span)
        + _score_pivot0(marod, marod_span)
    )

    lc_levels = compute_rmm_levels(level_count)
    return RmmResult(
        level_count=level_count,
        rsi=rsi,
        wpr=wpr,
        mfi=mfi,
        marod=marod,
        lc_levels=lc_levels,
    )


def compute_rmm_levels(level_count: np.ndarray) -> dict[str, float]:
    """level_count の σ6 水準（母σ÷N）を返す。

    ``avg=mean``, ``dev=母σ``::

        {"up_1s":avg+dev, "up_2s":avg+2dev, "up_3s":avg+3dev,
         "dn_1s":avg-dev, "dn_2s":avg-2dev, "dn_3s":avg-3dev}

    Args:
        level_count: レベルカウント系列。

    Returns:
        σ6 水準辞書（6 要素）。
    """
    avg = _series_avg(level_count)
    dev = _series_std(level_count)
    return {
        "up_1s": avg + dev,
        "up_2s": avg + 2.0 * dev,
        "up_3s": avg + 3.0 * dev,
        "dn_1s": avg - dev,
        "dn_2s": avg - 2.0 * dev,
        "dn_3s": avg - 3.0 * dev,
    }
