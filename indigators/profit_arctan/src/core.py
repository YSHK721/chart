"""PRO!fit_Arctan のコア（純粋ロジック・numpy ＋ 共有層のみ）。

層名/責務:
    core 層。元 MQL4 ``PRO!fit_Arctan.mq4`` + 依存ライブラリ ``ProfitSystem/PS.mqh`` の
    ``iARCTAN`` の数値計算「概念」だけを純粋関数として保持する。バッファ番号・描画色・
    別ウィンドウ指定・``IndicatorSetXxx`` は偶有的性質であり本層には持ち込まない
    （依存は常に内向き）。

指標の概念（iARCTAN オシレーター版「市場の温度」）:
    1. iARCTAN を 7 種の適用価格（W/T/M/H/L/O/C）で算出する。
       iARCTAN は移動平均の隣接差 ``MA[i]-MA[i-1]`` を ``MathArctan`` で角度（度）へ
       変換した値（warm-up / 前足未確定では 0）。
    2. 各 iARCTAN 値を「系列平均からの距離 / EMA 標準偏差」へ単位変換し 7 回加算する
       （= レベルカウント）。
    3. レベルカウント系列に σ バンド（0.67〜3.29σ）を当てて水準線とし、±3.29σ で
       クランプする。

元 MQL4 / PS.mqh の対応:
    * ``iARCTAN(...)``（PS.mqh L1214-）              → ``compute_arctan``
    * 7 回の ``PS_GetLevelCountValue`` 呼び出し        → ``compute_level_count``
    * ``PS_GetLevelCountValue`` / ``PS_GetUnitConversion`` → ``ps_level_count`` / ``_unit_conversion``
      （共有 profit_system の再公開。profit_adx_needle と同一実装を参照）
    * ``iBandsOnArray(...)``                          → ``compute_sigma_levels``
      （共有 profit_system の再公開。別名 ``compute_arctan_levels``）
    * ``ExtBuffer`` の ±3.29σ クランプ                → ``compute_arctan_full`` の clip

移植上の重要判断（元挙動 1:1 再現）:
    ADX_NEEDLE と異なり、iARCTAN は ``applied_price`` が実際に計算へ入る（MA への入力
    価格を切り替える）。したがって 7 系統は異なる系列となり、レベルカウントは 7 系統の
    単位変換値の総和となる（単純な 7 倍ではない）。``ps_level_count`` /
    ``compute_sigma_levels`` は共有 profit_system を再利用（キー名・定数は ADX_NEEDLE と同一）。

依存:
    標準: dataclasses, math, sys, typing / 外部: numpy
    プロジェクト内（共有層）: common.applied_price / moving_averages の on_buffer 関数
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping

import numpy as np

from common import AppliedPrice, applied_price
from moving_averages import (
    exponential_ma_on_buffer,
    linear_weighted_ma_on_buffer,
    simple_ma_on_buffer,
    smoothed_ma_on_buffer,
)

# PS レベルカウント系プリミティブは共有層 profit_system に集約済み（indicators 配下）。
from profit_system import (
    SIGMA_LEVELS,
    compute_sigma_levels,
    ps_level_count,
)

# 既定パラメータ（元 iARCTAN の period 既定。ma_method=1/bar_width=0.1 は関数シグネチャ既定）。
DEFAULT_PERIOD: int = 6

# 標準化窓 W（直近 W 本の過去のみで σ 距離を算出＝look-ahead 除去・repaint しない）。
# None で全期間バッチ（従来 1:1・比較用）。日足 ~半年。
DEFAULT_WINDOW: int | None = 120

# 元 7 系統の適用価格（W/T/M/H/L/O/C）。iARCTAN では出力に影響する（MA 入力を切替）。
APPLIED_PRICES: tuple[str, ...] = ("W", "T", "M", "H", "L", "O", "C")

# iARCTAN の角度変換定数（元 res = (MathArctan(A-B)/bar_width)*(180/3.14159265359)）。
_PI: float = 3.14159265359

# 適用価格 7 系統の処理順（W=初期化, 残り 6=加算。元 OnCalculate の呼び出し順）。
_APPLIED_PRICE_ORDER: tuple[AppliedPrice, ...] = (
    AppliedPrice.WEIGHTED,
    AppliedPrice.TYPICAL,
    AppliedPrice.MEDIAN,
    AppliedPrice.HIGH,
    AppliedPrice.LOW,
    AppliedPrice.OPEN,
    AppliedPrice.CLOSE,
)

# ma_method（元 iMA の MODE_SMA/EMA/SMMA/LWMA = 0/1/2/3）→ on_buffer 関数。
_MA_DISPATCH = {
    0: simple_ma_on_buffer,
    1: exponential_ma_on_buffer,
    2: smoothed_ma_on_buffer,
    3: linear_weighted_ma_on_buffer,
}


# =========================================================================== iARCTAN
def compute_arctan(
    price: np.ndarray,
    *,
    period: int,
    ma_method: int,
    bar_width: float,
) -> np.ndarray:
    """元 PS.mqh ``iARCTAN`` を昇順系列で 1:1 再現する。

    まず ``ma = MA(price, period, ma_method)``（on_buffer で buffer 0 初期化→破壊更新）を
    求め、各バー i について元コードの

        double_A = iMA(..., shift)      → ma[i]
        double_B = iMA(..., shift+1)    → ma[i-1]
        if(double_B == NULL) return 0
        res = (MathArctan(A - B) / bar_width) * (180 / 3.14159265359)

    を再現する。``i==0``（前足なし）または ``ma[i-1]==0``（warm-up = 元 B==NULL）は 0。

    Args:
        price: 単一の適用価格系列（昇順・1 次元）。
        period: MA 平滑期間（>=2。元 period）。
        ma_method: 0=SMA / 1=EMA / 2=SMMA / 3=LWMA。
        bar_width: 角度スケール（元 bar_width。res は atan を bar_width で除算）。

    Returns:
        iARCTAN 系列（price と同長, float64）。warm-up / i==0 は 0。

    Raises:
        ValueError: ``period < 2`` または ``ma_method`` が未知の場合。
    """
    if period < 2:
        raise ValueError(f"period は 2 以上である必要があります: {period}")
    ma_fn = _MA_DISPATCH.get(ma_method)
    if ma_fn is None:
        raise ValueError(f"未知の ma_method です: {ma_method!r}")

    p = np.asarray(price, dtype=np.float64)
    n = p.size
    buffer = np.zeros(n, dtype=np.float64)  # buffer 0 初期化 → on_buffer が破壊更新
    ma_fn(n, 0, 0, period, p, buffer)

    out = np.zeros(n, dtype=np.float64)
    for i in range(n):
        if i == 0 or buffer[i - 1] == 0.0:  # 元 double_B == NULL
            out[i] = 0.0
            continue
        diff = buffer[i] - buffer[i - 1]
        out[i] = (np.arctan(diff) / bar_width) * (180.0 / _PI)
    return out


# ===================================================== 7 価格集計 / 別名 / 一括計算
def compute_level_count(
    open_: np.ndarray,
    high: np.ndarray,
    low: np.ndarray,
    close: np.ndarray,
    *,
    period: int,
    ma_method: int,
    bar_width: float,
    window: int | None = DEFAULT_WINDOW,
    freeze_last: bool = False,
) -> np.ndarray:
    """7 系統の適用価格 iARCTAN を単位変換・加算したレベルカウント系列を返す。

    元 OnCalculate の 7 回の ``PS_GetLevelCountValue`` 呼び出し（W=初期化, 残り 6=加算）を
    再現する。iARCTAN では各適用価格で MA 入力が異なるため、結果は 7 系統の単位変換値の
    総和となる。

    Args:
        open_/high/low/close: OHLC 各系列（昇順・同長）。
        period: MA 平滑期間。
        ma_method: 0=SMA/1=EMA/2=SMMA/3=LWMA。
        bar_width: iARCTAN の角度スケール。
        window: 標準化窓 W（直近本数。既定 120＝因果。None で全期間バッチ）。

    Returns:
        レベルカウント系列（同長, float64）。因果窓時は warm-up が NaN（非描画）。
    """
    level_count: np.ndarray | None = None
    for k, kind in enumerate(_APPLIED_PRICE_ORDER):
        price = applied_price(kind, open_, high, low, close)
        arc = compute_arctan(price, period=period, ma_method=ma_method, bar_width=bar_width)
        # 元コードでは W のみ initialization=1、残りは 0（加算）。
        level_count = ps_level_count(arc, level_count, initialization=(k == 0), window=window, freeze_last=freeze_last)
    assert level_count is not None
    return level_count


def compute_arctan_levels(level_count: np.ndarray) -> Mapping[str, float]:
    """σ12 水準線（= ``compute_sigma_levels`` の別名）。複製元のキー名を保持する。"""
    return compute_sigma_levels(level_count)


@dataclass(frozen=True)
class ArctanResult:
    """PRO!fit_Arctan の計算成果（数値のみ・描画非依存）。

    Attributes:
        level_count_clamped: ±3.29σ でクランプしたレベルカウント（描画対象, N,）。
        raw_level_count: クランプ前のレベルカウント系列（N,）。
        levels: σ 水準線（up_*/dn_*）。
    """

    level_count_clamped: np.ndarray
    raw_level_count: np.ndarray
    levels: Mapping[str, float]

    def __post_init__(self) -> None:
        for name in ("level_count_clamped", "raw_level_count"):
            arr = np.asarray(getattr(self, name), dtype=np.float64)
            arr.setflags(write=False)  # DTO は不変
            object.__setattr__(self, name, arr)


def compute_arctan_full(
    open_: np.ndarray,
    high: np.ndarray,
    low: np.ndarray,
    close: np.ndarray,
    *,
    period: int = DEFAULT_PERIOD,
    ma_method: int = 1,
    bar_width: float = 0.1,
    window: int | None = DEFAULT_WINDOW,
    freeze_last: bool = False,
) -> ArctanResult:
    """iARCTAN レベルカウント（クランプ済み）を一括算出する。

    元 OnCalculate の全体（7 系統 iARCTAN → レベルカウント加算 → σ 水準 → ±3.29σ クランプ）を
    再現する。既定は因果ローリング窓（``window=DEFAULT_WINDOW``）で標準化し repaint しない。

    Args:
        open_/high/low/close: OHLC 各系列（昇順・同長）。
        period: MA 平滑期間（既定 6）。
        ma_method: 0=SMA/1=EMA/2=SMMA/3=LWMA（既定 1=EMA）。
        bar_width: iARCTAN の角度スケール（既定 0.1）。
        window: 標準化窓 W（既定 120＝因果。None で全期間バッチ）。

    Returns:
        ArctanResult（level_count_clamped / raw_level_count / levels）。
        因果窓時は warm-up（先頭 window-1）が NaN（非描画）。

    Raises:
        ValueError: OHLC の長さが不一致の場合。
    """
    o = np.asarray(open_, dtype=np.float64)
    h = np.asarray(high, dtype=np.float64)
    low_a = np.asarray(low, dtype=np.float64)
    c = np.asarray(close, dtype=np.float64)
    if not (o.size == h.size == low_a.size == c.size):
        raise ValueError(
            f"OHLC の長さが不一致です: {[o.size, h.size, low_a.size, c.size]}"
        )

    raw = compute_level_count(
        o, h, low_a, c, period=period, ma_method=ma_method, bar_width=bar_width, window=window,
        freeze_last=freeze_last,
    )
    levels = compute_arctan_levels(raw)
    upper = levels["up_329"]
    lower = levels["dn_329"]
    clamped = np.clip(raw, lower, upper)  # NaN（warm-up）は NaN のまま温存
    return ArctanResult(
        level_count_clamped=clamped,
        raw_level_count=raw,
        levels=levels,
    )
