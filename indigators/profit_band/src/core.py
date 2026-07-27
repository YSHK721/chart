"""PRO!fit_Band のコア統計計算（純粋ロジック・外部I/O非依存）。

MQL5 版インジケーター ``PRO!fit_Band.mq5`` の計算部を Python へ再設計したもの。
各ローソク足を陽線 / 陰線 / 同値に分類し、始値からの値幅（始値-高値・始値-安値・
高値-安値）の絶対値を分類ごとに集計、複数パーセンタイルの分位点を求め、
始値を基準とした統計バンドを生成する。

元 MQL5 では値幅算出時に ``int()`` で小数を切り捨てていたが、FX 等の小数価格で
結果が 0 になる不具合となるため、本実装では切り捨てを廃止し float 精度で計算する。
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

# 元コード probabilityPercent[7] と同一順序。インデックスはバンド名の百分率に対応する。
PROBABILITIES: tuple[float, ...] = (0.51, 0.80, 0.85, 0.90, 0.95, 0.98, 0.99)

# 集計バケットの識別子。
# p=Positive(陽線) / n=Negative(陰線)、OH=Open-High / OL=Open-Low / HL=High-Low。
BUCKETS: tuple[str, ...] = ("pOH", "pOL", "pHL", "nOH", "nOL", "nHL")


@dataclass(frozen=True)
class DistanceSamples:
    """分類別に集計した値幅サンプル（分位点計算の入力）。

    各配列は対象ローソク足から収集した値幅の絶対値（並び順は出現順）。
    全配列は ``__post_init__`` で float64 化のうえ ``writeable=False`` に固定する
    （ガイド §2「DTO は不変」）。
    """

    pOH: np.ndarray
    pOL: np.ndarray
    pHL: np.ndarray
    nOH: np.ndarray
    nOL: np.ndarray
    nHL: np.ndarray

    def __post_init__(self) -> None:
        for name in BUCKETS:
            arr = np.asarray(getattr(self, name), dtype=np.float64)
            arr.setflags(write=False)  # DTO は不変（ガイド §2）
            object.__setattr__(self, name, arr)

    def as_dict(self) -> dict[str, np.ndarray]:
        return {b: getattr(self, b) for b in BUCKETS}


def collect_distance_samples(
    open_: np.ndarray,
    high: np.ndarray,
    low: np.ndarray,
    close: np.ndarray,
) -> DistanceSamples:
    """OHLC 配列から分類別の値幅サンプルを収集する。

    分類規則（元 MQL5 と同一）:
      * 陽線 (open < close): 値幅を pOH / pOL / pHL に加える。
      * 陰線 (open > close): 値幅を nOH / nOL / nHL に加える。
      * 同値 (open == close): 値幅を pOH / nOL / pHL / nHL に加える（非対称）。

    Args:
        open_, high, low, close: 同一長の 1 次元価格配列。

    Returns:
        DistanceSamples: 分類別の値幅サンプル。

    Raises:
        ValueError: 配列長が一致しない、または空の場合。
    """
    o = np.asarray(open_, dtype=np.float64)
    h = np.asarray(high, dtype=np.float64)
    l = np.asarray(low, dtype=np.float64)
    c = np.asarray(close, dtype=np.float64)

    lengths = {o.size, h.size, l.size, c.size}
    if len(lengths) != 1:
        raise ValueError(f"OHLC 配列の長さが不一致です: {[o.size, h.size, l.size, c.size]}")
    if o.size == 0:
        raise ValueError("OHLC 配列が空です。")

    oh = np.abs(o - h)
    ol = np.abs(o - l)
    hl = np.abs(h - l)

    bull = o < c
    bear = o > c
    even = o == c

    return DistanceSamples(
        pOH=np.concatenate([oh[bull], oh[even]]),
        pOL=ol[bull],
        pHL=np.concatenate([hl[bull], hl[even]]),
        nOH=oh[bear],
        nOL=np.concatenate([ol[bear], ol[even]]),
        nHL=np.concatenate([hl[bear], hl[even]]),
    )


def compute_quantiles(
    samples: DistanceSamples,
    probabilities: tuple[float, ...] = PROBABILITIES,
) -> dict[str, np.ndarray]:
    """各バケットのサンプルから指定確率の分位点を算出する。

    MQL5 標準ライブラリ ``MathQuantile`` と同一の線形補間方式（R type-7 /
    numpy 既定 ``linear``）を用いるため numpy.quantile をそのまま使用する。

    Args:
        samples: 分類別の値幅サンプル。
        probabilities: 算出する確率の並び（既定は PROBABILITIES）。

    Returns:
        バケット名 -> probabilities と同順の分位点配列。

    Raises:
        ValueError: いずれかのバケットがバンド生成に必要なのに空の場合。
    """
    probs = np.asarray(probabilities, dtype=np.float64)
    result: dict[str, np.ndarray] = {}
    for name, values in samples.as_dict().items():
        if values.size == 0:
            # pHL / nHL は描画に未使用だが、pOH/pOL/nOH/nOL が空だとバンド不能。
            result[name] = np.full(probs.size, np.nan)
            continue
        result[name] = np.quantile(values, probs, method="linear")
    return result
