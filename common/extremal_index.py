"""極値指標 θ の推定（Ferro–Segers intervals 推定量）。

①層名/責務:
    共有プリミティブ層（純粋ロジック・外部 I/O 非依存・numpy のみ）。
    閾値超過の**時間的クラスタ化**の強さを表す極値指標 θ ∈ (0, 1] を推定する。

②なぜ必要か:
    金融時系列の超過は独立に散らばらず塊で起きる。θ は「1 クラスタあたりの期待超過数」の
    逆数であり、**有効独立クラスタ数 ≈ θ · N_exceedance** を与える。θ を無視して超過を独立と
    扱うと標準誤差を過小評価する（Leadbetter 1983）。GPD 当てはめ・分位推定・裾の共変量検定
    のいずれでも、有効標本は超過数ではなく θ 補正後のクラスタ数で数える必要がある。

③推定量（Ferro & Segers 2003, JRSS-B 65(2), 545–556 の intervals estimator）:
    閾値超過時刻 T_1 < … < T_N から超過間隔 S_i = T_{i+1} − T_i（i = 1..N−1）を取り、

        max(S_i) ≤ 2 のとき: θ̂ = 2 (Σ S_i)² / ((N−1) Σ S_i²)
        それ以外          : θ̂ = 2 (Σ (S_i − 1))² / ((N−1) Σ (S_i − 1)(S_i − 2))

    を 1 で打ち切る。閾値の選択や宣言クラスタリングのパラメータ（run length 等）を要さない点が
    runs 推定量に対する利点で、そのぶん閾値感度を別途調べる必要がある。

④含む構造:
    intervals_estimator       : 超過時刻列 → θ̂（本体）。
    extremal_index_of_series  : 系列＋閾値 → θ̂・超過数・有効クラスタ数。
    armax_series              : 検証用の既知 θ 過程（θ = 1 − α）。

⑤依存: 標準 __future__ / 外部 numpy。
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

#: θ̂ を計算するのに必要な最小の超過間隔数（S_i の本数）。これ未満は推定不能（NaN）。
MIN_INTERVALS: int = 2


def intervals_estimator(exceedance_times: "np.ndarray") -> float:
    """超過時刻列から極値指標 θ̂ を返す（Ferro–Segers intervals 推定量）。

    Args:
        exceedance_times: 超過が起きた時点の**整数 index**（昇順・重複なし）。

    Returns:
        θ̂ ∈ (0, 1]。超過間隔が ``MIN_INTERVALS`` 本未満なら ``nan``。

    Notes:
        分母が 0 以下になる退化（全間隔が 1＝完全連続超過など）では ``nan`` ではなく
        1 で打ち切った値を返さず **nan** とする。数値が定義できない状態を 1（独立）と
        取り違えると、クラスタ化が最も強い場合に最も楽観的な答えを返してしまう。
    """
    t = np.asarray(exceedance_times, dtype=np.float64).ravel()
    if t.size < MIN_INTERVALS + 1:
        return float("nan")
    s = np.diff(t)
    if s.size < MIN_INTERVALS:
        return float("nan")
    n = s.size                      # = N − 1
    if float(s.max()) <= 2.0:
        num = 2.0 * float(s.sum()) ** 2
        den = n * float((s ** 2).sum())
    else:
        s1 = s - 1.0
        num = 2.0 * float(s1.sum()) ** 2
        den = n * float((s1 * (s - 2.0)).sum())
    if not np.isfinite(num) or not np.isfinite(den) or den <= 0.0:
        return float("nan")
    return float(min(1.0, num / den))


@dataclass(frozen=True)
class ExtremalIndexResult:
    """θ̂ と、そこから導かれる有効標本量。"""

    theta: float
    n_exceedances: int
    threshold: float
    #: 有効独立クラスタ数 ≈ θ · N。GPD 当てはめ・検定の有効標本はこちら。
    effective_clusters: float


def extremal_index_of_series(
    values: "np.ndarray", threshold: float, *, upper: bool = True
) -> ExtremalIndexResult:
    """系列と閾値から θ̂ と有効クラスタ数を返す。

    Args:
        values: 対象系列（NaN は超過判定から除外する）。
        threshold: 閾値 u。
        upper: True で ``values > u`` を超過とする。False なら ``values < u``（下側裾）。
    """
    v = np.asarray(values, dtype=np.float64).ravel()
    finite = np.isfinite(v)
    hit = (v > threshold) if upper else (v < threshold)
    idx = np.flatnonzero(finite & hit)
    theta = intervals_estimator(idx)
    n = int(idx.size)
    eff = theta * n if np.isfinite(theta) else float("nan")
    return ExtremalIndexResult(
        theta=theta, n_exceedances=n, threshold=float(threshold), effective_clusters=eff
    )


def armax_series(n: int, alpha: float, *, rng) -> "np.ndarray":
    """検証用の ARMAX（max-autoregressive）過程 ``X_t = max(α X_{t−1}, (1−α) Z_t)``。

    ``Z_t`` は標準 Fréchet。この過程の極値指標は **θ = 1 − α** と解析的に既知であり、
    推定量の正しさを実測で確かめられる（α=0 なら iid＝θ=1）。
    """
    if not (0.0 <= alpha < 1.0):
        raise ValueError("alpha は [0, 1) の範囲")
    u = rng.random(n)
    z = 1.0 / (-np.log(u))                      # 標準 Fréchet
    x = np.empty(n, dtype=np.float64)
    x[0] = z[0]
    for i in range(1, n):
        x[i] = max(alpha * x[i - 1], (1.0 - alpha) * z[i])
    return x
