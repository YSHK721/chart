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
        off_low:   外れ値分位ラインの下側（分位 1-q_out）。q_out 無効時は None。
        off_high:  外れ値分位ラインの上側（分位 q_out）。q_out 無効時は None。
        band_method: "ols" / "empirical"。
        deviations: 乖離率 (close-mean)/mean（band_method="empirical" のときのみ。ols は None）。
            ISSUE-233: 増分計算が次バーの経験分位を求めるために要る（自前で式を持たない）。
    """

    mean: np.ndarray
    beta: np.ndarray
    sigma: np.ndarray
    band_low: np.ndarray
    band_high: np.ndarray
    band_method: str
    off_low: "np.ndarray | None" = None
    off_high: "np.ndarray | None" = None
    deviations: "np.ndarray | None" = None


def _validate_pair(q_low: float, q_high: float) -> tuple[float, float]:
    """単一分位ペアを検証する（0 < q_low < q_high < 1）。違反は ValueError。"""
    ql, qh = float(q_low), float(q_high)
    if not (0.0 < ql < qh < 1.0):
        raise ValueError(
            f"分位ペアは 0 < q_low < q_high < 1 が必要です: q_low={q_low}, q_high={q_high}"
        )
    return ql, qh


def deviation_ratio(close, mean):
    """乖離率 (close - mean) / mean（**唯一の定義**・スカラ/配列どちらも可）。

    経験分位バンドの原子。0 除算・NaN は errstate で抑制して非有限値のまま返す
    （呼び出し側が有限判定する契約）。ISSUE-233 の増分計算が確定バーの乖離率を継ぎ足す
    ために公開する（式を写さないため）。
    """
    with np.errstate(invalid="ignore", divide="ignore"):
        return (close - mean) / mean


def ols_band(mean, pred_sd, q: float):
    """名目 ols バンド端 mean ± norm_ppf(q)·pred_sd（**唯一の定義**・スカラ/配列可）。"""
    return mean + norm_ppf(q) * pred_sd


def empirical_band(mean, emp_q):
    """経験分位バンド端 mean·(1 + 経験分位)（**唯一の定義**・スカラ/配列可）。"""
    return mean * (1.0 + emp_q)


def empirical_quantile_latest(
    prior_deviations: np.ndarray, emp_n: int, q: float
) -> float:
    """``prior_deviations`` の **次のバー** に適用する経験分位 q を返す（因果・当該バー除外）。

    直近 emp_n 本（d_{t-N}..d_{t-1}）の有限値の経験分位。有限本数が ``_MIN_EMP_OBS`` 未満なら
    NaN。1 バーぶんの分位算出の **唯一の定義** であり、``_empirical_quantile_causal`` は
    本関数を各バーで呼ぶループである。

    ISSUE-233（B-2 承認）: 増分計算が「末尾 1 点だけ」を求めるための公開入口。ローリング版は
    実測 53.2ms（1386 本・emp_n=495）だが本関数 1 回は 0.037ms。

    Args:
        prior_deviations: 当該バーより前の乖離率系列（当該バーを含めない）。
        emp_n: 参照本数（末尾 emp_n 本を使う）。
        q: 分位（0..1）。
    """
    window = prior_deviations[max(0, prior_deviations.size - emp_n):]
    finite = window[np.isfinite(window)]
    if finite.size < _MIN_EMP_OBS:
        return float("nan")
    return float(np.quantile(finite, q))


def _empirical_quantile_causal(
    deviations: np.ndarray, emp_n: int, q: float
) -> np.ndarray:
    """各バー t で **当該バー t を除く** 直近 emp_n 本（d_{t-N}..d_{t-1}）の経験分位 q を返す。

    設計書 §4.3・C-4 の因果境界（当該バー除外）に従う。当該バー自身の乖離を分位算出に
    含めない＝「自分の乖離が自分を判定する分位に混入する」自己参照を遮断する（ISSUE-141）。
    未来のバーは参照しない（確定バーの値は不変＝非リペイント）。有限本数が
    ``_MIN_EMP_OBS`` 未満のバーは NaN。

    1 バーぶんの算出は :func:`empirical_quantile_latest` へ委譲する（定義は 1 箇所）。
    """
    n = deviations.size
    out = np.full(n, np.nan)
    for t in range(n):
        out[t] = empirical_quantile_latest(deviations[:t], emp_n, q)
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
    q_out=None,
) -> TrailResult:
    """価格 DataFrame から btlm_trail のローリング成果を組む（単一分位ペア）。

    Args:
        df: OHLC 列を持つ DataFrame（列名の大小不問）。
        source: 8 択ソース（close/open/high/low/hl2/hlc3/ohlc4/hlcc4）。回帰対象。
        maxbars: 回帰窓の本数（既定 100）。
        q_low/q_high: 分位ペア（0<q_low<q_high<1）。
        band_method: "ols"（名目）/ "empirical"（経験分位）。
        empirical_n: 経験分位バンドの参照本数（既定 500）。
        q_out: 外れ値分位（上側 q_out・下側 1-q_out で補助線）。有効条件 q_high < q_out < 1。
            None・範囲外・q_out<=q_high は無効化（off_low=off_high=None＝補助線なし）。
            算出はバンド方式と同一規約（ols=mean±norm_ppf(q_out)·pred_sd／経験分位=既存機構）。

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

    # 外れ値分位の有効性（黙って無効化＝補助線なし）。
    qo = None
    try:
        if q_out is not None and qh < float(q_out) < 1.0:
            qo = float(q_out)
    except (TypeError, ValueError):
        qo = None

    deviations = None
    if method == "ols":
        band_low = ols_band(mean, pred_sd, ql)
        band_high = ols_band(mean, pred_sd, qh)
    else:
        # 経験分位: 乖離率 (close - mean)/mean の直近 emp_n 本の経験 q（因果・当該バー除外＝設計書 §4.3）。
        lower = {str(c).lower(): c for c in df.columns}
        if "close" not in lower:
            raise ValueError("経験分位バンドには close 列が必要です。")
        close = df[lower["close"]].to_numpy(dtype=np.float64)
        deviations = deviation_ratio(close, mean)
        emp_lo = _empirical_quantile_causal(deviations, empirical_n, ql)
        emp_hi = _empirical_quantile_causal(deviations, empirical_n, qh)
        band_low = empirical_band(mean, emp_lo)
        band_high = empirical_band(mean, emp_hi)

    # 外れ値分位ライン（バンド方式と同一規約・上側 q_out／下側 1-q_out で上下対称）。
    off_low = off_high = None
    if qo is not None:
        if method == "ols":
            off_high = ols_band(mean, pred_sd, qo)
            off_low = ols_band(mean, pred_sd, 1.0 - qo)
        else:
            emp_off_hi = _empirical_quantile_causal(deviations, empirical_n, qo)
            emp_off_lo = _empirical_quantile_causal(deviations, empirical_n, 1.0 - qo)
            off_high = empirical_band(mean, emp_off_hi)
            off_low = empirical_band(mean, emp_off_lo)

    return TrailResult(
        mean=mean, beta=beta, sigma=sigma,
        band_low=band_low, band_high=band_high, band_method=method,
        off_low=off_low, off_high=off_high, deviations=deviations,
    )


