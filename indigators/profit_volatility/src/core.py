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

# 因果標準化の既定窓長 W（直近 W 本の過去のみで平均・ばらつきを算出 = look-ahead 除去）。
# 日足で ~半年分。少標本で z が頭打ちにならない長さ（>=60 推奨）。``window=None`` で全期間
# バッチ（look-ahead あり・比較用）に切り替わる。
DEFAULT_WINDOW: int = 120

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


# ===================================================== 本質コア（OHLC4 対数変化・1 系列）
# 49 系列（X∈0..6 × Y∈0..6）の合算は、実証上 第1主成分が分散の 94.4% を占め、加重値
# （OHLC4）どうしの 6 本変化 1 本で合算の 100% を再現できる（実効独立次元 ≒ 1）。本コアは
# その「本質 1 本」だけを保持し、さらに乖離を **値幅 X-Y → 対数差 ln(X/Y)** に変えて
# 価格水準依存（値幅は水準に比例し非定常）を除去する。標準化（σ 距離）は元 ps_level_count が
# 代数的に z=(d-avg)/std に帰着するのと同義の素直な z 化で実装する。
def _ohlc4(open_: np.ndarray, high: np.ndarray, low: np.ndarray,
           close: np.ndarray) -> np.ndarray:
    """加重値 OHLC4 = (O+H+L+C)/4 を返す（iVOLATILITY digit=6 と同式）。"""
    o = np.asarray(open_, dtype=np.float64)
    h = np.asarray(high, dtype=np.float64)
    low_a = np.asarray(low, dtype=np.float64)
    c = np.asarray(close, dtype=np.float64)
    return (o + h + low_a + c) / 4.0


def compute_core_divergence(
    open_: np.ndarray,
    high: np.ndarray,
    low: np.ndarray,
    close: np.ndarray,
    *,
    period: int = DEFAULT_PERIOD,
) -> np.ndarray:
    """加重値(OHLC4)の period 本「対数変化」 d[a]=ln(ohlc4[a]/ohlc4[a-period]) を返す。

    値幅 ``ohlc4[a]-ohlc4[a-period]`` は価格水準に比例して大きくなり非定常（水準依存）。
    対数差（＝比率の対数）にすることで「同じ % の動き」が価格水準に依らず同じ値になり、
    スケール不変になる。warm-up（``a<period``）は算出不能のため ``NaN``（非描画・統計から除外）。

    Args:
        open_/high/low/close: OHLC 各系列（昇順・同長・正値）。
        period: 変化をとる足数（>=2。既定 6）。

    Returns:
        対数変化系列（同長, float64）。``a<period`` は ``NaN``。

    Raises:
        ValueError: ``period < 2`` の場合。
    """
    if period < 2:
        raise ValueError(f"period は 2 以上である必要があります: {period}")
    w = _ohlc4(open_, high, low, close)
    n = w.size
    d = np.full(n, np.nan, dtype=np.float64)
    if n > period:
        d[period:] = np.log(w[period:] / w[:-period])
    return d


def _standardize(values: np.ndarray) -> np.ndarray:
    """NaN を除外した平均・母標準偏差で z 化する（warm-up NaN はそのまま温存）。

    ``z = (x - mean) / std``（std は母標準偏差 ÷N）。元 PS の σ 距離単位変換は代数的に
    この z に帰着する（定数 3.29/329/100 が相殺）。``std==0`` のとき有効点は 0。
    """
    v = np.asarray(values, dtype=np.float64)
    valid = ~np.isnan(v)
    out = np.full(v.size, np.nan, dtype=np.float64)
    if not valid.any():
        return out
    vals = v[valid]
    mean = float(np.mean(vals))
    std = float(np.sqrt(np.mean((vals - mean) ** 2)))
    out[valid] = (vals - mean) / std if std > 0.0 else 0.0
    return out


