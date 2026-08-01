"""btlm_trail コア（純粋ロジック・外部 I/O 非依存・numpy のみ）。

層名/責務:
    core 層。tgp_btlm の ols 参照実装（``OlsBtlmFitter`` の単一区分ベイズ線形回帰）と
    数値一致する「回帰窓末尾値」の閉形式ローリング計算を提供する。各営業日 t で直近
    ``maxbars`` 本に OLS を当てはめ、窓末尾（当日位置）の予測平均・傾き β・残差 σ・
    予測 sd を求める。確定バーの値は以後不変（非リペイント）。

含む構造:
    * 既定パラメータ定数（maxbars / 分位ペア / 経験分位 N / 被覆率 N）
    * norm_ppf          : 標準正規の逆累積分布（実体は共有 ``common.normal_dist``・再エクスポート）
    * resolve_source    : 8 択ソース（applied_price 参照）→ 価格系列
    * window_end_scalar : 1 窓 → (mean, pred_sd, beta, sigma)（末尾 1 点だけ要る増分経路の入口）
    * rolling_ols_window_end : 系列 → (mean, pred_sd, beta, sigma) の窓末尾ローリング

参照実装（無改変）:
    ``indigators/tgp_btlm/src/{reference.py,bands.py}`` の ols 経路。本モジュールは
    その窓末尾値を閉形式で再現し、回帰テスト（test_trail）で 1e-6 一致を固定する。

依存:
    標準: __future__ / 外部: numpy /
    プロジェクト内: common.applied_price, common.normal_dist, common.ols_fit
"""

from __future__ import annotations

import numpy as np

from common.applied_price import SOURCE_TO_APPLIED, applied_price
from common.ols_fit import ols_fit, pred_sd_at

# Acklam 有理近似の実体は共有プリミティブへ 1 本化した（ISSUE-179 項目 3）。スカラ経路が
# 旧ローカル実装と **ビット一致** することを実測（14,007 点で tobytes 不一致 0）してから統合。
# 本モジュールの公開面（``src/__init__.py`` の ``__all__``）を保つため同名で再エクスポートする。
from common.normal_dist import norm_ppf

# 既定値（正本仕様 kind-twirling-hollerith.md §2 / FINDINGS §3）。
DEFAULT_MAXBARS: int = 100            # 回帰窓（tgp_btlm core.py DEFAULT_MAXBARS と同値）
DEFAULT_Q_LOW: float = 0.05
DEFAULT_Q_HIGH: float = 0.95
DEFAULT_EMP_N: int = 500              # 経験分位バンドの参照本数（FINDINGS 結論 B: 88.6%@500）
DEFAULT_N_COV: int = 250             # 実現被覆率のローリング本数
_MIN_OBS: int = 3                    # 分散推定に必要な最小観測数（OlsBtlmFitter と同一）

# UI ソース値（catalog の source enum）→ 共有 AppliedPrice 種別（moving_averages と同期）。
#   写像の実体は共有プリミティブへ 1 本化した（ISSUE-179 項目 4）。
_SOURCE_TO_APPLIED = SOURCE_TO_APPLIED


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


def window_end_scalar(z: np.ndarray) -> tuple[float, float, float, float]:
    """1 窓（昇順 z・x=1..w）に OLS を当て、窓末尾値を返す。

    Returns:
        (mean_end, pred_sd_end, beta1, sigma)。w<3 なら全て NaN。

    参照実装 OlsBtlmFitter.fit_predict の末尾要素と同一の線形代数（Φ=[1,x]、
    予測分散 s²·(1+leverage)）。

    ISSUE-233（B-2 承認）: 増分計算が「末尾 1 窓だけ」を計算するための公開入口。
    ``rolling_ols_window_end`` は本関数を各バーで呼ぶループであり、末尾 1 点だけ要る
    経路が窓全体（実測 1386 窓 = 28.9ms）を走る必要は無い（本関数 1 回 = 0.021ms）。
    計算式・分岐・境界は非公開時から 1 文字も変えていない。
    """
    w = z.size
    if w < _MIN_OBS:
        return (np.nan, np.nan, np.nan, np.nan)
    x = np.arange(1.0, w + 1.0)
    fit = ols_fit(x, z)
    # leverage は端点ベクトル形（``pred_sd_at``）を用いる。全行 einsum 形の末尾要素とは
    # 総和順序が異なり最終ビットが一致しないため、共有側でも 2 形を統合していない。
    pred_sd = pred_sd_at(np.array([1.0, float(w)]), fit.xtx_inv, fit.s2)
    mean_end = float(fit.fitted[-1])
    return (mean_end, pred_sd, float(fit.beta[1]), float(np.sqrt(fit.s2)))


# 旧非公開名の別名（実体は同一オブジェクト＝二重定義を作らない）。既存の内部参照・
# ドキュメント記述との互換のために残す。
_window_end_scalar = window_end_scalar


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
        m, ps, b1, s = window_end_scalar(z)
        mean[t], pred_sd[t], beta[t], sigma[t] = m, ps, b1, s
    return mean, pred_sd, beta, sigma
