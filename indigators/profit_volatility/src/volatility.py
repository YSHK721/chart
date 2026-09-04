"""PRO!fit_Volatility の成果物層（DataFrame 入出力アダプタ）。

層名/責務:
    成果物層。OHLC を持つ pandas DataFrame を入力に取り、core 層
    （``compute_core_volatility``）を呼び出して、クランプ済み標準化系列の列を付与した
    DataFrame・σ12 水準辞書を返す。本層は列抽出・index 継承のみを担う。

計算の本質（本質コアへ修正済み）:
    49 系列合算は実証上 実効 1 次元（PC1 が分散の 94.4%）であり、加重値(OHLC4)どうしの
    6 本変化 1 本で合算を 100% 再現できる。本層が呼ぶ ``compute_core_volatility`` は、その
    本質 1 本だけを保持し、乖離を **値幅 → 対数差 ln(ohlc4[a]/ohlc4[a-period])** に変えて
    価格水準依存（値幅は水準に比例し非定常）を除去したパターンを算出する。

依存:
    標準: なし / 外部: pandas / プロジェクト内: src.core
"""

from __future__ import annotations

import pandas as pd

# ISSUE-009: 絶対 import `from src.core`（＋sys.path への parents[1] 挿入）は top-level 名 `src`
# を汚染し、adapter が他指標を先にロード済みのとき `src.core` が誤束縛され ImportError を招く。
# 他14指標と同じ相対 import へ統一し、`_<indicator>_src` 名前空間に閉じて衝突を断つ。
from .core import (
    DEFAULT_PERIOD,
    DEFAULT_WINDOW,
    compute_core_volatility,
)

# 成果物 DataFrame に付与する列名（クランプ済み標準化系列）。
LEVEL_COUNT_COLUMN: str = "volatility_lc"

# OHLC の論理名 → DataFrame からの抽出キー（列名は大小不問）。
_OHLC_KEYS: tuple[str, ...] = ("open", "high", "low", "close")


def _extract_ohlc(df: pd.DataFrame) -> dict[str, "pd.Series"]:
    """DataFrame から O/H/L/C 列を大小不問で抽出する。

    Args:
        df: OHLC を含む DataFrame。

    Returns:
        ``{"open": ..., "high": ..., "low": ..., "close": ...}``。

    Raises:
        KeyError: 必須列（O/H/L/C いずれか）が欠落している場合。
    """
    lower_map = {str(col).lower(): col for col in df.columns}
    extracted: dict[str, pd.Series] = {}
    for key in _OHLC_KEYS:
        if key not in lower_map:
            raise KeyError(f"必須列が見つかりません: {key}")
        extracted[key] = df[lower_map[key]]
    return extracted


def build_volatility(
    df: pd.DataFrame,
    *,
    period: int = DEFAULT_PERIOD,
    window: int | None = DEFAULT_WINDOW,
) -> pd.DataFrame:
    """OHLC DataFrame からクランプ済み標準化系列の列を付与した DataFrame を返す。

    本質コア（OHLC4 の対数 period 本変化を標準化した 1 系列）を ±3.29σ クランプした値。
    既定は **因果ローリング窓**（``window=DEFAULT_WINDOW``）で標準化するため、確定した
    バーは新データ追加でも値が変わらない（repaint しない）。warm-up（先頭 ``period+window-1``
    付近まで）は算出不能のため ``NaN``（非描画）。

    Args:
        df: O/H/L/C 列（大小不問）を含む DataFrame。
        period: 変化をとる足数（既定 6 = 測定幅）。
        window: 標準化窓 W（直近参照本数。既定 120）。``None`` で全期間バッチ
            （look-ahead あり・比較用）。

    Returns:
        ``LEVEL_COUNT_COLUMN`` 列を持つ DataFrame（元 index を継承）。

    Raises:
        KeyError: 必須列が欠落している場合。
    """
    ohlc = _extract_ohlc(df)
    res = compute_core_volatility(
        ohlc["open"].to_numpy(dtype=float),
        ohlc["high"].to_numpy(dtype=float),
        ohlc["low"].to_numpy(dtype=float),
        ohlc["close"].to_numpy(dtype=float),
        period=period,
        window=window,
    )
    return pd.DataFrame(
        {LEVEL_COUNT_COLUMN: res.level_count_clamped},
        index=df.index,
    )


def volatility_levels(
    df: pd.DataFrame,
    *,
    period: int = DEFAULT_PERIOD,
    window: int | None = DEFAULT_WINDOW,
) -> dict[str, float]:
    """OHLC DataFrame から σ12 水準線辞書（up_*/dn_*）を返す。

    Args:
        df: O/H/L/C 列（大小不問）を含む DataFrame。
        period: 変化をとる足数（既定 6）。
        window: 標準化窓 W（既定 120）。``None`` で全期間バッチ。

    Returns:
        σ12 水準（``up_067``..``up_329`` / ``dn_067``..``dn_329``）の辞書。

    Raises:
        KeyError: 必須列が欠落している場合。
    """
    ohlc = _extract_ohlc(df)
    res = compute_core_volatility(
        ohlc["open"].to_numpy(dtype=float),
        ohlc["high"].to_numpy(dtype=float),
        ohlc["low"].to_numpy(dtype=float),
        ohlc["close"].to_numpy(dtype=float),
        period=period,
        window=window,
    )
    return dict(res.levels)
