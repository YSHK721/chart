"""ProfitSystem PS.mqh プリミティブのコア（純粋ロジック・numpy のみ）。

層名/責務:
    core 層（共有プリミティブ）。元 MQL4 ``ProfitSystem/PS.mqh`` の
    レベルカウント系数値計算「概念」だけを純粋関数として保持する。
    バッファ番号・描画色・別ウィンドウ指定・``IndicatorSetXxx``（レベル線設定）は
    偶有的性質であり本層には持ち込まない（依存は常に内向き）。

提供するプリミティブ（profit_adx_needle/src/core.py から無改変集約）:
    * ``PS_GetLevelCountValue``  → ``ps_level_count``
    * ``PS_GetUnitConversion``   → ``_unit_conversion``
    * ``iBandsOnArray(...,deviation,...,MODE_UPPER/LOWER,0)`` → ``compute_sigma_levels``
    * ``PS_GetAverage`` / ``PS_GetStandardDeviationValue``   → ``_ps_average`` / ``_ps_std_ema`` / ``_ps_band``
    * ``iMAOnArray(..., MODE_EMA)`` → ``_ema``
    * ``NormalizeDouble(_, 5)``    → ``_normalize``

追加集約（profit_rmm/src/core.py の正準形を無改変集約。AST 一致を確認した
profit_rmm / profit_rmm_macd / profit_oscillator2 の重複定義を統一する）:
    * ``funLevelCount`` (4 ケース採点)  → ``level_count_score``
    * ``MAROD = iMARD/MAROD`` ((typical-ma)/ma*100) → ``compute_marod``
    （注: profit_oscillator の ``compute_mard`` は OHLC＋applied 引数で内部 EMA を計算する
      別シグネチャの変種であり、本層には集約しない。）

依存:
    標準: typing / 外部: numpy / プロジェクト内: なし
    （指標パッケージには依存しない。循環依存を作らない。）
"""

from __future__ import annotations

from typing import Mapping

import numpy as np

# σ 水準（元 #define SIGMA_L1..L6。信頼区間 75/90/95/97.5/99/99.9%）。
SIGMA_LEVELS: tuple[float, ...] = (0.67, 1.28, 1.65, 1.96, 2.58, 3.29)

# 単位変換の基準距離（元 #define PS_SIGMA_DISTANCE_L6 = 329）と σ（SIGMA_L6 = 3.29）。
_SIGMA_DISTANCE_L6: float = 329.0
_SIGMA_L6: float = 3.29

# MQL の NormalizeDouble(_, 5) に対応する丸め桁。
_NORMALIZE_DECIMALS: int = 5

# PS_UPSIDE / PS_DOWNSIDE（GetStandardDeviationValue / UnitConversion の mode）。
_UPSIDE: int = 1
_DOWNSIDE: int = 2


def _normalize(x: float) -> float:
    """MQL ``NormalizeDouble(x, 5)`` 相当（小数 5 桁へ丸める）。"""
    return float(round(x, _NORMALIZE_DECIMALS))


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


def _ps_average(array: np.ndarray) -> float:
    """元 ``PS_GetAverage`` 相当（算術平均, NormalizeDouble 5）。"""
    return _normalize(float(np.mean(array)))


def _ps_std_ema(array: np.ndarray) -> float:
    """元 ``iStdDevOnArray(array,0,length,0,MODE_EMA,0)`` 相当の標準偏差。

    全要素を対象に MA=EMA（period=length, α=2/(length+1)）の最終値 ma を基準とし、
    ``sqrt( mean( (array-ma)^2 ) )``（母分散ベース）を返す。元 PS では本値に σ を掛けて
    上下バンドを作り、単位変換の基準距離に用いる。

    Args:
        array: 対象系列（昇順, 1 次元）。

    Returns:
        EMA 基準の標準偏差。
    """
    a = np.asarray(array, dtype=np.float64)
    length = a.size
    if length == 0:
        return 0.0
    ma = _ema(a, length)[-1]
    return float(np.sqrt(np.mean((a - ma) ** 2)))


