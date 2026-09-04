"""成果物層: 計算結果 → ADX_NEEDLE の成果物 DataFrame。

層名/責務:
    成果物層。OHLC DataFrame から ``compute_adx_needle`` を呼び、元 MQL4 の
    ``ExtBufferLevelCount``（別ウィンドウのヒストグラム 1 本）に対応する機械可読
    DataFrame に整形する。入力 index を引き継ぎ、列は ``{系統}_{パラメータ}`` 形式
    （ガイド §5）。σ 水準線は時系列ではなくスカラ参照値のため別関数で返す。

含む構造:
    * build_adx_needle  : OHLC DataFrame → 成果物 DataFrame（needle/level_count/adx）
    * needle_levels     : σ 水準線（up_*/dn_*）とクランプ境界の辞書

元 MQL4 の対応:
    ``SetIndexBuffer(0, ExtBufferLevelCount)`` + ``DRAW_HISTOGRAM`` への書き込みと、
    ``PS_IndicatorLevelValueSet`` による水準線設定。

依存:
    標準: __future__ / 外部: numpy, pandas / プロジェクト内: core
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from .core import DEFAULT_PERIOD, DEFAULT_WINDOW, compute_adx_needle

# 成果物の列名（ガイド §5: 機械可読）。
NEEDLE_COLUMN: str = "adx_needle"        # クランプ済みヒストグラム（描画対象）
LEVEL_COLUMN: str = "adx_level_count"    # クランプ前レベルカウント
ADX_COLUMN: str = "adx"                  # 単一 ADX 本線（参照用）


def _extract_hlc(df: pd.DataFrame) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """DataFrame から high/low/close を取り出す（列名の大小不問）。"""
    cols = {c.lower(): c for c in df.columns}
    missing = [k for k in ("high", "low", "close") if k not in cols]
    if missing:
        raise KeyError(f"HLC 列が不足しています: {missing}（存在する列: {list(df.columns)}）")
    h = df[cols["high"]].to_numpy(dtype=np.float64)
    low = df[cols["low"]].to_numpy(dtype=np.float64)
    c = df[cols["close"]].to_numpy(dtype=np.float64)
    return h, low, c


def build_adx_needle(
    df: pd.DataFrame, *, period: int = DEFAULT_PERIOD, window: int | None = DEFAULT_WINDOW
) -> pd.DataFrame:
    """OHLC DataFrame から ADX_NEEDLE の成果物 DataFrame を生成する。

    既定は因果ローリング窓（``window=DEFAULT_WINDOW``）で標準化し repaint しない。
    warm-up（先頭 window-1）は ``NaN``（非描画）。

    Args:
        df: ``high``/``low``/``close`` 列を持つ DataFrame（列名の大小不問・昇順）。
            元 index を引き継ぐ。
        period: ADX 平滑期間（既定 6。元 inpPeriod）。
        window: 標準化窓 W（既定 120＝因果。None で全期間バッチ）。

    Returns:
        index=入力 index、列= ``adx_needle`` / ``adx_level_count`` / ``adx``。

    Raises:
        KeyError: HLC 列が存在しない場合。
        ValueError: 行が空・period<=0 の場合。
    """
    h, low, c = _extract_hlc(df)
    result = compute_adx_needle(h, low, c, period=period, window=window)
    return pd.DataFrame(
        {
            NEEDLE_COLUMN: result.needle,
            LEVEL_COLUMN: result.level_count,
            ADX_COLUMN: result.adx,
        },
        index=df.index,
    )


def needle_levels(
    df: pd.DataFrame, *, period: int = DEFAULT_PERIOD, window: int | None = DEFAULT_WINDOW
) -> dict[str, float]:
    """σ 水準線（up_067..up_329 / dn_067..dn_329）とクランプ境界を返す。

    時系列ではなく価格軸（オシレーター軸）の水平参照線であるため、成果物 DataFrame と
    分離して提供する（元 ``PS_IndicatorLevelValueSet`` の水準）。

    Args:
        df: OHLC DataFrame。
        period: ADX 平滑期間（既定 6）。
        window: 標準化窓 W（既定 120＝因果。None で全期間バッチ）。

    Returns:
        ``up_*``/``dn_*`` の 12 水準に加え、``upper_clamp``/``lower_clamp`` を含む辞書。
    """
    h, low, c = _extract_hlc(df)
    result = compute_adx_needle(h, low, c, period=period, window=window)
    levels = dict(result.sigma_levels)
    levels["upper_clamp"] = result.upper_clamp
    levels["lower_clamp"] = result.lower_clamp
    return levels
