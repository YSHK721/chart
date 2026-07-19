"""btlm_trail コア（純粋ロジック・外部 I/O 非依存・numpy のみ）。

層名/責務:
    core 層。tgp_btlm の ols 参照実装（``OlsBtlmFitter`` の単一区分ベイズ線形回帰）と
    数値一致する「回帰窓末尾値」の閉形式ローリング計算を提供する。各営業日 t で直近
    ``maxbars`` 本に OLS を当てはめ、窓末尾（当日位置）の予測平均・傾き β・残差 σ・
    予測 sd を求める。確定バーの値は以後不変（非リペイント）。

含む構造:
    * 既定パラメータ定数（maxbars / 分位ペア / 経験分位 N / 被覆率 N）
    * norm_ppf          : 標準正規の逆累積分布（Acklam 有理近似・scipy 非依存）
    * resolve_source    : 8 択ソース（applied_price 参照）→ 価格系列
    * rolling_ols_window_end : 系列 → (mean, pred_sd, beta, sigma) の窓末尾ローリング

参照実装（無改変）:
    ``indigators/tgp_btlm/src/{reference.py,bands.py}`` の ols 経路。本モジュールは
    その窓末尾値を閉形式で再現し、回帰テスト（test_trail）で 1e-6 一致を固定する。

依存:
    標準: __future__ / 外部: numpy / プロジェクト内: common.applied_price
"""

from __future__ import annotations

import numpy as np

from common.applied_price import AppliedPrice, applied_price

# 既定値（正本仕様 kind-twirling-hollerith.md §2 / FINDINGS §3）。
DEFAULT_MAXBARS: int = 100            # 回帰窓（tgp_btlm core.py DEFAULT_MAXBARS と同値）
DEFAULT_Q_LOW: float = 0.05
DEFAULT_Q_HIGH: float = 0.95
DEFAULT_EMP_N: int = 500              # 経験分位バンドの参照本数（FINDINGS 結論 B: 88.6%@500）
DEFAULT_N_COV: int = 250             # 実現被覆率のローリング本数
_MIN_OBS: int = 3                    # 分散推定に必要な最小観測数（OlsBtlmFitter と同一）

# UI ソース値（catalog の source enum）→ 共有 AppliedPrice 種別（moving_averages と同期）。
_SOURCE_TO_APPLIED = {
    "close": AppliedPrice.CLOSE,
    "open": AppliedPrice.OPEN,
    "high": AppliedPrice.HIGH,
    "low": AppliedPrice.LOW,
    "hl2": AppliedPrice.MEDIAN,
    "hlc3": AppliedPrice.TYPICAL,
    "hlcc4": AppliedPrice.WEIGHTED,
    "ohlc4": AppliedPrice.OHLC4,
}


def norm_ppf(p: float) -> float:
    """標準正規分布の逆累積分布関数（Acklam の有理近似・scipy 非依存）。

    tgp_btlm/src/core.norm_ppf と同一係数（参照実装との数値一致を保つため）。
    """
    a = (-3.969683028665376e+01, 2.209460984245205e+02, -2.759285104469687e+02,
         1.383577518672690e+02, -3.066479806614716e+01, 2.506628277459239e+00)
    b = (-5.447609879822406e+01, 1.615858368580409e+02, -1.556989798598866e+02,
         6.680131188771972e+01, -1.328068155288572e+01)
    c = (-7.784894002430293e-03, -3.223964580411365e-01, -2.400758277161838e+00,
         -2.549732539343734e+00, 4.374664141464968e+00, 2.938163982698783e+00)
    d = (7.784695709041462e-03, 3.224671290700398e-01, 2.445134137142996e+00,
         3.754408661907416e+00)
    if not (0.0 < p < 1.0):
        raise ValueError("p は 0 < p < 1 の範囲で指定してください。")
    plow, phigh = 0.02425, 1.0 - 0.02425
    if p < plow:
        q = np.sqrt(-2.0 * np.log(p))
        return (((((c[0]*q + c[1])*q + c[2])*q + c[3])*q + c[4])*q + c[5]) / \
               ((((d[0]*q + d[1])*q + d[2])*q + d[3])*q + 1.0)
    if p > phigh:
        q = np.sqrt(-2.0 * np.log(1.0 - p))
        return -(((((c[0]*q + c[1])*q + c[2])*q + c[3])*q + c[4])*q + c[5]) / \
                ((((d[0]*q + d[1])*q + d[2])*q + d[3])*q + 1.0)
    q = p - 0.5
    r = q * q
    return (((((a[0]*r + a[1])*r + a[2])*r + a[3])*r + a[4])*r + a[5]) * q / \
           (((((b[0]*r + b[1])*r + b[2])*r + b[3])*r + b[4])*r + 1.0)