def _standardize_causal(values: np.ndarray, window: int) -> np.ndarray:
    """因果ローリング窓で z 化する（look-ahead 除去）。

    各バー a の標準化基準（平均・母標準偏差）を **直近 window 本の過去データのみ**
    （バー区間 ``[a-window+1, a]``）から計算する。未来は一切入れないため、確定した
    バーの値は後から新データを足しても変わらない（repaint しない）。

    warm-up（先頭 NaN 区間 ＋ 窓を満たさない区間）は ``NaN``（非描画）。すなわち最初の
    有効点は ``period + window - 1`` 付近（窓が全て有限値で満たされる最初のバー）。

    Args:
        values: 乖離系列（先頭 period 本が NaN、以降有限）。
        window: 過去参照本数 W（>=2）。

    Returns:
        因果標準化系列（同長, float64。warm-up は NaN）。
    """
    v = np.asarray(values, dtype=np.float64)
    n = v.size
    out = np.full(n, np.nan, dtype=np.float64)
    if window < 2 or n == 0:
        return out
    finite = ~np.isnan(v)
    if not finite.any():
        return out
    start = int(np.argmax(finite))  # 先頭の有限 index（= period）
    # NaN を 0 に置いた作業配列で累積和を作る（有効窓は全て有限なので 0 置換の影響なし）。
    w0 = np.where(finite, v, 0.0)
    csum = np.concatenate([[0.0], np.cumsum(w0)])
    csq = np.concatenate([[0.0], np.cumsum(w0 * w0)])
    first = start + window - 1  # 窓が全て有限値で満たされる最初のバー
    for a in range(first, n):
        lo = a - window + 1
        s = csum[a + 1] - csum[lo]
        sq = csq[a + 1] - csq[lo]
        mean = s / window
        var = sq / window - mean * mean
        std = np.sqrt(var) if var > 0.0 else 0.0
        out[a] = (v[a] - mean) / std if std > 0.0 else 0.0
    return out


@dataclass(frozen=True)
class CoreVolatilityResult:
    """本質コア（OHLC4 対数変化・標準化 1 系列）の計算成果（描画非依存）。

    Attributes:
        level_count_clamped: ±3.29σ クランプ後の標準化系列（描画対象, N,。warm-up は NaN）。
        raw_level_count: クランプ前の標準化系列（N,。warm-up は NaN）。
        divergence: OHLC4 の対数変化 d=ln(ohlc4[a]/ohlc4[a-period])（N,。warm-up は NaN）。
        levels: σ 水準線（up_*/dn_*。warm-up を除く有効点から算出）。
    """

    level_count_clamped: np.ndarray
    raw_level_count: np.ndarray
    divergence: np.ndarray
    levels: Mapping[str, float]

    def __post_init__(self) -> None:
        for name in ("level_count_clamped", "raw_level_count", "divergence"):
            arr = np.asarray(getattr(self, name), dtype=np.float64)
            arr.setflags(write=False)  # DTO は不変
            object.__setattr__(self, name, arr)


def compute_core_volatility(
    open_: np.ndarray,
    high: np.ndarray,
    low: np.ndarray,
    close: np.ndarray,
    *,
    period: int = DEFAULT_PERIOD,
    window: int | None = None,
) -> CoreVolatilityResult:
    """本質コア（OHLC4 の対数 period 本変化を標準化した 1 系列）を一括算出する。

    49 系列合算の本質（実効 1 次元）だけを残し、価格水準依存を対数差で除去したパターン。
    σ12 水準（``compute_sigma_levels``）と ±3.29σ クランプは表示継続のため維持する
    （水準・クランプは warm-up NaN を除く有効点から算出）。

    標準化の基準（平均・ばらつき）の算出範囲:
        * ``window=None``: 全期間バッチ（look-ahead あり。比較・参照用）。
        * ``window=W``: 因果ローリング窓（直近 W 本の過去のみ。look-ahead 除去。
          確定したバーは repaint しない）。warm-up は ``period + W - 1`` 付近まで。

    Args:
        open_/high/low/close: OHLC 各系列（昇順・同長・正値）。
        period: 変化をとる足数（既定 6 = 測定幅）。
        window: 標準化窓 W（直近参照本数）。None で全期間バッチ。

    Returns:
        CoreVolatilityResult（level_count_clamped / raw_level_count / divergence / levels）。

    Raises:
        ValueError: OHLC の長さが不一致、または ``period < 2`` の場合。
    """
    o = np.asarray(open_, dtype=np.float64)
    h = np.asarray(high, dtype=np.float64)
    low_a = np.asarray(low, dtype=np.float64)
    c = np.asarray(close, dtype=np.float64)
    if not (o.size == h.size == low_a.size == c.size):
        raise ValueError(
            f"OHLC の長さが不一致です: {[o.size, h.size, low_a.size, c.size]}"
        )

    d = compute_core_divergence(o, h, low_a, c, period=period)
    z = _standardize(d) if window is None else _standardize_causal(d, window)
    valid = ~np.isnan(z)
    levels = compute_sigma_levels(z[valid] if valid.any() else np.zeros(1))
    upper = levels["up_329"]
    lower = levels["dn_329"]
    clamped = np.clip(z, lower, upper)  # NaN（warm-up）は NaN のまま温存
    return CoreVolatilityResult(
        level_count_clamped=clamped,
        raw_level_count=z,
        divergence=d,
        levels=levels,
    )
