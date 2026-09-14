"""予測精度の評価手続き（仕様 §5）。

層名/責務:
    純粋ロジック層。``engine`` に依存しない。入力は「代理変数の系列」と
    「各モデルの σ̂ 系列」だけであり、予測がどう作られたかを知らない。
    これにより M0〜M4 を同一の面で比較できる（仕様 §5.3）。

損失関数（仕様 §5.1・Patton 2011）:
    主指標 QLIKE、副指標 MSE。MAE・MAPE・R² は使用しない。

モデル比較（仕様 §5.2）:
    * 2 モデル：Diebold–Mariano (1995)。分散は Newey–West HAC、
      ラグ ``floor(4·(T/100)^(2/9))``、両側 5%。
    * 3 モデル以上：Model Confidence Set（Hansen, Lunde & Nason 2011）。
      ``1 − α_MCS = 0.90``、stationary bootstrap（Politis & Romano 1994）、
      平均ブロック長 20、``B = 10,000``、乱数シード固定。

    Newey–West のラグは仕様どおり ``floor`` で丸める。既存の
    ``indigators/market_profile/analysis/mp_stats/stats_core.py`` は ``ceil`` を用いる
    別実装であり、丸めが仕様と異なるため参照しない（アクターも analysis 層で異なる）。
    定常ブートストラップの添字生成のみ共有プリミティブ
    ``common.stats_boot.stationary_bootstrap_indices`` を無改変参照する。

依存: 標準 math / 外部 numpy / プロジェクト内 common.stats_boot。
"""

from __future__ import annotations

import math
from typing import Mapping, NamedTuple

import numpy as np

from common.stats_boot import norm_cdf, stationary_bootstrap_indices

#: 仕様 §5.2：Model Confidence Set の既定値。
MCS_ALPHA: float = 0.10
MCS_BLOCK: int = 20
MCS_B: int = 10_000
MCS_SEED: int = 20260729

#: 仕様 §5.2：Diebold–Mariano の有意水準（両側）。
DM_ALPHA: float = 0.05

#: ブートストラップ反復の分割幅（中間配列のメモリ上限を決める。結果には影響しない）。
_BOOT_CHUNK: int = 500


def qlike(proxy: np.ndarray, sigma_hat: np.ndarray) -> np.ndarray:
    """``QLIKE_t = V^proxy_t / σ̂_t² − ln(V^proxy_t / σ̂_t²) − 1``（仕様 §5.1）。

    ``proxy <= 0`` または ``σ̂ <= 0``・非有限のバーは ``nan``（比較対象から除外される）。
    """
    p = np.asarray(proxy, dtype=np.float64)
    s = np.asarray(sigma_hat, dtype=np.float64)
    out = np.full(p.shape, np.nan, dtype=np.float64)
    ok = np.isfinite(p) & np.isfinite(s) & (p > 0.0) & (s > 0.0)
    ratio = p[ok] / (s[ok] ** 2)
    out[ok] = ratio - np.log(ratio) - 1.0
    return out


def mse_loss(proxy: np.ndarray, sigma_hat: np.ndarray) -> np.ndarray:
    """``MSE_t = (V^proxy_t − σ̂_t²)²``（仕様 §5.1・副指標）。"""
    p = np.asarray(proxy, dtype=np.float64)
    s = np.asarray(sigma_hat, dtype=np.float64)
    out = np.full(p.shape, np.nan, dtype=np.float64)
    ok = np.isfinite(p) & np.isfinite(s)
    out[ok] = (p[ok] - s[ok] ** 2) ** 2
    return out


def newey_west_lag(t_obs: int) -> int:
    """仕様 §5.2：``floor(4·(T/100)^(2/9))``。"""
    if t_obs <= 0:
        return 0
    return int(math.floor(4.0 * (t_obs / 100.0) ** (2.0 / 9.0)))


def newey_west_lrvar(d: np.ndarray, lag: int) -> float:
    """Newey–West HAC による長期分散（Bartlett カーネル）。"""
    d = np.asarray(d, dtype=np.float64)
    t_obs = d.size
    if t_obs < 2:
        return float("nan")
    dc = d - d.mean()
    gamma0 = float((dc * dc).sum() / t_obs)
    total = gamma0
    for k in range(1, min(lag, t_obs - 1) + 1):
        gamma_k = float((dc[k:] * dc[:-k]).sum() / t_obs)
        total += 2.0 * (1.0 - k / (lag + 1.0)) * gamma_k
    return total


class DmResult(NamedTuple):
    """Diebold–Mariano 統計量・両側 p 値・有効標本数・使用ラグ。"""

    stat: float
    p_value: float
    n_obs: int
    lag: int


