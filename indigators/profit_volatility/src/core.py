"""PRO!fit_Volatility のコア（純粋ロジック・numpy のみ）。

層名/責務:
    core 層。元 MQL4 ``PRO!fit_Volatility.mq4`` + 依存ライブラリ ``ProfitSystem/PS.mqh`` の
    ``iVOLATILITY`` の数値計算「概念」だけを純粋関数として保持する。バッファ番号・描画色・
    別ウィンドウ指定・``IndicatorSetXxx`` は偶有的性質であり本層には持ち込まない
    （依存は常に内向き）。

指標の概念（iVOLATILITY「価格乖離」オシレーター）:
    1. iVOLATILITY を 49 系列（X∈0..6 × Y∈0..6 = price_A × price_B）で算出する。
       各系列は ``res[a] = pX[a] - pY[a-period]``（現足の価格 X と period 本前の
       価格 Y の乖離）。``a < period``（元 OnCalculate の `for i<limit-inpPeriod` で未計算）は ``res=0``。
    2. 各 iVOLATILITY 系列を「系列平均からの距離 / EMA 標準偏差」へ単位変換し 49 回加算する
       （= レベルカウント）。
    3. レベルカウント系列に σ バンド（0.67〜3.29σ）を当てて水準線とし、±3.29σ で
       クランプする。

元 MQL4 / PS.mqh の対応:
    * ``iVOLATILITY(...)``（PS.mqh）                  → ``compute_volatility``
    * 49 回の ``PS_GetLevelCountValue`` 呼び出し       → ``compute_level_count``
    * ``PS_GetLevelCountValue`` / ``PS_GetUnitConversion`` → ``ps_level_count`` / ``_unit_conversion``
      （共有 profit_system の再公開。profit_adx_needle と同一実装を参照）
    * ``iBandsOnArray(...)``                          → ``compute_sigma_levels``
      （共有 profit_system の再公開。別名 ``compute_volatility_levels``）
    * ``ExtBufferLevelCount`` の ±3.29σ クランプ       → ``compute_volatility_full`` の clip

移植上の重要判断（元挙動 1:1 再現）:
    iVOLATILITY の applied_price は 2 桁コード ``XY``（X=1 桁目=price_A=x_digit,
    Y=2 桁目=price_B=y_digit, 各 0..6 = Close/Open/High/Low/Median/Typical/Weighted）で
    49 系列を表す。各系列は「現足の X 価格」と「period 本前の Y 価格」の乖離
    ``res[a]=pX[a]-pY[a-period]`` である（arctan のような MA 隣接差ではない）。
    **median/typical/weighted は素の MT4 式**（median=(H+L)/2, typical=(H+L+C)/3,
    weighted=(H+L+C+O)/4）であり、common.applied_price の weighted=(H+L+2C)/4 とは
    異なるため、本 core は iVOLATILITY の式をそのまま実装する。warm-up（``a<period``）は
    元 OnCalculate のループが計算せず 0 を残す挙動を 1:1 再現し ``res=0``。
    ``ps_level_count`` / ``compute_sigma_levels`` は共有 profit_system を再利用
    （キー名・定数も保持）。元 OnCalculate では mode 00（X=0,Y=0）が initialization=1、
    残り 48 系列が加算（= 49 系列の単位変換値の総和）。

依存:
    標準: dataclasses, sys, pathlib, typing / 外部: numpy /
    プロジェクト内: profit_system（PS プリミティブ: ps_level_count /
    compute_sigma_levels / SIGMA_LEVELS）
"""

from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping

import numpy as np

# PS レベルカウント系プリミティブは共有層 profit_system に集約済み（indicators 配下）。
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))  # indicators → profit_system

from profit_system import (  # noqa: E402
    SIGMA_LEVELS,
    compute_sigma_levels,
    ps_level_count,
)

# 既定パラメータ（元 ``input int inpPeriod = 6``）。
DEFAULT_PERIOD: int = 6

# iVOLATILITY の 2 桁 case コード ``XY`` の digit 価格種別（MQL4 流・0 始まり）。
# 0=Close,1=Open,2=High,3=Low,4=Median,5=Typical,6=Weighted。
# X=1 桁目=price_A（現足側 x_digit）、Y=2 桁目=price_B（period 本前側 y_digit）。
_PRICE_DIGITS: tuple[int, ...] = (0, 1, 2, 3, 4, 5, 6)

# 49 modes の (x_digit, y_digit) 出現順: 00,01..06, 10..16, ..., 60..66
# （X=0..6 を外側ループ・Y=0..6 を内側ループ。元 OnCalculate の case 00..66 順）。
VOLATILITY_MODES: tuple[tuple[int, int], ...] = tuple(
    (x, y) for x in _PRICE_DIGITS for y in _PRICE_DIGITS
)

# ======================================================================= iVOLATILITY
def _vol_price(digit: int, open_: np.ndarray, high: np.ndarray,
               low: np.ndarray, close: np.ndarray) -> np.ndarray:
    """iVOLATILITY の素の MT4 価格式で 1 digit の価格系列を返す。

    元 ``PS.mqh iVOLATILITY`` の case 群で使われる価格定義に厳密一致させる
    （digit は MQL4 流の 0 始まり）:
        0=Close, 1=Open, 2=High, 3=Low,
        4=Median=(H+L)/2, 5=Typical=(H+L+C)/3, 6=Weighted=(H+L+C+O)/4。
    common.applied_price とは weighted の係数（(H+L+2C)/4）が異なるため独自に実装する。

    Args:
        digit: 価格種別の桁（0..6）。
        open_/high/low/close: OHLC 各系列（昇順・同長）。

    Returns:
        指定 digit の価格系列（float64）。

    Raises:
        ValueError: ``digit`` が 0..6 のいずれにも該当しない場合。
    """
    o = np.asarray(open_, dtype=np.float64)
    h = np.asarray(high, dtype=np.float64)
    low_a = np.asarray(low, dtype=np.float64)
    c = np.asarray(close, dtype=np.float64)
    if digit == 0:
        return c
    if digit == 1:
        return o
    if digit == 2:
        return h
    if digit == 3:
        return low_a
    if digit == 4:
        return (h + low_a) / 2.0
    if digit == 5:
        return (h + low_a + c) / 3.0
    if digit == 6:
        return (h + low_a + c + o) / 4.0
    raise ValueError(f"未知の価格 digit です: {digit!r}")


