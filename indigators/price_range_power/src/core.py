"""価格帯別ブルベアレシオ（PriceRangePower）のコア（純粋ロジック・numpy のみ）。

層名/責務:
    core 層。元 VBA ``TECNICAL_ANALYSIS.PriceRangePower`` の数値計算「概念」だけを
    純粋関数として保持する。シート I/O（``inputData`` / ``outputData``）・表示書式
    （``displayFormatSet``）・UserForm（``PriceRangePower.frm``）は偶有的性質であり
    本層には持ち込まない（依存は常に内向き）。

含む構造:
    * 既定パラメータ定数（interval / interval 候補）
    * WickStats          : 1 系統（HC/OL/HL/LH）の平均・標準偏差・±1σ/2σ/3σ 閾値（不変 DTO）
    * PrpResult          : バンド・度数・比率・合計・閾値を保持する不変 DTO
    * round_up           : Excel ROUNDUP(_,4) 相当（バンド刻みの忠実再現）
    * build_price_bands  : range_from..range_to を interval 刻みで価格帯（級）化
    * wick_samples       : OC 符号で陽線/陰線/同値を分類し 4 系統のヒゲ幅を抽出
    * wick_stats         : 系統ごとの平均・標本標準偏差・±σ 閾値
    * compute_price_range_power : 上記を束ね度数集計→比率→合計まで一括算出

元 VBA の対応（``sample/VBA/TECNICAL_ANALYSIS.cls`` 172-417 行）:
    * ``inOC = OPE.fun_OpeCHANGE(inTSD,"OC")``  → ``oc = close - open``（陽線=正）
    * Select Case inOC（>0/<0/=0）でヒゲ幅 inHC/inOL/inHL/inLH を構築
    * ``OPE.opeAverage`` は Empty を除外し ``Σ/cnt``（= 該当バーのみ平均）
    * ``OPE.opeSTDEV`` は Empty を除外し ``Sqr(Σ(x-avg)²/(cnt-1))``（= 標本標準偏差）
    * inHC 等は VBA で Variant 配列のため非該当バーは Empty＝統計から除外（NaN で再現）
    * 度数集計（A1 To A2 / A2 To A3 / Is>A3）と比率（分母・分子いずれか 0 で Empty）

依存:
    標準: dataclasses, math, typing / 外部: numpy / プロジェクト内: なし
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Mapping

import numpy as np

# 既定値（元 UserForm PriceRangePower / TA.PriceRangePower の Optional 既定に一致）。
DEFAULT_INTERVAL: float = 0.1                       # IsMissing(interval) -> iv = 0.1
INTERVAL_CHOICES: tuple[float, ...] = (0.1, 0.01, 0.001)  # ComboBox1 の選択肢
_ROUND_DECIMALS: int = 4                            # WorksheetFunction.RoundUp(_, 4)

# 系統名（HC=上ヒゲ / OL=下ヒゲ / HL=高→安 / LH=安→高）。
WICK_NAMES: tuple[str, ...] = ("hc", "ol", "hl", "lh")

# 成果物の度数 14 列（元 resPRP 列 1..14 の順）。
COUNT_COLUMNS: tuple[str, ...] = (
    "fda_f_l",                                  # 1  価格帯別度数分布 安値
    "f_ol_a1", "f_ol_a2", "f_ol_a3",            # 2-4  下ヒゲ ±1/2/3σ
    "f_lh_a1", "f_lh_a2", "f_lh_a3",            # 5-7  安値→高値 ±1/2/3σ
    "fda_f_h",                                  # 8  価格帯別度数分布 高値
    "f_hc_a1", "f_hc_a2", "f_hc_a3",            # 9-11  上ヒゲ ±1/2/3σ
    "f_hl_a1", "f_hl_a2", "f_hl_a3",            # 12-14 高値→安値 ±1/2/3σ
)

# 成果物の比率 12 列（元 resPRP 列 15..26 の順）。
RATIO_COLUMNS: tuple[str, ...] = (
    "f_ol_a1_pct", "f_ol_a2_pct", "f_ol_a3_pct",
    "f_lh_a1_pct", "f_lh_a2_pct", "f_lh_a3_pct",
    "f_hc_a1_pct", "f_hc_a2_pct", "f_hc_a3_pct",
    "f_hl_a1_pct", "f_hl_a2_pct", "f_hl_a3_pct",
)

TOTAL_COLUMN: str = "total"                     # 27  比率 12 列の合計


def round_up(x: float, decimals: int = _ROUND_DECIMALS) -> float:
    """Excel ``ROUNDUP(x, decimals)`` 相当（0 から遠ざかる方向へ切り上げ）。

    元 VBA は ``WorksheetFunction.RoundUp(prev + iv, 4)`` でバンド刻みを生成する。
    浮動小数の桁あふれ（例 1.1+0.1=1.2000000000000002）が誤った +1 桁を生むのを防ぐため、
    ``x*10^d`` を 6 桁で丸めてノイズを除いてから ``ceil`` する（ガイド §4.1: 実装都合の
    桁化は意図通りに安定化する）。

    Args:
        x: 対象値。
        decimals: 小数桁（既定 4）。

    Returns:
        4 桁へ切り上げた値。
    """
    factor = 10 ** decimals
    return math.ceil(round(x * factor, 6)) / factor


def build_price_bands(
    range_from: float, range_to: float, interval: float = DEFAULT_INTERVAL
) -> np.ndarray:
    """価格帯（級）の下端列を生成する。

    元 VBA（278-284 行）の忠実再現:
        resPRP(1,0) = range_from
        Do While 直前 <= range_to: 次 = RoundUp(直前 + interval, 4)
    直前バンドが ``range_to`` 以下である限り次を生成するため、**最後の 1 本は
    ``range_to`` を初めて超える値**になる（元の境界挙動をそのまま保つ）。

    Args:
        range_from: 開始価格（既定は安値の最小値）。
        range_to: 終了価格（既定は高値の最大値）。
        interval: 級の刻み幅（既定 0.1）。

    Returns:
        バンド下端の昇順 1 次元配列（float64）。

    Raises:
        ValueError: interval <= 0 の場合。
    """
    if interval <= 0:
        raise ValueError(f"interval は正値である必要があります: {interval}")
    bands = [float(range_from)]
    # 直前が range_to 以下の間だけ次を生成（元 Do While 条件）。
    while bands[-1] <= range_to:
        bands.append(round_up(bands[-1] + interval, _ROUND_DECIMALS))
    return np.asarray(bands, dtype=np.float64)


@dataclass(frozen=True)
class WickStats:
    """1 系統のヒゲ幅統計（該当バーのみ・標本標準偏差）。

    Attributes:
        name: 系統名（hc/ol/hl/lh）。
        avg: 算術平均（元 ``opeAverage``。Empty 除外）。
        std: 標本標準偏差（元 ``opeSTDEV``。``Sqr(Σ(x-avg)²/(n-1))``）。
        a1/a2/a3: avg+1σ / avg+2σ / avg+3σ の閾値。
    """

    name: str
    avg: float
    std: float
    a1: float
    a2: float
    a3: float


@dataclass(frozen=True)
class PrpResult:
    """PriceRangePower の計算成果（数値のみ・描画非依存）。

    Attributes:
        bands: 価格帯（級）の下端列（M,）。
        interval: 級の刻み幅。
        counts: 度数行列（M, 14）。列順は ``COUNT_COLUMNS``。
        ratios: 比率行列（M, 12）。列順は ``RATIO_COLUMNS``。分母/分子いずれか 0 は NaN。
        total: 比率 12 列の行合計（M,）。NaN は 0 とみなして加算。
        stats: 系統名→WickStats の対応（hc/ol/hl/lh）。
    """

    bands: np.ndarray
    interval: float
    counts: np.ndarray
    ratios: np.ndarray
    total: np.ndarray
    stats: Mapping[str, WickStats] = field(default_factory=dict)

    def __post_init__(self) -> None:
        for name in ("bands", "counts", "ratios", "total"):
            arr = np.asarray(getattr(self, name), dtype=np.float64)
            arr.setflags(write=False)  # DTO は不変（ガイド §2）
            object.__setattr__(self, name, arr)


def wick_samples(
    open_: np.ndarray, high: np.ndarray, low: np.ndarray, close: np.ndarray
) -> dict[str, np.ndarray]:
    """OC 符号でバーを分類し 4 系統のヒゲ幅を抽出する（非該当は NaN）。

    元 VBA（294-300 行）の Select Case を 1:1 で再現する。inHC/inOL/inHL/inLH は
    VBA で Variant 配列のため、代入されないバーは Empty（= 統計・度数集計から除外）。
    Python では NaN で表現する（ガイド §4.4: 非対称分類を忠実再現）。

        OC>0（陽線）: hc = high-close,  lh = high-low
        OC<0（陰線）: ol = open-low,   hl = high-low
        OC=0（同値）: hc = high-close,  ol = open-low

    Args:
        open_/high/low/close: 各バーの OHLC（昇順・同長）。

    Returns:
        {"hc","ol","hl","lh"} → 各系統のヒゲ幅配列（非該当 NaN, float64）。

    Raises:
        ValueError: 配列長が不一致、または空の場合。
    """
    arrays = [np.asarray(a, dtype=np.float64) for a in (open_, high, low, close)]
    sizes = {a.size for a in arrays}
    if len(sizes) != 1:
        raise ValueError(f"OHLC の長さが不一致です: {[a.size for a in arrays]}")
    o, h, low_a, c = arrays
    n = o.size
    if n == 0:
        raise ValueError("OHLC が空です。")

    oc = c - o  # = fun_OpeCHANGE(_, "OC") = Close - Open（陽線=正）
    bull = oc > 0
    bear = oc < 0
    doji = oc == 0

    hc = np.full(n, np.nan)
    ol = np.full(n, np.nan)
    hl = np.full(n, np.nan)
    lh = np.full(n, np.nan)

    hc[bull | doji] = (h - c)[bull | doji]
    lh[bull] = (h - low_a)[bull]
    ol[bear | doji] = (o - low_a)[bear | doji]
    hl[bear] = (h - low_a)[bear]
    return {"hc": hc, "ol": ol, "hl": hl, "lh": lh}


def wick_stats(name: str, samples: np.ndarray) -> WickStats:
    """系統のヒゲ幅から平均・標本標準偏差・±σ 閾値を求める。

    元 ``opeAverage`` / ``opeSTDEV`` は Empty を除外して集計する。Python では NaN を
    除外し、標準偏差は標本（``ddof=1``）で計算する。該当バーが 1 本以下のとき σ は
    定義不能のため NaN（閾値も NaN → 当該系統は度数分類されない）。

    Args:
        name: 系統名（hc/ol/hl/lh）。
        samples: 当該系統のヒゲ幅（非該当は NaN）。

    Returns:
        WickStats（avg/std/a1/a2/a3）。
    """
    x = np.asarray(samples, dtype=np.float64)
    valid = x[np.isfinite(x)]
    if valid.size == 0:
        avg = math.nan
    else:
        avg = float(valid.mean())
    if valid.size < 2:
        std = math.nan  # Sqr(_/(cnt-1)) は cnt<2 で未定義
    else:
        std = float(valid.std(ddof=1))
    a1, a2, a3 = avg + std, avg + std * 2, avg + std * 3
    return WickStats(name=name, avg=avg, std=std, a1=a1, a2=a2, a3=a3)


def _sigma_bins(samples: np.ndarray, st: WickStats) -> np.ndarray:
    """各ヒゲ幅を ±σ ビン（1/2/3、非該当 0）へ分類する。

    元 VBA Select Case の評価順（先勝ち）を再現:
        Case A1 To A2 → 1（[a1, a2]）
        Case A2 To A3 → 2（(a2, a3]）  ※ x==a2 は上で 1 に確定
        Case Is > A3  → 3
        それ以外      → 0（NaN や a1 未満も含む）
    """
    x = np.asarray(samples, dtype=np.float64)
    bins = np.zeros(x.size, dtype=np.int8)
    if not (np.isfinite(st.a1) and np.isfinite(st.a2) and np.isfinite(st.a3)):
        return bins
    finite = np.isfinite(x)
    b1 = finite & (x >= st.a1) & (x <= st.a2)
    b2 = finite & ~b1 & (x >= st.a2) & (x <= st.a3)
    b3 = finite & ~b1 & ~b2 & (x > st.a3)
    bins[b1] = 1
    bins[b2] = 2
    bins[b3] = 3
    return bins


def compute_price_range_power(
    open_: np.ndarray,
    high: np.ndarray,
    low: np.ndarray,
    close: np.ndarray,
    *,
    interval: float = DEFAULT_INTERVAL,
    range_from: float | None = None,
    range_to: float | None = None,
) -> PrpResult:
    """価格帯別ブルベアレシオ（度数・比率・合計）を一括算出する。

    元 ``TA.PriceRangePower(timeSeriesData, interval, rangeFrom, rangeTo)`` の忠実移植。

    Args:
        open_/high/low/close: OHLC（昇順・同長）。
        interval: 級の刻み幅（既定 0.1。元 Optional interval）。
        range_from: 開始価格（既定 None → 安値の最小値。元 ``opeMIN(inL)``）。
        range_to: 終了価格（既定 None → 高値の最大値。元 ``opeMAX(inH)``）。

    Returns:
        PrpResult（bands / counts(M,14) / ratios(M,12) / total(M,) / stats）。

    Raises:
        ValueError: OHLC 長不一致・空、interval<=0 の場合。
    """
    samples = wick_samples(open_, high, low, close)
    high_a = np.asarray(high, dtype=np.float64)
    low_a = np.asarray(low, dtype=np.float64)

    # range 既定（元 opeMIN(inL) / opeMAX(inH)。NaN は除外）。
    if range_from is None:
        range_from = float(np.nanmin(low_a))
    if range_to is None:
        range_to = float(np.nanmax(high_a))

    bands = build_price_bands(range_from, range_to, interval)
    upper = bands + interval  # 元 resPRP(i,0)+iv（次バンドではなく下端+刻み）
    m = bands.size

    # 帯メンバシップ（M, N）: 下端 <= 値 < 下端+刻み。
    lo = bands[:, None]
    hi = upper[:, None]
    low_in = (lo <= low_a[None, :]) & (hi > low_a[None, :])
    high_in = (lo <= high_a[None, :]) & (hi > high_a[None, :])
    low_in_f = low_in.astype(np.float64)
    high_in_f = high_in.astype(np.float64)

    stats = {name: wick_stats(name, samples[name]) for name in WICK_NAMES}

    # 各バーの ±σ ビン → ビン別マスク（N,）→ 帯メンバシップとの行列積で度数化。
    def bin_masks(name: str) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        b = _sigma_bins(samples[name], stats[name])
        return (b == 1).astype(np.float64), (b == 2).astype(np.float64), (b == 3).astype(np.float64)

    hc1, hc2, hc3 = bin_masks("hc")
    ol1, ol2, ol3 = bin_masks("ol")
    hl1, hl2, hl3 = bin_masks("hl")
    lh1, lh2, lh3 = bin_masks("lh")

    counts = np.zeros((m, len(COUNT_COLUMNS)), dtype=np.float64)
    counts[:, 0] = low_in_f.sum(axis=1)        # fda_f_l
    counts[:, 1] = low_in_f @ ol1              # f_ol_a1
    counts[:, 2] = low_in_f @ ol2              # f_ol_a2
    counts[:, 3] = low_in_f @ ol3              # f_ol_a3
    counts[:, 4] = low_in_f @ lh1              # f_lh_a1
    counts[:, 5] = low_in_f @ lh2              # f_lh_a2
    counts[:, 6] = low_in_f @ lh3              # f_lh_a3
    counts[:, 7] = high_in_f.sum(axis=1)       # fda_f_h
    counts[:, 8] = high_in_f @ hc1             # f_hc_a1
    counts[:, 9] = high_in_f @ hc2             # f_hc_a2
    counts[:, 10] = high_in_f @ hc3            # f_hc_a3
    counts[:, 11] = high_in_f @ hl1            # f_hl_a1
    counts[:, 12] = high_in_f @ hl2            # f_hl_a2
    counts[:, 13] = high_in_f @ hl3            # f_hl_a3

    # 比率（分母 = fda_f_l または fda_f_h）。分母/分子いずれか 0 は NaN（元 Empty）。
    fda_l = counts[:, 0]
    fda_h = counts[:, 7]
    num_cols = [1, 2, 3, 4, 5, 6, 8, 9, 10, 11, 12, 13]   # 比率の分子（COUNT_COLUMNS index）
    den_cols = [0, 0, 0, 0, 0, 0, 7, 7, 7, 7, 7, 7]       # 比率の分母（fda_l/fda_h）
    ratios = np.full((m, len(RATIO_COLUMNS)), np.nan)
    for k, (nc, dc) in enumerate(zip(num_cols, den_cols)):
        num = counts[:, nc]
        den = counts[:, dc]
        valid = (den > 0) & (num > 0)
        ratios[valid, k] = num[valid] / den[valid]

    total = np.nansum(ratios, axis=1)  # 元 col27: Empty を 0 とみなした合計

    return PrpResult(
        bands=bands,
        interval=float(interval),
        counts=counts,
        ratios=ratios,
        total=total,
        stats=stats,
    )