def diebold_mariano(loss_a: np.ndarray, loss_b: np.ndarray) -> DmResult:
    """仕様 §5.2：2 モデルの損失系列に対する Diebold–Mariano (1995) 検定。

    ``d_t = loss_a - loss_b``。``stat < 0`` は A（第 1 引数）の損失が小さいことを意味する。
    両モデルで有限な標本のみを用いる。
    """
    a = np.asarray(loss_a, dtype=np.float64)
    b = np.asarray(loss_b, dtype=np.float64)
    ok = np.isfinite(a) & np.isfinite(b)
    d = a[ok] - b[ok]
    t_obs = d.size
    if t_obs < 3:
        return DmResult(float("nan"), float("nan"), t_obs, 0)
    lag = newey_west_lag(t_obs)
    lrvar = newey_west_lrvar(d, lag)
    if not np.isfinite(lrvar) or lrvar <= 0.0:
        return DmResult(float("nan"), float("nan"), t_obs, lag)
    stat = float(d.mean() / math.sqrt(lrvar / t_obs))
    p = 2.0 * (1.0 - norm_cdf(abs(stat)))
    return DmResult(stat, float(p), t_obs, lag)


class McsResult(NamedTuple):
    """Model Confidence Set の生存集合と除去順序・各段の p 値。"""

    surviving: tuple[str, ...]
    eliminated: tuple[str, ...]
    p_values: tuple[float, ...]


def model_confidence_set(losses: Mapping[str, np.ndarray], *,
                         alpha: float = MCS_ALPHA, block: int = MCS_BLOCK,
                         n_boot: int = MCS_B, seed: int = MCS_SEED) -> McsResult:
    """仕様 §5.2：Hansen, Lunde & Nason (2011) の Model Confidence Set（``T_max`` 版）。

    手順（信頼水準 ``1 − alpha``）:
        1. 生存集合 M の各モデル i について ``d̄_i = mean_{j≠i} mean_t(L_it − L_jt)``
        2. 定常ブートストラップ（平均ブロック長 ``block``・反復 ``n_boot``）で
           ``d̄_i`` の標準偏差を推定し ``t_i = d̄_i / sd_i``
        3. ``T_max = max_i t_i`` をブートストラップ帰無分布と比較
        4. ``p < alpha`` なら ``argmax_i t_i`` を除去して 1 に戻る。そうでなければ終了

    全モデルで有限な標本のみを用いる（比較の同一標本性を保つ）。
    乱数はシード固定であり、同一入力で同一結果を返す。
    """
    names = list(losses.keys())
    if len(names) < 2:
        return McsResult(tuple(names), (), ())

    mat = np.vstack([np.asarray(losses[k], dtype=np.float64) for k in names])
    ok = np.all(np.isfinite(mat), axis=0)
    mat = mat[:, ok]
    t_obs = mat.shape[1]
    if t_obs < 3:
        return McsResult(tuple(names), (), ())

    rng = np.random.default_rng(seed)
    # 定常ブート添字は除去ラウンドをまたいで固定する（同一の帰無分布上で比較するため）。
    boot_idx = np.vstack([stationary_bootstrap_indices(t_obs, block, rng)
                          for _ in range(n_boot)])

    alive = list(range(len(names)))
    eliminated: list[str] = []
    p_values: list[float] = []

    while len(alive) > 1:
        sub = mat[alive]                                   # (m, T)
        m = len(alive)
        # d̄_i：他モデルとの平均損失差（列平均を用いた同値形）。
        col_mean = sub.mean(axis=0)
        dev = sub - col_mean                               # (m, T)
        d_bar = dev.mean(axis=1) * (m / (m - 1.0))

        # dev[:, boot_idx] は (m, B, T) となり B = 10,000・T = 1,000 で 320 MB を要する。
        # 反復を分割して中間配列を 1/10 以下に抑える（結果は分割数に依存しない）。
        boot = np.empty((m, n_boot), dtype=np.float64)
        step = max(1, _BOOT_CHUNK)
        for lo in range(0, n_boot, step):
            hi = min(lo + step, n_boot)
            boot[:, lo:hi] = dev[:, boot_idx[lo:hi]].mean(axis=2)
        boot *= (m / (m - 1.0))
        sd = boot.std(axis=1, ddof=1)
        sd = np.where(sd > 0.0, sd, np.nan)

        t_stat = d_bar / sd
        if not np.any(np.isfinite(t_stat)):
            # ブートストラップ標準偏差が全モデルで 0（損失差が標本内で定数）。
            # 標本変動が無く優劣を統計的に判別できないため、全モデルを生存とする。
            break
        boot_t = (boot - d_bar[:, None]) / sd[:, None]
        t_max = float(np.nanmax(t_stat))
        boot_max = np.nanmax(boot_t, axis=0)
        p = float(np.mean(boot_max >= t_max))
        p_values.append(p)

        if p >= alpha:
            break
        worst = int(np.nanargmax(t_stat))
        eliminated.append(names[alive[worst]])
        alive.pop(worst)

    return McsResult(tuple(names[i] for i in alive), tuple(eliminated), tuple(p_values))