def compute_volatility(
    open_: np.ndarray,
    high: np.ndarray,
    low: np.ndarray,
    close: np.ndarray,
    *,
    period: int,
    x_digit: int,
    y_digit: int,
) -> np.ndarray:
    """元 PS.mqh ``iVOLATILITY`` の 1 系列（X[a]-Y[a-period]）を昇順で 1:1 再現する。

    mode は 2 桁コード ``XY``（X=1 桁目=price_A=現足側 x_digit、Y=2 桁目=price_B=
    period 本前側 y_digit、各 0..6）。元コードは
    ``res = priceX(shift) - priceY(shift+period)`` で、shift は MT4 の
    「新しい足ほど小さい index」。昇順（古い→新しい）に直すと
    ``res[a] = pX[a] - pY[a-period]`` となる。指示書「確定セマンティクス」に従い
    warm-up（``a < period``）は元 MQL4 OnCalculate（``for i<limit-inpPeriod``）が
    計算せず 0 を残す挙動を 1:1 再現し ``res=0`` とする（ISSUE-002 解決済み）。

    Args:
        open_/high/low/close: OHLC 各系列（昇順・同長）。
        period: 乖離をとる足数（>=2。元 inpPeriod=6）。
        x_digit: 現足側 A の価格 digit（0..6）。
        y_digit: period 本前 B の価格 digit（0..6）。

    Returns:
        iVOLATILITY 系列（同長, float64）。``a < period`` は ``res=0``（未計算）。

    Raises:
        ValueError: ``period < 2`` または digit が未知（0..6 外）の場合。
    """
    if period < 2:
        raise ValueError(f"period は 2 以上である必要があります: {period}")
    px = _vol_price(x_digit, open_, high, low, close)
    py = _vol_price(y_digit, open_, high, low, close)
    n = px.size
    # 元 OnCalculate のループは for(i=0; i<limit-inpPeriod; i++) であり、最古 period
    # 本（昇順 a<period）は計算されず 0 のまま残る（ArrayResize 既定値）。1:1 再現。
    out = np.zeros(n, dtype=np.float64)
    for a in range(period, n):
        out[a] = px[a] - py[a - period]
    return out


# ===================================================== 49 系列集計 / 別名 / 一括計算
def compute_level_count(
    open_: np.ndarray,
    high: np.ndarray,
    low: np.ndarray,
    close: np.ndarray,
    *,
    period: int,
) -> np.ndarray:
    """49 系列の iVOLATILITY を単位変換・加算したレベルカウント系列を返す。

    元 OnCalculate の 49 回の ``PS_GetLevelCountValue`` 呼び出し（mode 00=X:0,Y:0 が
    初期化、残り 48 系列が加算）を再現する。``VOLATILITY_MODES`` の出現順
    （00,01..06,10..16,...,66）に一致させる。

    Args:
        open_/high/low/close: OHLC 各系列（昇順・同長）。
        period: 乖離をとる足数。

    Returns:
        レベルカウント系列（同長, float64）。
    """
    level_count: np.ndarray | None = None
    for k, (x_digit, y_digit) in enumerate(VOLATILITY_MODES):
        vol = compute_volatility(
            open_, high, low, close, period=period, x_digit=x_digit, y_digit=y_digit
        )
        # 元コードでは mode 00 のみ initialization=1、残りは 0（加算）。
        level_count = ps_level_count(vol, level_count, initialization=(k == 0))
    assert level_count is not None
    return level_count


def compute_volatility_levels(level_count: np.ndarray) -> Mapping[str, float]:
    """σ12 水準線（= ``compute_sigma_levels`` の別名）。複製元のキー名を保持する。"""
    return compute_sigma_levels(level_count)


@dataclass(frozen=True)
class VolatilityResult:
    """PRO!fit_Volatility の計算成果（数値のみ・描画非依存）。

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


def compute_volatility_full(
    open_: np.ndarray,
    high: np.ndarray,
    low: np.ndarray,
    close: np.ndarray,
    *,
    period: int = DEFAULT_PERIOD,
) -> VolatilityResult:
    """iVOLATILITY レベルカウント（クランプ済み）を一括算出する。

    元 OnCalculate の全体（49 系列 iVOLATILITY → レベルカウント加算 → σ 水準 →
    ±3.29σ クランプ）を再現する。

    Args:
        open_/high/low/close: OHLC 各系列（昇順・同長）。
        period: 乖離をとる足数（既定 6）。

    Returns:
        VolatilityResult（level_count_clamped / raw_level_count / levels）。

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

    raw = compute_level_count(o, h, low_a, c, period=period)
    levels = compute_volatility_levels(raw)
    upper = levels["up_329"]
    lower = levels["dn_329"]
    clamped = np.clip(raw, lower, upper)
    return VolatilityResult(
        level_count_clamped=clamped,
        raw_level_count=raw,
        levels=levels,
    )
