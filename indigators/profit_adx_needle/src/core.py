"""PRO!fit_ADX_NEEDLE のコア（純粋ロジック・numpy のみ）。

層名/責務:
    core 層。元 MQL4 ``PRO!fit_ADX_NEEDLE.mq4`` + 依存ライブラリ
    ``ProfitSystem/PS.mqh`` の数値計算「概念」だけを純粋関数として保持する。
    バッファ番号・描画色・別ウィンドウ指定・``IndicatorSetXxx``（レベル線設定）は
    偶有的性質であり本層には持ち込まない（依存は常に内向き）。

指標の概念（「市場の温度」を測るオシレーター）:
    1. ADX を ``inpPeriod=6`` で 7 種の適用価格（W/T/M/H/L/O/C）で算出する。
    2. 各 ADX 値を「系列平均からの距離 / EMA 標準偏差」へ単位変換し 7 回加算する
       （= レベルカウント。符号付き ≒ 7×(adx-平均)/std の「温度」）。
    3. レベルカウント系列に Bollinger 風 σ バンド（0.67〜3.29σ）を当てて水準線とし、
       ±3.29σ でクランプしてヒストグラム表示する。

元 MQL4 / PS.mqh の対応:
    * ``iADX(NULL,0,inpPeriod,PRICE_*,0,i)``             → ``compute_adx``
    * ``PS_GetLevelCountValue`` / ``PS_GetUnitConversion``→ ``ps_level_count`` / ``ps_unit_conversion``
    * ``PS_GetAverage`` / ``PS_GetStandardDeviationValue``→ ``ps_average`` / ``ps_std_ema``
    * ``iBandsOnArray(...,deviation,...,MODE_UPPER/LOWER,0)`` → ``compute_sigma_levels``
    * ``ExtBufferLevelCount`` のクランプ（SD_1S6/SD_2S6）  → ``compute_adx_needle`` の clip

移植上の重要判断（ガイド §4.4「まず元挙動を 1:1 再現」）:
    MetaQuotes 版 MT4 の ``iADX`` は方向性移動を High/Low、True Range を
    High/Low/Close から算出し、``applied_price`` は計算式へ実質的に入らない
    （MQL5 で同パラメータが削除された理由）。したがって 7 種の適用価格呼び出しは
    同一の ADX 系列を返し、レベルカウントは同一の単位変換値を 7 回加算する（= 7×）。
    本実装はこの MetaQuotes 仕様を忠実再現する。詳細は SPEC.md §9。

依存:
    標準: dataclasses, typing / 外部: numpy / プロジェクト内: なし
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping

import numpy as np

# PS レベルカウント系プリミティブは profit_system に集約済み（本パッケージが正準供給元）。
# ISSUE-182 項目 1: 4 プリミティブは public 名へ昇格済み。深い ``profit_system.src.core``
# ではなくパッケージの公開面（``__all__``）だけを参照する。
from profit_system import (
    SIGMA_LEVELS,
    compute_sigma_levels,
    ps_average,
    ps_level_count,
    ps_normalize,
    ps_std_ema,
    ps_unit_conversion,
)

# 本パッケージの既存参照面（tests/test_core.py）を維持するための旧名別名。
# 束縛先は上の public 名と同一オブジェクト＝値は 1 ビットも変わらない。
_normalize = ps_normalize
_ps_average = ps_average
_ps_std_ema = ps_std_ema
_unit_conversion = ps_unit_conversion

# 既定パラメータ（元 ``input int inpPeriod = 6``）。
DEFAULT_PERIOD: int = 6

# 標準化窓 W（直近 W 本の過去のみで σ 距離を算出＝look-ahead 除去・repaint しない）。
# None で全期間バッチ（従来 1:1・比較用）。日足 ~半年。
DEFAULT_WINDOW: int | None = 120

# 元 7 系統の適用価格（PRICE_WEIGHTED/TYPICAL/MEDIAN/HIGH/LOW/OPEN/CLOSE）。
# MetaQuotes 版 iADX では出力に影響しない（vestigial）が、元の 7 回加算（= 7×）を
# 構造として保持するために枚数のみ用いる。SPEC.md §9 参照。
APPLIED_PRICES: tuple[str, ...] = ("W", "T", "M", "H", "L", "O", "C")

# クランプに用いる σ（元 SD_1S6/SD_2S6 = ±3.29σ バンド）。
_CLAMP_SIGMA: float = 3.29


def _ema(values: np.ndarray, period: int) -> np.ndarray:
    """MQL ``iMAOnArray(..., MODE_EMA)`` 相当の指数移動平均（α=2/(period+1)）。

    昇順（古い→新しい）系列に対し ``ema[0]=values[0]``、
    ``ema[k]=ema[k-1]+α(values[k]-ema[k-1])`` で更新する。MT4 の EMA は Wilder の
    1/period ではなく 2/(period+1) を用いる（ガイド調査結果）。

    Args:
        values: 入力系列（昇順, 1 次元）。
        period: 平滑期間（>0）。

    Returns:
        同長の EMA 系列。
    """
    v = np.asarray(values, dtype=np.float64)
    n = v.size
    out = np.empty(n, dtype=np.float64)
    if n == 0:
        return out
    alpha = 2.0 / (period + 1.0)
    out[0] = v[0]
    for k in range(1, n):
        out[k] = out[k - 1] + alpha * (v[k] - out[k - 1])
    return out


def compute_adx(
    high: np.ndarray, low: np.ndarray, close: np.ndarray, period: int = DEFAULT_PERIOD
) -> np.ndarray:
    """MetaQuotes 版 MT4 ``iADX`` の ADX 本線（MODE_MAIN）を再現する。

    昇順（古い→新しい）OHLC を前提とする。各バー i (i>=1) について:
        +DM = high[i]-high[i-1],  -DM = low[i-1]-low[i]   （負は 0、非対称ゼロ化）
        TR  = max(|H-L|, |H-prevC|, |L-prevC|)
        +SDI = 100*(+DM)/TR,  -SDI = 100*(-DM)/TR        （TR=0 は 0）
        +DI = EMA(+SDI),  -DI = EMA(-SDI)                （α=2/(period+1)）
        DX  = 100*|+DI - -DI| / (+DI + -DI)              （分母 0 は 0）
        ADX = EMA(DX)
    ``applied_price`` は MetaQuotes 仕様では ADX に影響しないため引数に持たない
    （SPEC.md §9）。先頭バー（i=0）は前足が無いため +DM/-DM/TR=0（warm-up）。

    Args:
        high/low/close: 各バーの高値/安値/終値（昇順・同長）。
        period: 平滑期間（既定 6。元 inpPeriod）。

    Returns:
        ADX 本線（同長, float64）。先頭は warm-up のため 0 付近。

    Raises:
        ValueError: 配列長が不一致、空、または period<=0 の場合。
    """
    h = np.asarray(high, dtype=np.float64)
    low_a = np.asarray(low, dtype=np.float64)
    c = np.asarray(close, dtype=np.float64)
    if not (h.size == low_a.size == c.size):
        raise ValueError(f"HLC の長さが不一致です: {[h.size, low_a.size, c.size]}")
    n = h.size
    if n == 0:
        raise ValueError("HLC が空です。")
    if period <= 0:
        raise ValueError(f"period は正値である必要があります: {period}")

    pdm = np.zeros(n, dtype=np.float64)
    mdm = np.zeros(n, dtype=np.float64)
    tr = np.zeros(n, dtype=np.float64)

    up = h[1:] - h[:-1]          # +方向の動き
    dn = low_a[:-1] - low_a[1:]  # -方向の動き
    p = np.where(up < 0.0, 0.0, up)
    m = np.where(dn < 0.0, 0.0, dn)
    # 非対称ゼロ化: 同値は両 0、小さい側を 0。
    eq = p == m
    p_lt = p < m
    p_out = np.where(eq, 0.0, np.where(p_lt, 0.0, p))
    m_out = np.where(eq, 0.0, np.where(p_lt, m, 0.0))
    pdm[1:] = p_out
    mdm[1:] = m_out

    hl = np.abs(h[1:] - low_a[1:])
    hc = np.abs(h[1:] - c[:-1])
    lc = np.abs(low_a[1:] - c[:-1])
    tr[1:] = np.maximum(np.maximum(hl, hc), lc)

    with np.errstate(divide="ignore", invalid="ignore"):
        sdi_plus = np.where(tr > 0.0, 100.0 * pdm / tr, 0.0)
        sdi_minus = np.where(tr > 0.0, 100.0 * mdm / tr, 0.0)

    pdi = _ema(sdi_plus, period)
    mdi = _ema(sdi_minus, period)

    denom = pdi + mdi
    with np.errstate(divide="ignore", invalid="ignore"):
        dx = np.where(denom != 0.0, 100.0 * np.abs(pdi - mdi) / denom, 0.0)

    return _ema(dx, period)


def compute_level_count(
    high: np.ndarray, low: np.ndarray, close: np.ndarray, period: int = DEFAULT_PERIOD,
    *, window: int | None = DEFAULT_WINDOW, freeze_last: bool = False,
) -> np.ndarray:
    """7 系統の適用価格 ADX を単位変換・加算したレベルカウント系列を返す。

    元 OnCalculate の 7 回の ``PS_GetLevelCountValue`` 呼び出し（W は初期化付き、
    残り 6 は加算）を再現する。MetaQuotes 仕様では 7 系統の ADX は同一のため、
    結果は単一 ADX の単位変換値の 7 倍に等しい（SPEC.md §9）。

    Args:
        high/low/close: OHLC の高値/安値/終値（昇順・同長）。
        period: ADX 平滑期間（既定 6）。
        window: 標準化窓 W（直近本数。既定 120＝因果。None で全期間バッチ）。

    Returns:
        レベルカウント系列（同長, float64, 符号付き ≒ 7×σ距離）。
        因果窓時は warm-up（先頭 window-1）が NaN（非描画）。
    """
    adx = compute_adx(high, low, close, period)
    res: np.ndarray | None = None
    for k, _name in enumerate(APPLIED_PRICES):
        # 元コードでは W のみ initialization=1、残りは 0（加算）。
        res = ps_level_count(adx, res, initialization=(k == 0), window=window, freeze_last=freeze_last)
    assert res is not None
    return res


@dataclass(frozen=True)
class AdxNeedleResult:
    """PRO!fit_ADX_NEEDLE の計算成果（数値のみ・描画非依存）。

    Attributes:
        needle: ±3.29σ でクランプしたヒストグラム値（描画対象, N,）。
        level_count: クランプ前のレベルカウント系列（N,）。
        adx: 単一 ADX 本線（N,。7 系統共通）。
        sigma_levels: σ 水準線（up_*/dn_*）。
        upper_clamp: クランプ上限（= up_329, SD_1S6）。
        lower_clamp: クランプ下限（= dn_329, SD_2S6）。
    """

    needle: np.ndarray
    level_count: np.ndarray
    adx: np.ndarray
    sigma_levels: Mapping[str, float]
    upper_clamp: float
    lower_clamp: float

    def __post_init__(self) -> None:
        for name in ("needle", "level_count", "adx"):
            arr = np.asarray(getattr(self, name), dtype=np.float64)
            arr.setflags(write=False)  # DTO は不変（ガイド §2）
            object.__setattr__(self, name, arr)


def compute_adx_needle(
    high: np.ndarray, low: np.ndarray, close: np.ndarray, period: int = DEFAULT_PERIOD,
    *, window: int | None = DEFAULT_WINDOW, freeze_last: bool = False,
) -> AdxNeedleResult:
    """ADX_NEEDLE のヒストグラム（クランプ済みレベルカウント）を一括算出する。

    元 OnCalculate の全体（ADX 算出 → レベルカウント加算 → σ 水準 → ±3.29σ クランプ）を
    再現する。既定は因果ローリング窓（``window=DEFAULT_WINDOW``）で標準化し repaint しない。

    Args:
        high/low/close: OHLC の高値/安値/終値（昇順・同長）。
        period: ADX 平滑期間（既定 6。元 inpPeriod）。
        window: 標準化窓 W（既定 120＝因果。None で全期間バッチ）。

    Returns:
        AdxNeedleResult（needle / level_count / adx / sigma_levels / clamp 境界）。
        因果窓時は warm-up（先頭 window-1）が NaN（非描画）。

    Raises:
        ValueError: 配列長不一致・空・period<=0 の場合。
    """
    adx = compute_adx(high, low, close, period)
    level = compute_level_count(high, low, close, period, window=window, freeze_last=freeze_last)
    levels = compute_sigma_levels(level)
    upper = levels["up_329"]
    lower = levels["dn_329"]
    needle = np.clip(level, lower, upper)  # NaN（warm-up）は NaN のまま温存
    return AdxNeedleResult(
        needle=needle,
        level_count=level,
        adx=adx,
        sigma_levels=levels,
        upper_clamp=upper,
        lower_clamp=lower,
    )
