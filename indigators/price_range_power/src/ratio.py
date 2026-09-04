"""成果物層: 計算結果 → 価格帯別ブルベアレシオの成果物 DataFrame。

層名/責務:
    成果物層。OHLC DataFrame から ``compute_price_range_power`` を呼び、元 VBA の
    resPRP（価格帯 × 27 列）に対応する機械可読 DataFrame に整形する。バンド下端を
    index（``prp``）とし、列は ``{系統}_{パラメータ}`` 形式（ガイド §5）。

含む構造:
    * build_price_range_power : OHLC DataFrame → 成果物 DataFrame（度数14 + 比率12 + 合計）
    * build_bull_bear_profile : 帯別の「ブル（支持）/ベア（抵抗）勢力」要約 DataFrame

元 VBA の対応:
    ``pD.iDataWrite(res)`` がシートへ書き出す resPRP（級・FDA・F.*.A* %・TOTAL）。
    比率の Empty（分母/分子 0）は NaN として保持する。

依存:
    標準: __future__ / 外部: numpy, pandas / プロジェクト内: core
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from .core import (
    COUNT_COLUMNS,
    DEFAULT_INTERVAL,
    RATIO_COLUMNS,
    TOTAL_COLUMN,
    PrpResult,
    compute_price_range_power,
)

# index 名（元 resPRP 列 0 "PRP"＝価格帯の級）。
PRP_INDEX_NAME = "prp"

# ブル（安値＝支持帯）勢力に寄与する比率列と、ベア（高値＝抵抗帯）勢力に寄与する比率列。
_BULL_RATIO_COLUMNS = (
    "f_ol_a1_pct", "f_ol_a2_pct", "f_ol_a3_pct",
    "f_lh_a1_pct", "f_lh_a2_pct", "f_lh_a3_pct",
)
_BEAR_RATIO_COLUMNS = (
    "f_hc_a1_pct", "f_hc_a2_pct", "f_hc_a3_pct",
    "f_hl_a1_pct", "f_hl_a2_pct", "f_hl_a3_pct",
)


def result_to_frame(result: PrpResult) -> pd.DataFrame:
    """PrpResult を成果物 DataFrame（バンド index・度数/比率/合計列）に整形する。

    Args:
        result: compute_price_range_power の戻り値。

    Returns:
        index=バンド下端（``prp``）、列= COUNT_COLUMNS + RATIO_COLUMNS + ``total``。
    """
    data = {}
    for j, name in enumerate(COUNT_COLUMNS):
        data[name] = result.counts[:, j]
    for j, name in enumerate(RATIO_COLUMNS):
        data[name] = result.ratios[:, j]
    data[TOTAL_COLUMN] = result.total
    index = pd.Index(result.bands, name=PRP_INDEX_NAME)
    return pd.DataFrame(data, index=index)


def build_price_range_power(
    df: pd.DataFrame,
    *,
    interval: float = DEFAULT_INTERVAL,
    range_from: float | None = None,
    range_to: float | None = None,
) -> pd.DataFrame:
    """OHLC DataFrame から価格帯別ブルベアレシオの成果物 DataFrame を生成する。

    Args:
        df: ``open``/``high``/``low``/``close`` 列を持つ DataFrame（列名の大小不問・昇順）。
        interval: 級の刻み幅（既定 0.1）。
        range_from: 開始価格（既定 None → 安値の最小値）。
        range_to: 終了価格（既定 None → 高値の最大値）。

    Returns:
        index=バンド下端（``prp``）の成果物 DataFrame（度数14 + 比率12 + 合計）。

    Raises:
        KeyError: 必須の OHLC 列が存在しない場合。
        ValueError: 行が空、interval<=0 の場合。
    """
    cols = {c.lower(): c for c in df.columns}
    missing = [k for k in ("open", "high", "low", "close") if k not in cols]
    if missing:
        raise KeyError(f"OHLC 列が不足しています: {missing}（存在する列: {list(df.columns)}）")

    o = df[cols["open"]].to_numpy(dtype=np.float64)
    h = df[cols["high"]].to_numpy(dtype=np.float64)
    low = df[cols["low"]].to_numpy(dtype=np.float64)
    c = df[cols["close"]].to_numpy(dtype=np.float64)

    result = compute_price_range_power(
        o, h, low, c, interval=interval, range_from=range_from, range_to=range_to
    )
    return result_to_frame(result)


def build_bull_bear_profile(
    df: pd.DataFrame,
    *,
    interval: float = DEFAULT_INTERVAL,
    range_from: float | None = None,
    range_to: float | None = None,
) -> pd.DataFrame:
    """帯別の「ブル（支持）/ベア（抵抗）勢力」要約を返す（描画・分析用）。

    元 resPRP の比率 12 列を、安値側（ブル: OL/LH%）と高値側（ベア: HC/HL%）に束ね、
    各帯の度数（fda_f_l / fda_f_h）と勢力合計を 1 表にまとめる。比率の NaN は 0 として
    合計する（元 TOTAL と同じ扱い）。

    Args:
        df: OHLC DataFrame。
        interval/range_from/range_to: build_price_range_power と同じ意味。

    Returns:
        index=バンド下端（``prp``）、列= ``freq_low``/``freq_high``/``bull_power``/
        ``bear_power``/``net_power``（= bull-bear）/``total``。
    """
    res = build_price_range_power(
        df, interval=interval, range_from=range_from, range_to=range_to
    )
    bull = np.nansum(res[list(_BULL_RATIO_COLUMNS)].to_numpy(), axis=1)
    bear = np.nansum(res[list(_BEAR_RATIO_COLUMNS)].to_numpy(), axis=1)
    return pd.DataFrame(
        {
            "freq_low": res["fda_f_l"].to_numpy(),
            "freq_high": res["fda_f_h"].to_numpy(),
            "bull_power": bull,
            "bear_power": bear,
            "net_power": bull - bear,
            TOTAL_COLUMN: res[TOTAL_COLUMN].to_numpy(),
        },
        index=res.index,
    )
