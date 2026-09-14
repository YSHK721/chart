"""層名: core 層（純粋計算）。

責務:
    PRO!fit_HLBand（メインチャート overlay の上下 8 バンド価格線を出す距離指標・
    アンダースコア版）の計算を numpy 配列のみで行う純粋関数層。入出力・描画・
    pandas を含まない。High-Close / Low-Close の絶対距離系列を起点とし、全系列の
    平均 + dev·母σ（÷N）を起点終値 close[-2] に加減算して 8 バンドを得る。

含む構造:
    HL_BAND_DEVS     : 固定偏差 (0.67, 1.65, 1.96, 2.58)。
    DEFAULT_WINDOW   : 因果窓の既定本数 120（None で全長＝後方互換）。
    MIN_EFFECTIVE_BARS: 帯算出に要する最小有効本数 2（未満で available=False）。
    compute_distances: dist_high=|H-C|, dist_low=|L-C|（全 i・warm-up/NaN なし）。
    compute_ratios   : r_high=|H-C|/C, r_low=|L-C|/C（per-bar・各バー close で除算）。
    band_upper       : mean(series) + dev*母σ(series)（÷N・末尾 W 本）。
    compute_hl_band  : close_ref=close[-2] へ 8 バンドを投影した frozen DTO。
    HlBandResult     : dist_high/dist_low/close_ref/levels/available の不変 DTO。

因果窓 W + 比率正規化（忠実移植は放棄。後方互換は window=None/normalize=False）:
    比率の分母と投影の基準は別物である。比率正規化（normalize=True）の分母は
    「各バー自身の close[i]」（per-bar 正規化＝価格水準依存の是正）。一方、帯を価格軸へ
    戻す投影基準 close_ref は系列 1 点の「close[-2]」（元 iClose(...,1)）であり、両者は
    異なる close を指す。window は帯幅統計に用いる末尾 W 本のみを切る（履歴長非依存・
    look-ahead 是正）が、close_ref は window に依らず close[-2] を維持する。

元 MQL 対応（``PRO!fit_HLBand.mq4`` を昇順=古→新へ 1:1 変換）:
    L205 ResBufferDivisionOpenHigh[i]=MathAbs(iHigh(i)-iClose(i)) → compute_distances dist_high
    L206 ResBufferDivisionOpenLow[i] =MathAbs(iLow(i)-iClose(i))  → compute_distances dist_low
    L220-223 StdDevArray[1..4]=iClose(1)+iBandsOnArray(OpenHigh,dev,0,1,0)
        → up_k = close_ref + band_upper(dist_high, dev_k)（加算・mode1=upper）。
    L224-227 StdDevArray[5..8]=iClose(1)-iBandsOnArray(OpenLow,dev,0,1,0)
        → dn_k = close_ref - band_upper(dist_low, dev_k)（減算）。
    iClose(inpSymbol,inpTimeFrame,1) = 系列 index 1 = 昇順 close[-2]  → close_ref。
    iBandsOnArray(...,dev,0,1,0) の中心=全系列平均・偏差=母σ（÷N）          → band_upper。
    input は inpSymbol/inpTimeFrame のみ（計算 period 無し）。

依存:
    標準: __future__, dataclasses / 外部: numpy のみ
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

# iBandsOnArray の deviation 引数（L220-227）。元 define SIGMA_L1/L3/L4/L5。
HL_BAND_DEVS: tuple[float, float, float, float] = (0.67, 1.65, 1.96, 2.58)

# 因果窓の既定本数（直近 W 本）。None で全長（後方互換）。
DEFAULT_WINDOW: int | None = 120

# 帯幅統計に必要な最小有効本数。母σ算出には >=2 本が要る（1 本では σ=0・帯潰れ）。
MIN_EFFECTIVE_BARS: int = 2

# levels 辞書のキー接尾辞（dev → キー）。
_DEV_SUFFIX: dict[float, str] = {0.67: "067", 1.65: "165", 1.96: "196", 2.58: "258"}


def compute_distances(
    high: np.ndarray, low: np.ndarray, close: np.ndarray
) -> tuple[np.ndarray, np.ndarray]:
    """``dist_high=|high-close|``, ``dist_low=|low-close|`` を全 i で返す。

    元 ``MathAbs(iHigh(i)-iClose(i))`` / ``MathAbs(iLow(i)-iClose(i))``（L205-206）。

    Args:
        high: 高値配列（昇順, 古→新）。
        low: 安値配列（昇順, 同長）。
        close: 終値配列（昇順, 同長）。

    Returns:
        ``(dist_high, dist_low)`` のタプル（入力と同長, float64）。

    Raises:
        ValueError: high/low/close の長さが一致しない。
    """
    high = np.asarray(high, dtype=np.float64)
    low = np.asarray(low, dtype=np.float64)
    close = np.asarray(close, dtype=np.float64)
    if not (high.shape == low.shape == close.shape):
        raise ValueError(
            f"high/low/close の長さが一致しません: "
            f"{high.shape}/{low.shape}/{close.shape}"
        )
    return np.abs(high - close), np.abs(low - close)


def compute_ratios(
    high: np.ndarray, low: np.ndarray, close: np.ndarray
) -> tuple[np.ndarray, np.ndarray]:
    """``r_high=|high-close|/close``, ``r_low=|low-close|/close`` を per-bar で返す。

    比率正規化（価格水準依存の是正）。各バーをそのバーの close で正規化するため、
    価格水準が異なる期間でも帯幅が相対量として比較可能になる。

    Args:
        high: 高値配列（昇順, 古→新）。
        low: 安値配列（昇順, 同長）。
        close: 終値配列（昇順, 同長）。全要素 > 0 が必要（0 除算ガード）。

    Returns:
        ``(r_high, r_low)`` のタプル（入力と同長, float64）。

    Raises:
        ValueError: high/low/close の長さが一致しない、または close に 0 以下を含む。
    """
    high = np.asarray(high, dtype=np.float64)
    low = np.asarray(low, dtype=np.float64)
    close = np.asarray(close, dtype=np.float64)
    if not (high.shape == low.shape == close.shape):
        raise ValueError(
            f"high/low/close の長さが一致しません: "
            f"{high.shape}/{low.shape}/{close.shape}"
        )
    if np.any(close <= 0.0):
        raise ValueError("close に 0 以下の値が含まれます（比率正規化の 0 除算ガード）。")
    return np.abs(high - close) / close, np.abs(low - close) / close


def _tail(series: np.ndarray, window: int | None) -> np.ndarray:
    """帯幅統計に用いる末尾 W 本 ``series[-window:]`` を返す（window=None で全長）。

    比率／絶対いずれの series（per-bar 系列）に対しても同一規則で末尾を切る。
    close_ref（投影基準）はこのスライスに依らず別途 ``close[-2]`` を維持する点に注意。
    """
    if window is None:
        return series
    return series[-window:]


def band_upper(dist: np.ndarray, dev: float) -> float:
    """``mean(dist) + dev * 母σ(dist)`` を返す（全系列・母σ÷N）。

    元 ``iBandsOnArray(dist, 0, length, dev, 0, 1, 0)``（mode1=upper）に対応する。
    中心は全系列平均、偏差は母標準偏差（÷N）。

    Args:
        dist: 距離系列（全系列）。
        dev: 偏差係数。

    Returns:
        ``mean + dev*sigma`` のスカラ。
    """
    x = np.asarray(dist, dtype=np.float64)
    mean = float(np.mean(x))
    sigma = float(np.sqrt(np.mean((x - mean) ** 2)))  # 母σ（÷N, MT4 iBands 準拠）
    return mean + dev * sigma


@dataclass(frozen=True)
class HlBandResult:
    """PRO!fit_HLBand の計算成果（数値のみ・描画非依存の不変 DTO）。

    Attributes:
        dist_high: High 側の帯幅統計に用いた per-bar 系列（N,。writeable=False）。
            normalize=True では**比率** ``|H-C|/C``、normalize=False では**絶対距離**
            ``|H-C|`` を保持する（compute_hl_band の normalize に追従）。フィールド名は
            下流影響回避のため ``dist_*`` のまま据え置く（compute_distances の独立呼出に
            無影響）。
        dist_low: Low 側の per-bar 系列（N,。writeable=False）。normalize=True では比率
            ``|L-C|/C``、normalize=False では絶対距離 ``|L-C|`` を保持する。
        close_ref: 起点終値（= close[-2]。元 iClose(...,1)）。
        levels: 8 バンド辞書 ``{"up_067","up_165","up_196","up_258",
            "dn_067","dn_165","dn_196","dn_258"}``。有効本数 < 2 のとき全 NaN。
        available: 有効本数 >= 2 で帯が算出可能なら True、不足なら False（levels は NaN）。
    """

    dist_high: np.ndarray
    dist_low: np.ndarray
    close_ref: float
    levels: dict[str, float]
    available: bool = True

    def __post_init__(self) -> None:
        for name in ("dist_high", "dist_low"):
            arr = np.asarray(getattr(self, name), dtype=np.float64)
            arr.setflags(write=False)  # DTO は不変（profit_hlband 準拠）
            object.__setattr__(self, name, arr)


def compute_hl_band(
    high: np.ndarray,
    low: np.ndarray,
    close: np.ndarray,
    *,
    window: int | None = DEFAULT_WINDOW,
    normalize: bool = True,
) -> HlBandResult:
    """因果窓 W + 比率正規化を反映した 8 バンドを HlBandResult として返す。

    normalize=True（既定）は per-bar 比率 ``|X-C|/C`` を起点に帯幅を相対量として
    算出し、close_ref へ乗算投影する（``up_k=close_ref*(1+off_high_k)``、
    ``dn_k=close_ref*(1-off_low_k)``）。価格水準依存を是正し、スケール不変な相対帯を得る。
    normalize=False は従来の絶対距離 ``|X-C|`` を close_ref へ加減算投影する
    （``up_k=close_ref+off_high_k``、``dn_k=close_ref-off_low_k``。旧挙動と bit 一致）。

    window が int なら帯幅統計は系列末尾 W 本 ``series[-window:]`` のみを用いる
    （履歴長非依存・look-ahead 是正）。None なら全長（後方互換）。close_ref は窓に
    依らず ``close[-2]`` を維持する。

    Args:
        high: 高値配列（昇順）。
        low: 安値配列（昇順・同長）。
        close: 終値配列（昇順・同長）。
        window: 因果窓本数（直近 W 本）。None で全長。
        normalize: True で比率正規化、False で絶対距離（後方互換）。

    Returns:
        HlBandResult（dist_high/dist_low/close_ref/levels/available）。有効本数は実際に
        統計へ用いた末尾スライス長 ``len(series[-window:])``（window=None で n）であり、
        これを単一の真実源として MIN_EFFECTIVE_BARS 未満なら available=False・levels 全 NaN。

    Raises:
        ValueError: high/low/close の長さが一致しない、N<2（close[-2] 不在）、
            window が int かつ < 1（直近 W 本の窓として無意味）、または normalize=True
            かつ close に 0 以下を含む（0 除算ガード）。
    """
    close = np.asarray(close, dtype=np.float64)
    n = close.shape[0]
    if n < MIN_EFFECTIVE_BARS:
        raise ValueError(f"N>={MIN_EFFECTIVE_BARS} が必要です（close[-2] 起点）。len(close)={n}")

    # window<1（0・負）は「直近 W 本」の窓として無意味であり、_tail のスライス長と
    # min(window, n) が乖離して available の真実源を壊す（_tail(s,0)=s[-0:]=全長、
    # _tail(s,-3)=s[3:]）。全長フォールバックは呼出側のバグを暗黙救済するため採らず、
    # 入口で ValueError に正規化して乖離経路自体を消す（単一の真実源化の前提条件）。
    if window is not None and window < 1:
        raise ValueError(f"window>=1 または None が必要です（直近 W 本の窓長）。window={window}")

    # series（帯幅統計の per-bar 量）: normalize=True は比率 |X-C|/C、False は絶対距離 |X-C|。
    if normalize:
        series_high, series_low = compute_ratios(high, low, close)
    else:
        series_high, series_low = compute_distances(high, low, close)

    close_ref = float(close[-2])  # 昇順 index 1 from end = iClose(...,1)・窓に依らず維持

    # 帯幅統計に用いる末尾 W 本（close_ref はこのスライスに依らない）。
    slice_high = _tail(series_high, window)
    slice_low = _tail(series_low, window)

    # 有効本数 = 実際に統計へ用いるスライス長（単一の真実源）。入口で window>=1 を保証
    # 済みのため len(slice_high) == len(slice_low) == min(window, n)（window=None で n）。
    effective = len(slice_high)
    available = effective >= MIN_EFFECTIVE_BARS

    # close_ref への投影規則を normalize で 1 度だけ選択する（per-dev で再分岐しない）。
    # normalize=True : 相対オフセットを乗算 close_ref*(1±off)（スケール不変な相対帯）。
    # normalize=False: 絶対オフセットを加減算 close_ref±off（後方互換）。
    # up は加算側（+/1+off）、dn は減算側（-/1-off）で対称。
    def project(offset: float, *, additive: bool) -> float:
        if normalize:
            return close_ref * (1.0 + offset) if additive else close_ref * (1.0 - offset)
        return close_ref + offset if additive else close_ref - offset

    levels: dict[str, float] = {}
    for dev in HL_BAND_DEVS:
        suffix = _DEV_SUFFIX[dev]
        if not available:
            levels[f"up_{suffix}"] = float("nan")
            levels[f"dn_{suffix}"] = float("nan")
            continue
        levels[f"up_{suffix}"] = project(band_upper(slice_high, dev), additive=True)
        levels[f"dn_{suffix}"] = project(band_upper(slice_low, dev), additive=False)

    return HlBandResult(
        dist_high=series_high,
        dist_low=series_low,
        close_ref=close_ref,
        levels=levels,
        available=available,
    )
