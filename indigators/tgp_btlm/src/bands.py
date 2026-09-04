"""BTLM 成果物層: 予測結果 → 成果物 DataFrame。

層名/責務:
    成果物層。直近 maxbars 本の価格系列に対し ``BtlmFitter`` で予測平均・上下分位点を
    求め、入力 index を引き継いだ 3 列 DataFrame に整形する。窓の外は NaN（EMPTY_VALUE 相当）。

含む構造:
    build_btlm_bands : OHLC DataFrame + Fitter → 成果物 DataFrame。

元 MQL4 の対応:
    ``RGetVector(model$Zp.mean|q1|q2)`` を ``buf_mean/buf_q1/buf_q2`` に書き込む処理
    （窓の手前は EMPTY_VALUE）。ガイド付録 profit_band の ``build_bands`` に相当。

依存:
    標準: __future__ / 外部: numpy, pandas / プロジェクト内: core
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from .core import (
    DEFAULT_MAXBARS,
    DEFAULT_Q_HIGH,
    DEFAULT_Q_LOW,
    BtlmFitter,
    make_design,
    mean_column,
    quantile_column,
)


def build_btlm_bands(
    df: pd.DataFrame,
    fitter: BtlmFitter,
    *,
    price: str = "open",
    maxbars: int = DEFAULT_MAXBARS,
    q_low: float = DEFAULT_Q_LOW,
    q_high: float = DEFAULT_Q_HIGH,
) -> pd.DataFrame:
    """価格系列から btlm 予測バンドの DataFrame を生成する。

    Args:
        df: ``open`` 等の価格列を持つ DataFrame（列名の大小不問）。元 index を引き継ぐ。
        fitter: モデル当てはめのポート実装（R tgp / numpy 参照 / Fake）。
        price: 回帰対象の価格列名（既定 ``open``。元 MQL4 は ``Open[]`` を使用）。
        maxbars: 当てはめに用いる直近本数（既定 100。元 ``extern int maxbars``）。
        q_low: 下側予測分位点（0..1、既定 0.05）。
        q_high: 上側予測分位点（0..1、既定 0.95）。

    Returns:
        ``btlm_mean`` / ``btlm_q{lo}`` / ``btlm_q{hi}`` 列を持つ DataFrame。
        当てはめ窓（直近 min(maxbars, 行数) 本）以外の行は NaN。

    Raises:
        KeyError: 指定の価格列が存在しない場合。
        ValueError: q_low >= q_high、または 0..1 範囲外、df が空の場合。
    """
    if not (0.0 < q_low < q_high < 1.0):
        raise ValueError(f"分位点は 0 < q_low < q_high < 1 が必要です: q_low={q_low}, q_high={q_high}")
    n = len(df)
    if n == 0:
        raise ValueError("入力 DataFrame が空です。")

    cols = {c.lower(): c for c in df.columns}
    if price.lower() not in cols:
        raise KeyError(f"価格列が存在しません: {price}（存在する列: {list(df.columns)}）")

    series = df[cols[price.lower()]].to_numpy(dtype=np.float64)
    window = min(maxbars, n)
    x, z = make_design(series[-window:])

    result = fitter.fit_predict(x, z, q_low=q_low, q_high=q_high)
    if result.mean.size != window:
        raise ValueError(
            f"Fitter の返り値長 {result.mean.size} が当てはめ窓 {window} と一致しません。"
        )

    mean = np.full(n, np.nan)
    lower = np.full(n, np.nan)
    upper = np.full(n, np.nan)
    mean[-window:] = result.mean
    lower[-window:] = result.q_low
    upper[-window:] = result.q_high

    return pd.DataFrame(
        {
            mean_column(): mean,
            quantile_column(q_low): lower,
            quantile_column(q_high): upper,
        },
        index=df.index,
    )
