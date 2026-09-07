"""層名: core 層（純粋計算）。

責務:
    PRO!fitRMM（複合レベルカウント指標）の純粋計算を numpy ＋ 共有層のみで行う層。
    入出力・描画・pandas を含まない。iRSI / iMFI / iWPR は共有 mql_builtins、採点
    （funLevelCount）・MAROD は共有 profit_system を import 再公開して in-package
    参照面を維持する。σ スパン統計（series_avg / series_std / oscillator_span /
    rolling_span）は本指標パッケージが所有する単一情報源 ``profit_rmm.span_stats``
    へ集約し、ここでは import 再公開する（ISSUE-502 D-5: 姉妹 profit_rmm_macd との
    verbatim 複製の解消）。σ6 水準（compute_rmm_levels）のみ本モジュール内に閉じる。
    EMA 平滑・typical_price は共有層を再利用する（in-package 再実装はしない）。

含む構造:
    compute_wpr        : 生 Williams %R（-100..0）。権威 WPR.mq5 準拠（warm-up i<period-1）。
    compute_marod      : (typical-ma)/ma*100（float 精度）。
    compute_rsi        : iRSI（共有 mql_builtins.compute_rsi の再公開）。
    compute_mfi        : iMFI（共有 mql_builtins.compute_mfi の再公開）。
    _series_avg/_series_std : 系列平均・母標準偏差（÷N・全系列。span_stats の再公開）。
    oscillator_span    : avg±3σ のスパン（clamp で [0,100] クランプ・MAROD は非クランプ）。
    level_count_score  : funLevelCount 4 ケース（ゼロ割ガードなし 1:1）。
    compute_rmm        : 合成（iRSI/iWPR/iMFI/MAROD を採点・合算）。
    rolling_span       : oscillator_span の因果ローリング版（span_stats の再公開）。
    compute_rmm_levels : level_count の σ6 水準（母σ÷N）。
    RmmResult          : 計算成果の不変 DTO（全 ndarray writeable=False, frozen）。

元 MQL 対応（``PRO!fitRMM.mq4`` ＋ 標準 ``iWPR``（WPR.mq5）を昇順=古→新へ 1:1 変換）:
    iRSI / iMFI → compute_rsi / compute_mfi（共有 mql_builtins の再公開）。
    iWPR(period) → compute_wpr。WPR.mq5: warm-up [0..period-2]=0、最初の有効値は
        i=period-1（iRSI/iMFI の i<period とは 1 本ズレる）。maxH/minL は直近 period 本
        （現バー含む）の最大/最小。maxH!=minL → -(maxH-close)*100/(maxH-minL)、
        maxH==minL → wpr[i-1]（前値）。n<period → 全 0。
    funLevelCount → level_count_score（4 ケース・ゼロ割ガードなし）。
    iMAOnArray(EMA) → moving_averages.ma(..., "ema", ...)（共有再利用）。
    typical_price → common.typical_price（共有再利用）。

依存:
    標準: __future__, dataclasses, sys, pathlib / 外部: numpy
    共有: common（typical_price）, moving_averages（ma）。
    パッケージ内: profit_rmm.span_stats（σ スパン統計の単一情報源・numpy のみ）。
    pandas/描画 import は禁止。
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

# 共有ライブラリ moving_averages / mql_builtins（indigators/ 直下）を絶対 import で再利用する。
from moving_averages import ma
from mql_builtins import (  # noqa: F401  # 正準 iWPR/iRSI/iMFI（再公開して in-package 参照面を維持）
    compute_mfi,
    compute_rsi,
    compute_wpr,
)
from profit_system import (  # noqa: F401  # 正準 funLevelCount/MAROD（再公開して in-package 参照面を維持）
    compute_marod,
    level_count_score,
)

# σ スパン統計の単一情報源（ISSUE-502 D-5）。姉妹 profit_rmm_macd も同一モジュールを
# import するため、実装は本リポジトリ内に 1 つしか存在しない（複製の再発は
# profit_rmm_macd/tests/test_span_stats_single_source.py が機械的に遮断する）。
# 旧来の in-module 名（_series_avg / _series_std）は別名で維持し、参照面を変えない。
from profit_rmm.span_stats import (  # noqa: F401
    oscillator_span,
    rolling_span,
    series_avg as _series_avg,
    series_std as _series_std,
)

from common import typical_price

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
# _series_avg / _series_std / oscillator_span / rolling_span の実装は
# profit_rmm/span_stats.py（本パッケージ所有の単一情報源）にある。上部の import で
# 再公開しており、``core._series_avg`` 等の従来の参照面は変わらない。
# 実装を本モジュールへ書き戻す（＝複製の再生）ことは禁止する。


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
        ma  = EMA(typical, ma_period)（moving_averages.ma(..., "ema", ...)）
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

    ma_values = ma(typical, "ema", ma_period)
    marod = compute_marod(typical, ma_values)

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