def _ps_band(array: np.ndarray, sigma: float, mode: int) -> float:
    """元 ``PS_GetStandardDeviationValue`` 相当（平均 ± σ×EMA標準偏差, Normalize 5）。"""
    avg = _ps_average(array)
    std = _ps_std_ema(array)
    if mode == _UPSIDE:
        return _normalize(avg + std * sigma)
    return _normalize(avg - std * sigma)


def _unit_conversion(
    osi: float, avg: float, band: float, distant: float, mode: int
) -> float:
    """元 ``PS_GetUnitConversion`` 相当（オシレーター値を σ 距離単位へ変換）。

    ``length = (band - avg) / distant`` とし、
        mode=UPSIDE  : res = ((osi-avg)/length)/100
        mode=DOWNSIDE: res = ((avg-osi)/length)/100
    を NormalizeDouble(5) で返す。``length==0`` のときは 0（元のガード相当）。

    符号に注意: 下方分岐では band=avg-σ×std（< avg）のため length<0 となり、
    ``res`` は負になる。すなわち両分岐とも ``res ≒ (osi-avg)/std``（平均超で正・
    平均未満で負）の符号付き標準化量に帰着する（丸めを除く）。

    Args:
        osi: オシレーター現在値。
        avg: 系列平均。
        band: 上方/下方バンド値（= avg ± σ×std）。
        distant: 基準距離（元 PS_SIGMA_DISTANCE_L6 = 329）。
        mode: PS_UPSIDE(1) / PS_DOWNSIDE(2)。

    Returns:
        単位変換後の符号付き値（Normalize 5）。
    """
    length = (band - avg) / distant
    if length == 0.0:
        return 0.0
    if mode == _UPSIDE:
        res = ((osi - avg) / length) / 100.0
    else:
        res = ((avg - osi) / length) / 100.0
    return _normalize(res)


def ps_level_count(
    array: np.ndarray,
    res: np.ndarray | None = None,
    *,
    initialization: bool = False,
    sigma: float = _SIGMA_L6,
    distant: float = _SIGMA_DISTANCE_L6,
) -> np.ndarray:
    """元 ``PS_GetLevelCountValue`` 相当（系列を「平均からの σ 距離」へ変換・加算）。

    各 i について平均との大小で UPSIDE/DOWNSIDE の単位変換を選び、``res`` に加算する。
    ``initialization=True`` のとき先に ``res[i]=0`` で初期化する（元の第 1 引数）。
    同値（array[i]==avg）は 0。単位変換は符号付き（平均超で正・平均未満で負、
    ``_unit_conversion`` 参照）。

    Args:
        array: 適用価格別オシレーター系列（昇順）。
        res: 加算先（None なら 0 で新規）。
        initialization: True で res を 0 初期化してから加算。
        sigma: バンド σ（既定 3.29 = SIGMA_L6）。
        distant: 基準距離（既定 329 = PS_SIGMA_DISTANCE_L6）。

    Returns:
        加算後のレベルカウント配列（array と同長）。
    """
    a = np.asarray(array, dtype=np.float64)
    n = a.size
    out = np.zeros(n, dtype=np.float64) if res is None else np.array(res, dtype=np.float64)
    if initialization:
        out[:] = 0.0

    avg = _ps_average(a)
    up = _ps_band(a, sigma, _UPSIDE)
    down = _ps_band(a, sigma, _DOWNSIDE)

    # ループ版 ``_unit_conversion`` をベクトル化（要素ごとの分岐を np.where で表現）。
    # avg/up/down は系列全体のスカラなので length も系列内で一定。
    # ``length == 0``（= 母分散 0 の定数系列）のときは ``_unit_conversion`` のガードと同じく
    # 寄与 0 とする（その場合 a[i] > avg / < avg は成立せず else 分岐に落ちるため挙動も等価）。
    # np.round(_, 5) はスカラ ``round(_, 5)`` とビット一致することを乱数掃引で確認済み。
    up_length = (up - avg) / distant
    down_length = (down - avg) / distant
    res_up = (
        np.zeros(n, dtype=np.float64)
        if up_length == 0.0
        else np.round(((a - avg) / up_length) / 100.0, _NORMALIZE_DECIMALS)
    )
    res_down = (
        np.zeros(n, dtype=np.float64)
        if down_length == 0.0
        else np.round(((avg - a) / down_length) / 100.0, _NORMALIZE_DECIMALS)
    )
    # a[i] > avg は加算（UPSIDE）、a[i] < avg は加算（DOWNSIDE）、a[i] == avg は 0 で上書き。
    return np.where(a > avg, out + res_up, np.where(a < avg, out + res_down, 0.0))


