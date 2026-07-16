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

from common import typical_price  # noqa: E402

# 元 input の既定値（PRO!fitRMM.mq4）。
DEFAULT_OSC_PERIOD: int = 6
DEFAULT_MA_PERIOD: int = 6

# 標準化窓 W（各オシレーターのスパン avg±3σ を直近 W 本の過去のみから算出＝look-ahead 除去・
# repaint しない）。None で全期間バッチ（従来 1:1・比較用）。日足 ~半年。
DEFAULT_WINDOW: int | None = 120

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


def rolling_span(
    x: np.ndarray, window: int, *, clamp: bool, freeze_last: bool = False
) -> np.ndarray:
    """``oscillator_span`` の因果ローリング版（各バーの avg±3σ スパンを直近 W 本から算出）。

    バー i のスパンを区間 ``[i-window+1, i]`` の平均・母標準偏差から
    ``(avg+3σ) - (avg-3σ)``（clamp 時は各端を [0,100] に丸め）で求める。未来を含まないため
    確定バーのスパン＝レベルカウントは repaint しない。warm-up（``i<window-1``）は ``NaN``。

    ``freeze_last``（既定 ``False``）:
        * ``False``: 上記の通り（既定。出力は 1 ビットも変えない）。
        * ``True``: **最終要素 ``out[-1]`` のみ** 基準窓を確定足
          ``[n-1-window .. n-2]``（最終点を除く直前 window 本）へ差し替えてスパンを
          算出する。``out[0..n-2]`` は ``freeze_last=False`` と完全に同一。形成中（足内）
          の最新足をティック粒度で採点する際、スパン（採点の分母）の基準を 1 足 1 回・
          足内で固定（凍結）する用途。平均・母標準偏差・分母 ``window``・クランプは
          本関数の既存定義と厳密に同一で、最終点だけ窓をずらした以外は数値が一致する。
          直前 window 本が満たせない（``n < window + 1``）場合は ``out[-1]=NaN``
          （warm-up と同様）。これは ``profit_system._causal_z`` の freeze_last と整合する。

    Args:
        x: 対象オシレーター系列。
        window: 過去参照本数 W（>=2）。
        clamp: True で各端を [0,100] にクランプ（RSI/WPR/MFI）、False で素値（MAROD）。
        freeze_last: True で最終点のスパン基準を確定足（直前 W 本）へ凍結する。既定
            False で挙動不変。

    Returns:
        各バーのスパン（同長, float64。warm-up は NaN）。
    """
    a = np.asarray(x, dtype=np.float64)
    n = a.size
    out = np.full(n, np.nan, dtype=np.float64)
    if window < 2 or n < window:
        return out
    csum = np.concatenate([[0.0], np.cumsum(a)])
    csq = np.concatenate([[0.0], np.cumsum(a * a)])
    for i in range(window - 1, n):
        lo = i - window + 1
        avg = (csum[i + 1] - csum[lo]) / window
        var = (csq[i + 1] - csq[lo]) / window - avg * avg
        dev = np.sqrt(var) if var > 0.0 else 0.0
        x3p = avg + 3.0 * dev
        x3m = avg - 3.0 * dev
        if clamp:
            x3p = min(100.0, x3p)
            x3m = max(0.0, x3m)
        out[i] = x3p - x3m
    if freeze_last:
        # 最終点 out[-1] のみ、基準窓を確定足 [n-1-window .. n-2]（最終点を除く直前
        # window 本）へ差し替える。out[0..n-2] は上のループ結果のまま不変。
        if n < window + 1:
            out[-1] = np.nan  # 直前 window 本を満たせない（warmup 同様）。
        else:
            lo = n - 1 - window  # 直前 window 本 = a[lo:n-1]（= a[n-1-window .. n-2]）。
            hi = n - 1  # csum 上限 index（確定足 a[lo:n-1] の和 = csum[hi]-csum[lo]）。
            avg = (csum[hi] - csum[lo]) / window
            var = (csq[hi] - csq[lo]) / window - avg * avg
            dev = np.sqrt(var) if var > 0.0 else 0.0
            x3p = avg + 3.0 * dev
            x3m = avg - 3.0 * dev
            if clamp:
                x3p = min(100.0, x3p)
                x3m = max(0.0, x3m)
            out[-1] = x3p - x3m
    return out


# funLevelCount（level_count_score）は共有 profit_system へ集約済み（上部で import・再公開）。


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
    window: int | None = DEFAULT_WINDOW,
    freeze_last: bool = False,
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
        window: 標準化窓 W（既定 120＝因果。None で全期間バッチ）。
        freeze_last: True かつ ``window is not None`` のとき、最終点のスパン基準
            （採点の分母）を確定足（直前 W 本）に凍結する（``rolling_span`` 参照）。
            ``window=None``（全期間バッチ）経路では無関係（未使用）。既定 False で挙動不変。

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

    n = close.shape[0]
    # スパン（採点の分母）を全期間スカラ（window=None）か因果ローリング（window=W）で用意。
    # 配列化して各バー span[i] を参照する（因果時は warm-up が NaN→採点 NaN→level_count NaN）。
    if window is None:
        rsi_span = np.full(n, oscillator_span(rsi, clamp=True))
        wpr_span = np.full(n, oscillator_span(wpr, clamp=True))
        mfi_span = np.full(n, oscillator_span(mfi, clamp=True))
        marod_span = np.full(n, oscillator_span(marod, clamp=False))
    else:
        rsi_span = rolling_span(rsi, window, clamp=True, freeze_last=freeze_last)
        wpr_span = rolling_span(wpr, window, clamp=True, freeze_last=freeze_last)
        mfi_span = rolling_span(mfi, window, clamp=True, freeze_last=freeze_last)
        marod_span = rolling_span(marod, window, clamp=False, freeze_last=freeze_last)

    level_count = np.zeros(n, dtype=np.float64)
    for i in range(n):
        lc = 0.0
        if rsi[i] < 50.0:
            lc += level_count_score(rsi[i], rsi_span[i], 1)
        elif rsi[i] > 50.0:
            lc += level_count_score(rsi[i], rsi_span[i], 0)
        if wpr[i] < 50.0:
            lc += level_count_score(wpr[i], wpr_span[i], 1)
        elif wpr[i] > 50.0:
            lc += level_count_score(wpr[i], wpr_span[i], 0)
        if mfi[i] < 50.0:
            lc += level_count_score(mfi[i], mfi_span[i], 1)
        elif mfi[i] > 50.0:
            lc += level_count_score(mfi[i], mfi_span[i], 0)
        if marod[i] < 0.0:
            lc += level_count_score(marod[i], marod_span[i], 2)
        elif marod[i] > 0.0:
            lc += level_count_score(marod[i], marod_span[i], 3)
        level_count[i] = lc

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
    x = np.asarray(level_count, dtype=np.float64)
    x = x[np.isfinite(x)]  # 因果版 warm-up の NaN を除外（全期間版は無影響）
    if x.size == 0:
        x = np.zeros(1, dtype=np.float64)
    avg = _series_avg(x)
    dev = _series_std(x)
    return {
        "up_1s": avg + dev,
        "up_2s": avg + 2.0 * dev,
        "up_3s": avg + 3.0 * dev,
        "dn_1s": avg - dev,
        "dn_2s": avg - 2.0 * dev,
        "dn_3s": avg - 3.0 * dev,
    }
