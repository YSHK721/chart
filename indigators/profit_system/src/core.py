"""ProfitSystem PS.mqh プリミティブのコア（純粋ロジック・numpy のみ）。

層名/責務:
    core 層（共有プリミティブ）。元 MQL4 ``ProfitSystem/PS.mqh`` の
    レベルカウント系数値計算「概念」だけを純粋関数として保持する。
    バッファ番号・描画色・別ウィンドウ指定・``IndicatorSetXxx``（レベル線設定）は
    偶有的性質であり本層には持ち込まない（依存は常に内向き）。

提供するプリミティブ（profit_adx_needle/src/core.py から無改変集約）:
    * ``PS_GetLevelCountValue``  → ``ps_level_count``
    * ``PS_GetUnitConversion``   → ``ps_unit_conversion``
    * ``iBandsOnArray(...,deviation,...,MODE_UPPER/LOWER,0)`` → ``compute_sigma_levels``
    * ``PS_GetAverage`` / ``PS_GetStandardDeviationValue``   → ``ps_average`` / ``ps_std_ema`` / ``_ps_band``
    * ``iMAOnArray(..., MODE_EMA)`` → ``_ema``
    * ``NormalizeDouble(_, 5)``    → ``ps_normalize``

公開契約について（ISSUE-182 項目 1）:
    ``ps_normalize`` / ``ps_average`` / ``ps_std_ema`` / ``ps_unit_conversion`` は
    パッケージ境界を越えて参照される実績がある（profit_adx_needle）。したがって
    アンダースコア名のままにせず **public 名へ昇格し ``__all__`` に載せる**。
    旧名（``_normalize`` / ``_ps_average`` / ``_ps_std_ema`` / ``_unit_conversion``）は
    既存参照面を壊さないため **同一オブジェクトの別名**として残置する（値は不変）。
    ``_ema`` / ``_ps_band`` / ``_causal_z`` は越境参照が無いため private のまま。

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


def ps_normalize(x: float) -> float:
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


def ps_average(array: np.ndarray) -> float:
    """元 ``PS_GetAverage`` 相当（算術平均, NormalizeDouble 5）。"""
    return ps_normalize(float(np.mean(array)))


def ps_std_ema(array: np.ndarray) -> float:
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
    avg = ps_average(array)
    std = ps_std_ema(array)
    if mode == _UPSIDE:
        return ps_normalize(avg + std * sigma)
    return ps_normalize(avg - std * sigma)


def ps_unit_conversion(
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
    return ps_normalize(res)


def _causal_z(array: np.ndarray, window: int, *, freeze_last: bool = False) -> np.ndarray:
    """因果ローリング窓の σ 距離（z）を返す（look-ahead 除去）。

    各バー i の基準（平均・母標準偏差）を **直近 window 本の過去のみ**（区間
    ``[i-window+1, i]``）から算出し ``z[i]=(a[i]-mean_i)/std_i`` を返す。未来を含まない
    ため確定バーは repaint しない。窓を満たさない先頭（``i < window-1``）は ``NaN``。
    符号は平均超で正・平均未満で負（元 ``_unit_conversion`` と同義の σ 距離）。

    全期間版の EMA 基準 std（``_ps_std_ema``）は系列長依存・seed バイアスがあるため、
    因果版では窓内の母標準偏差を用いる（忠実移植は放棄済み・統計的に健全な定義）。

    ``freeze_last``（既定 ``False``）:
        * ``False``: 上記の通り（既定。出力は 1 ビットも変えない）。
        * ``True``: **最終要素 ``out[-1]`` のみ** 基準窓を確定足
          ``[n-1-window .. n-2]``（最終点を除く直前 window 本）へ差し替え、
          ``z = (a[-1] - mean_prior) / std_prior`` を算出する。``out[0..n-2]`` は
          ``freeze_last=False`` と完全に同一。形成中（足内）の最新足をティック粒度で
          評価する際、標準化基準を 1 足 1 回・足内で固定（凍結）する用途。
          mean/std は本関数の窓内統計（母分散・分母 ``window``）と厳密に同一定義で、
          最終点だけ窓をずらした以外は数値が一致する。直前 window 本が満たせない
          （``n < window + 1``）場合は ``out[-1]=NaN``（warmup と同様）。``std==0`` 等の
          縮退時は ``freeze_last=False`` と同じく ``0.0``。
    """
    a = np.asarray(array, dtype=np.float64)
    n = a.size
    out = np.full(n, np.nan, dtype=np.float64)
    if window < 2 or n < window:
        return out
    csum = np.concatenate([[0.0], np.cumsum(a)])
    csq = np.concatenate([[0.0], np.cumsum(a * a)])
    for i in range(window - 1, n):
        lo = i - window + 1
        mean = (csum[i + 1] - csum[lo]) / window
        var = (csq[i + 1] - csq[lo]) / window - mean * mean
        std = np.sqrt(var) if var > 0.0 else 0.0
        out[i] = ps_normalize((a[i] - mean) / std) if std > 0.0 else 0.0
    if freeze_last:
        # 最終点 out[-1] のみ、基準窓を確定足 [n-1-window .. n-2]（最終点を除く直前
        # window 本）へ差し替える。out[0..n-2] は上のループ結果のまま不変。
        if n < window + 1:
            out[-1] = np.nan  # 直前 window 本を満たせない（warmup 同様）。
        else:
            lo = n - 1 - window  # 直前 window 本 = a[lo:n-1]（= a[n-1-window .. n-2]）。
            hi = n - 1
            mean = (csum[hi] - csum[lo]) / window
            var = (csq[hi] - csq[lo]) / window - mean * mean
            std = np.sqrt(var) if var > 0.0 else 0.0
            out[-1] = ps_normalize((a[-1] - mean) / std) if std > 0.0 else 0.0
    return out


def ps_level_count(
    array: np.ndarray,
    res: np.ndarray | None = None,
    *,
    initialization: bool = False,
    sigma: float = _SIGMA_L6,
    distant: float = _SIGMA_DISTANCE_L6,
    window: int | None = None,
    freeze_last: bool = False,
) -> np.ndarray:
    """元 ``PS_GetLevelCountValue`` 相当（系列を「平均からの σ 距離」へ変換・加算）。

    各 i について平均との大小で UPSIDE/DOWNSIDE の単位変換を選び、``res`` に加算する。
    ``initialization=True`` のとき先に ``res[i]=0`` で初期化する（元の第 1 引数）。
    同値（array[i]==avg）は 0。単位変換は符号付き（平均超で正・平均未満で負、
    ``_unit_conversion`` 参照）。

    標準化基準（平均・ばらつき）の算出範囲:
        * ``window=None``: 全期間バッチ（元 PS 1:1。look-ahead あり）。
        * ``window=W``: 因果ローリング窓（直近 W 本の過去のみ。look-ahead 除去・
          repaint しない）。窓未充足の先頭（``i<W-1``）は ``NaN`` を加算＝合算も NaN
          （非描画）。各系列が同一窓なら warm-up 区間は全系列 NaN で整合する。

    Args:
        array: 適用価格別オシレーター系列（昇順）。
        res: 加算先（None なら 0 で新規）。
        initialization: True で res を 0 初期化してから加算。
        sigma: バンド σ（既定 3.29 = SIGMA_L6。window 指定時は未使用）。
        distant: 基準距離（既定 329。window 指定時は未使用）。
        window: 因果窓 W（直近参照本数）。None で全期間バッチ。
        freeze_last: True かつ ``window is not None`` のとき、最終点の標準化基準を
            確定足（直前 W 本）に凍結する（``_causal_z`` 参照）。``window=None``
            （全期間バッチ）経路では無関係（未使用）。既定 False で挙動不変。

    Returns:
        加算後のレベルカウント配列（array と同長）。
    """
    a = np.asarray(array, dtype=np.float64)
    n = a.size
    out = np.zeros(n, dtype=np.float64) if res is None else np.array(res, dtype=np.float64)
    if initialization:
        out[:] = 0.0

    if window is not None:
        # 因果ローリング窓: 各系列の σ 距離（z）を加算。warm-up は NaN 加算で合算も NaN。
        return out + _causal_z(a, window, freeze_last=freeze_last)

    avg = ps_average(a)
    up = _ps_band(a, sigma, _UPSIDE)
    down = _ps_band(a, sigma, _DOWNSIDE)

    for i in range(n):
        if a[i] > avg:
            out[i] = out[i] + ps_unit_conversion(a[i], avg, up, distant, _UPSIDE)
        elif a[i] < avg:
            out[i] = out[i] + ps_unit_conversion(a[i], avg, down, distant, _DOWNSIDE)
        else:
            out[i] = 0.0
    return out


def compute_sigma_levels(level_count: np.ndarray) -> Mapping[str, float]:
    """元 ``iBandsOnArray`` 相当の σ 水準線（上方 6 本・下方 6 本）を求める。

    レベルカウントの SMA（= 平均）と母標準偏差を基準に、各 σ（0.67〜3.29）で
    ``mean ± σ×std`` を計算する。元 StdDevArray[1..6]=上方, [7..12]=下方 に対応。
    因果版レベルカウントの warm-up（``NaN``）は基準算出から除外する（有限値のみ）。

    Args:
        level_count: レベルカウント系列（NaN を含みうる）。

    Returns:
        キー ``up_067``..``up_329`` / ``dn_067``..``dn_329`` → 水準値（float）。
    """
    x = np.asarray(level_count, dtype=np.float64)
    x = x[np.isfinite(x)]  # 因果版 warm-up の NaN を除外（全期間版は無影響）
    if x.size == 0:
        x = np.zeros(1, dtype=np.float64)
    mean = float(np.mean(x))
    std = float(np.sqrt(np.mean((x - mean) ** 2)))  # 母標準偏差（MT4 iBands と同じ）
    levels: dict[str, float] = {}
    for sigma in SIGMA_LEVELS:
        key = f"{int(round(sigma * 100)):03d}"
        levels[f"up_{key}"] = ps_normalize(mean + std * sigma)
        levels[f"dn_{key}"] = ps_normalize(mean - std * sigma)
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


# ===========================================================================
# 旧アンダースコア名（後方互換の別名。ISSUE-182 項目 1）
#
# public 昇格前の名前で参照している既存面（profit_adx_needle/src/core.py の
# 再エクスポート、profit_system / profit_adx_needle の既存テスト）を壊さないため、
# **同一の関数オブジェクト**を旧名でも束縛しておく。関数は 1 つしか無いので
# 値・丸め・例外は 1 ビットも変わらない。新規コードは public 名を使うこと。
# ===========================================================================
_normalize = ps_normalize
_ps_average = ps_average
_ps_std_ema = ps_std_ema
_unit_conversion = ps_unit_conversion