def resolve_source(df, source: str) -> np.ndarray:
    """8 択ソース（close/open/high/low/hl2/hlc3/ohlc4/hlcc4）を float 配列で返す。

    合成価格の計算は共有 ``applied_price`` に委譲する（moving_averages と同一の写像）。
    列名の大小は問わない。
    """
    kind = _SOURCE_TO_APPLIED.get(str(source).lower())
    if kind is None:
        raise ValueError(f"未知のソースです: {source}")
    lower = {str(c).lower(): c for c in df.columns}

    def col(name: str) -> np.ndarray:
        if name not in lower:
            raise ValueError(f"ソース計算に必要な列がありません: {name}")
        return df[lower[name]].to_numpy(dtype=np.float64)

    return applied_price(kind, col("open"), col("high"), col("low"), col("close"))


def _window_end_scalar(z: np.ndarray) -> tuple[float, float, float, float]:
    """1 窓（昇順 z・x=1..w）に OLS を当て、窓末尾値を返す。

    Returns:
        (mean_end, pred_sd_end, beta1, sigma)。w<3 なら全て NaN。

    参照実装 OlsBtlmFitter.fit_predict の末尾要素と同一の線形代数（Φ=[1,x]、
    予測分散 s²·(1+leverage)）。
    """
    w = z.size
    if w < _MIN_OBS:
        return (np.nan, np.nan, np.nan, np.nan)
    x = np.arange(1.0, w + 1.0)
    phi = np.column_stack([np.ones(w), x])
    xtx_inv = np.linalg.inv(phi.T @ phi)
    beta = xtx_inv @ phi.T @ z
    fitted = phi @ beta
    residual = z - fitted
    s2 = float(residual @ residual) / (w - 2)
    phi_end = np.array([1.0, float(w)])
    leverage = float(phi_end @ xtx_inv @ phi_end)
    pred_sd = float(np.sqrt(s2 * (1.0 + leverage)))
    mean_end = float(fitted[-1])
    return (mean_end, pred_sd, float(beta[1]), float(np.sqrt(s2)))


def rolling_ols_window_end(
    prices: np.ndarray, maxbars: int = DEFAULT_MAXBARS
) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """各バー t で直近 min(maxbars, t+1) 本に OLS を当て、窓末尾量をローリングで返す。

    確定バー t の値は df[:t+1] のみに依存する（未来を使わない＝非リペイント・因果）。
    先頭の窓 < 3 本のバーは NaN。

    Args:
        prices: 価格系列（昇順・古い→新しい）。
        maxbars: 回帰窓の本数（既定 100）。

    Returns:
        (mean, pred_sd, beta, sigma)。各長さ n。
    """
    prices = np.asarray(prices, dtype=np.float64).ravel()
    n = prices.size
    mean = np.full(n, np.nan)
    pred_sd = np.full(n, np.nan)
    beta = np.full(n, np.nan)
    sigma = np.full(n, np.nan)
    if maxbars < _MIN_OBS:
        raise ValueError("maxbars は 3 以上が必要です（分散推定）。")
    for t in range(n):
        w = min(maxbars, t + 1)
        if w < _MIN_OBS:
            continue
        z = prices[t - w + 1: t + 1]
        m, ps, b1, s = _window_end_scalar(z)
        mean[t], pred_sd[t], beta[t], sigma[t] = m, ps, b1, s
    return mean, pred_sd, beta, sigma