def _coverage_flags(
    close: np.ndarray, low: np.ndarray, high: np.ndarray
) -> tuple[np.ndarray, np.ndarray]:
    """被覆率の判定フラグ（inside / valid）を組む（**唯一の定義**）。"""
    close = np.asarray(close, dtype=np.float64).ravel()
    low = np.asarray(low, dtype=np.float64).ravel()
    high = np.asarray(high, dtype=np.float64).ravel()
    inside = (close >= low) & (close <= high)
    valid = np.isfinite(low) & np.isfinite(high)
    return inside, valid


def _coverage_ratio(inside: np.ndarray, valid: np.ndarray) -> float:
    """1 窓ぶんの被覆率（バンドが有限なバーのみを分母に数える・**唯一の定義**）。"""
    denom = int(valid.sum())
    if denom <= 0:
        return float("nan")
    return int((inside & valid).sum()) / denom


def coverage_latest(
    close: np.ndarray, low: np.ndarray, high: np.ndarray, n_cov: int = DEFAULT_N_COV
) -> float:
    """**最新バー**（末尾）の被覆率を返す（直近 n_cov 本・当該バーを含む）。

    ``rolling_coverage(...)[-1]`` と同値で、ローリング全体を組まない。ISSUE-233 の増分計算が
    末尾 1 点だけを求めるための入口（ローリング版は実測 5.4ms／本関数は O(n_cov) の numpy 和）。
    有限本数 0 のときは NaN（``rolling_coverage`` と同じ）。
    """
    inside, valid = _coverage_flags(close, low, high)
    start = max(0, inside.size - n_cov)
    return _coverage_ratio(inside[start:], valid[start:])


def rolling_coverage(
    close: np.ndarray, low: np.ndarray, high: np.ndarray, n_cov: int = DEFAULT_N_COV
) -> np.ndarray:
    """各バー t で直近 n_cov 本（t を含む）の「close がバンド内」割合を返す（因果）。

    バンドが有限なバーのみを分母に数える。有限本数 0 のバーは NaN。判定フラグと 1 窓の
    割合算出は :func:`_coverage_flags` / :func:`_coverage_ratio` を共有する（定義は 1 箇所）。
    """
    inside, valid = _coverage_flags(close, low, high)
    n = inside.size
    out = np.full(n, np.nan)
    for t in range(n):
        start = max(0, t - n_cov + 1)
        out[t] = _coverage_ratio(inside[start: t + 1], valid[start: t + 1])
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
