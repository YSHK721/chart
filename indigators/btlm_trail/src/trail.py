"""btlm_trail 成果物層: 価格系列 → ローリング窓末尾トレイル（ドット原子）＋バンド＋被覆率。

層名/責務:
    成果物層。core のローリング窓末尾 OLS を用い、各バーの btlm_mean（トレンド現在位置＝
    ドットの原子）・β・残差 σ を求め、分位ペアごとに 2 方式（名目 ols / 経験分位）の
    バンドを組む。経験分位は直近 N 本の乖離率の経験分位（因果・当該バー内固定＝非リペイント）。

バンド方式（正本仕様 §2）:
    (a) 名目 ols   : mean ± norm_ppf(q)·pred_sd（参照実装 build_btlm_bands と数値一致）。
    (b) 経験分位   : mean·(1 + 直近 N 本の乖離率 (close-mean)/mean の経験 q)。ウォークフォワード。

依存:
    標準: __future__, dataclasses / 外部: numpy, pandas / プロジェクト内: core
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from .core import (
    DEFAULT_EMP_N,
    DEFAULT_MAXBARS,
    DEFAULT_N_COV,
    DEFAULT_Q_HIGH,
    DEFAULT_Q_LOW,
    norm_ppf,
    resolve_source,
    rolling_ols_window_end,
)

_MIN_EMP_OBS: int = 2  # 経験分位算出に必要な最小の有限乖離本数


@dataclass(frozen=True)
class TrailResult:
    """btlm_trail のローリング成果。各配列は入力バーと同順・同長。

    Attributes:
        mean:      窓末尾 btlm_mean（トレンド現在位置＝ドットの原子）。
        beta:      回帰傾き β（方向の正式判定値）。
        sigma:     残差 σ（σ 正規化ストップ距離の計算資源）。
        band_low:  下側分位バンド（q_low）。
        band_high: 上側分位バンド（q_high）。
        band_method: "ols" / "empirical"。
    """

    mean: np.ndarray
    beta: np.ndarray
    sigma: np.ndarray
    band_low: np.ndarray
    band_high: np.ndarray
    band_method: str


def _validate_pair(q_low: float, q_high: float) -> tuple[float, float]:
    """単一分位ペアを検証する（0 < q_low < q_high < 1）。違反は ValueError。"""
    ql, qh = float(q_low), float(q_high)
    if not (0.0 < ql < qh < 1.0):
        raise ValueError(
            f"分位ペアは 0 < q_low < q_high < 1 が必要です: q_low={q_low}, q_high={q_high}"
        )
    return ql, qh


def _empirical_quantile_causal(
    deviations: np.ndarray, emp_n: int, q: float
) -> np.ndarray:
    """各バー t で直近 emp_n 本（t を含む・因果）の有限乖離の経験分位 q を返す。

    未来のバーは参照しない（確定バーの値は不変＝非リペイント）。有限本数が
    ``_MIN_EMP_OBS`` 未満のバーは NaN。
    """
    n = deviations.size
    out = np.full(n, np.nan)
    for t in range(n):
        start = max(0, t - emp_n + 1)
        window = deviations[start: t + 1]
        finite = window[np.isfinite(window)]
        if finite.size >= _MIN_EMP_OBS:
            out[t] = float(np.quantile(finite, q))
    return out


def build_btlm_trail(
    df,
    *,
    source: str = "close",
    maxbars: int = DEFAULT_MAXBARS,
    q_low: float = DEFAULT_Q_LOW,
    q_high: float = DEFAULT_Q_HIGH,
    band_method: str = "ols",
    empirical_n: int = DEFAULT_EMP_N,
) -> TrailResult:
    """価格 DataFrame から btlm_trail のローリング成果を組む（単一分位ペア）。

    Args:
        df: OHLC 列を持つ DataFrame（列名の大小不問）。
        source: 8 択ソース（close/open/high/low/hl2/hlc3/ohlc4/hlcc4）。回帰対象。
        maxbars: 回帰窓の本数（既定 100）。
        q_low/q_high: 分位ペア（0<q_low<q_high<1）。
        band_method: "ols"（名目）/ "empirical"（経験分位）。
        empirical_n: 経験分位バンドの参照本数（既定 500）。

    Returns:
        TrailResult。

    Raises:
        ValueError: 分位ペア不正・未知ソース・未知バンド方式・maxbars<3。
    """
    ql, qh = _validate_pair(q_low, q_high)
    method = str(band_method).lower()
    if method not in ("ols", "empirical"):
        raise ValueError(f"未知のバンド方式です: {band_method}")

    prices = resolve_source(df, source)
    mean, pred_sd, beta, sigma = rolling_ols_window_end(prices, maxbars)

    if method == "ols":
        band_low = mean + norm_ppf(ql) * pred_sd
        band_high = mean + norm_ppf(qh) * pred_sd
    else:
        # 経験分位: 乖離率 (close - mean)/mean の直近 emp_n 本の経験 q（因果）。
        lower = {str(c).lower(): c for c in df.columns}
        if "close" not in lower:
            raise ValueError("経験分位バンドには close 列が必要です。")
        close = df[lower["close"]].to_numpy(dtype=np.float64)
        with np.errstate(invalid="ignore", divide="ignore"):
            deviations = (close - mean) / mean
        emp_lo = _empirical_quantile_causal(deviations, empirical_n, ql)
        emp_hi = _empirical_quantile_causal(deviations, empirical_n, qh)
        band_low = mean * (1.0 + emp_lo)
        band_high = mean * (1.0 + emp_hi)

    return TrailResult(
        mean=mean, beta=beta, sigma=sigma,
        band_low=band_low, band_high=band_high, band_method=method,
    )


def rolling_coverage(
    close: np.ndarray, low: np.ndarray, high: np.ndarray, n_cov: int = DEFAULT_N_COV
) -> np.ndarray:
    """各バー t で直近 n_cov 本（t を含む）の「close がバンド内」割合を返す（因果）。

    バンドが有限なバーのみを分母に数える。有限本数 0 のバーは NaN。
    """
    close = np.asarray(close, dtype=np.float64).ravel()
    low = np.asarray(low, dtype=np.float64).ravel()
    high = np.asarray(high, dtype=np.float64).ravel()
    n = close.size
    inside = (close >= low) & (close <= high)
    valid = np.isfinite(low) & np.isfinite(high)
    out = np.full(n, np.nan)
    for t in range(n):
        start = max(0, t - n_cov + 1)
        v = valid[start: t + 1]
        denom = int(v.sum())
        if denom > 0:
            num = int((inside[start: t + 1] & v).sum())
            out[t] = num / denom
    return out


def realized_coverage_latest(
    close: np.ndarray, low: np.ndarray, high: np.ndarray, n_cov: int = DEFAULT_N_COV
) -> float:
    """最新確定バーの実現被覆率（直近 n_cov 本で close がバンド内の割合）を返す。

    有限被覆値が 1 つも無ければ NaN。
    """
    cov = rolling_coverage(close, low, high, n_cov)
    finite = cov[np.isfinite(cov)]
    return float(finite[-1]) if finite.size else float("nan")
