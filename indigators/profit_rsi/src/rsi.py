"""層名: 成果物層（pandas）。

責務:
    DataFrame から OHLC を小文字正規化抽出し、core 層（compute_rsi_full）で RSI 列を、
    levels 層（rsi_levels）で正常帯・外れ値水準の列を付与した DataFrame（元 index 継承）を
    返す薄い変換層。数値計算・適用価格選択・水準算出は core / levels（共有プリミティブ経由）
    へ委譲し、本層は列抽出・列名正規化・必須列欠落例外の I/O 契約のみを担う。

    列名は描画のライン名と完全一致させる（PORTING_GUIDE §5）。分位バンドの列名は分位から
    導く（`rsi_q10` / `rsi_q90`）ため、パラメータを変えると列名も変わる（tickvol と同規約）。

元 MQL 対応:
    OnCalculate 全体の入出力境界（OHLC ＋ Apply → RSI バッファ）。水準は元の σ 7 本ではなく
    因果ローリング分位＋POT/GPD（SPEC §5.4・承認 2026-08-02）。

依存:
    標準: __future__ / 外部: pandas, numpy
    同一パッケージ: core（compute_rsi_full, DEFAULT_*）, levels（rsi_levels, DEFAULT_*）。
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from common.event_quantiles import DEFAULT_K_EVENTS, DEFAULT_Q_OUT

from .core import (
    DEFAULT_APPLY,
    DEFAULT_RSI_PERIOD,
    compute_rsi_full,
)
from .levels import (
    DEFAULT_Q_HIGH,
    DEFAULT_Q_LOW,
    DEFAULT_WINDOW_N,
    rsi_levels,
)

# 出力列名。
RSI_COLUMN = "rsi"
#: 外れ値水準の列名（キー → 列名）。`_evq_` は経験的分位・`_gpd_` は GPD 外挿（共有命名規約）。
LEVEL_COLUMNS: dict[str, str] = {
    "ext_hi": f"{RSI_COLUMN}_evq_ext_hi",
    "ext_lo": f"{RSI_COLUMN}_evq_ext_lo",
    "gpd_hi": f"{RSI_COLUMN}_gpd_hi",
    "gpd_lo": f"{RSI_COLUMN}_gpd_lo",
}

# 抽出する必須入力列（小文字正規化後）。
_REQUIRED_COLUMNS = ("open", "high", "low", "close")


def quantile_column(q: float) -> str:
    """分位 q（0..1）に対応する正常帯の列名（例 0.10 -> ``rsi_q10``）。

    ``tickvol/src/lwc_chart._quantile_series_name`` と対称の命名。
    """
    return f"{RSI_COLUMN}_q{int(round(float(q) * 100))}"


def _extract_ohlc(df: pd.DataFrame) -> tuple[np.ndarray, ...]:
    """DataFrame から open/high/low/close を小文字正規化して抽出する。

    Args:
        df: 入力 DataFrame（列名の大小不問）。

    Returns:
        ``(open, high, low, close)`` の float64 ndarray タプル。

    Raises:
        KeyError: open/high/low/close のいずれかが欠落している場合。
    """
    lower_map = {str(c).lower(): c for c in df.columns}
    missing = [c for c in _REQUIRED_COLUMNS if c not in lower_map]
    if missing:
        raise KeyError(f"必須列が欠落しています: {missing}")
    return tuple(
        df[lower_map[c]].to_numpy(dtype=np.float64) for c in _REQUIRED_COLUMNS
    )


def build_rsi(
    df: pd.DataFrame,
    *,
    rsi_period: int = DEFAULT_RSI_PERIOD,
    apply: int = DEFAULT_APPLY,
    window_n: int = DEFAULT_WINDOW_N,
    q_low: float = DEFAULT_Q_LOW,
    q_high: float = DEFAULT_Q_HIGH,
    q_out: "float | None" = DEFAULT_Q_OUT,
    k_events: int = DEFAULT_K_EVENTS,
) -> pd.DataFrame:
    """RSI 列・正常帯 2 列・外れ値水準 4 列を付与した DataFrame（元 index 継承）を返す。

    Args:
        df: open/high/low/close を含む DataFrame（列名の大小不問）。
        rsi_period: RSI 期間（既定 6）。
        apply: 適用価格選択（既定 5 -> TYPICAL。core の APPLY_TO_PRICE 写像に従う）。
        window_n: 正常帯（＝POT 閾値）の因果ローリング窓（既定 500）。
        q_low / q_high: 正常帯の下側・上側分位（既定 0.10 / 0.90）。
        q_out: 超過エピソードの極端分位（既定 0.99。上側 q_out・下側 1−q_out）。
        k_events: 水準に使う直近イベント件数（既定 50）。

    Returns:
        ``rsi`` ＋ ``rsi_q{low}`` / ``rsi_q{high}``（正常帯）＋
        ``rsi_evq_ext_hi`` / ``rsi_gpd_hi`` / ``rsi_evq_ext_lo`` / ``rsi_gpd_lo`` を
        付与した DataFrame（元 index 継承）。未定義の水準は NaN。

    Raises:
        KeyError: 必須列欠落。
        ValueError: rsi_period<2 / OHLC 長不一致 / 水準パラメータ不正。
    """
    open_, high, low, close = _extract_ohlc(df)
    full = compute_rsi_full(
        open_, high, low, close,
        rsi_period=rsi_period, apply=apply,
    )
    out = df.copy()
    out[RSI_COLUMN] = full.rsi
    levels = rsi_levels(
        full.rsi, window_n=window_n, q_low=q_low, q_high=q_high,
        q_out=q_out, k_events=k_events,
    )
    out[quantile_column(q_low)] = levels["band_low"]
    out[quantile_column(q_high)] = levels["band_high"]
    for key, column in LEVEL_COLUMNS.items():
        out[column] = levels[key]
    return out