def compute_sigma_levels(level_count: np.ndarray) -> Mapping[str, float]:
    """元 ``iBandsOnArray`` 相当の σ 水準線（上方 6 本・下方 6 本）を求める。

    レベルカウント全長の SMA（= 平均）と母標準偏差を基準に、各 σ（0.67〜3.29）で
    ``mean ± σ×std`` を計算する。元 StdDevArray[1..6]=上方, [7..12]=下方 に対応。

    Args:
        level_count: レベルカウント系列。

    Returns:
        キー ``up_067``..``up_329`` / ``dn_067``..``dn_329`` → 水準値（float）。
    """
    x = np.asarray(level_count, dtype=np.float64)
    mean = float(np.mean(x))
    std = float(np.sqrt(np.mean((x - mean) ** 2)))  # 母標準偏差（MT4 iBands と同じ）
    levels: dict[str, float] = {}
    for sigma in SIGMA_LEVELS:
        key = f"{int(round(sigma * 100)):03d}"
        levels[f"up_{key}"] = _normalize(mean + std * sigma)
        levels[f"dn_{key}"] = _normalize(mean - std * sigma)
    return levels


# ===========================================================================
# MAROD（iMARD/MAROD・profit_rmm の正準形を無改変集約）
# ===========================================================================
def compute_marod(typical: np.ndarray, ma: np.ndarray) -> np.ndarray:
    """(typical - ma) / ma * 100 を返す（float 精度・int 切り捨て無し）。"""
    typical = np.asarray(typical, dtype=np.float64)
    ma = np.asarray(ma, dtype=np.float64)
    return (typical - ma) / ma * 100.0


# ===========================================================================
# funLevelCount（採点・ゼロ割ガードなし 1:1・profit_rmm の正準形を無改変集約）
# ===========================================================================
def level_count_score(osi: float, span: float, case: int) -> float:
    """funLevelCount の 4 ケース採点を返す（ゼロ割ガードなし・1:1 再現）。

    ::

        case0: r=(span-50)/200; return ((osi-50)/r)/100
        case1: r=(span-50)/200; return -((50-osi)/r)/100
        case2: r=(span/2)/200;  return ((osi-r)/r)/100
        case3: r=(span/2)/200;  return -((r-osi)/r)/100

    ゼロ割はガードしない。退化入力（span==50 / span==0）は inf/nan を許容する
    （1:1 再現）。numpy float 演算で例外を投げず inf/nan を返す。

    Args:
        osi: オシレーター現在値。
        span: 当該オシレーターのスパン。
        case: 0/1/2/3。

    Returns:
        採点値（float。退化時 inf/nan）。
    """
    osi = np.float64(osi)
    span = np.float64(span)
    if case == 0:
        r = (span - 50.0) / 200.0
        return float(((osi - 50.0) / r) / 100.0)
    if case == 1:
        r = (span - 50.0) / 200.0
        return float(-((50.0 - osi) / r) / 100.0)
    if case == 2:
        r = (span / 2.0) / 200.0
        return float(((osi - r) / r) / 100.0)
    # case == 3
    r = (span / 2.0) / 200.0
    return float(-((r - osi) / r) / 100.0)
