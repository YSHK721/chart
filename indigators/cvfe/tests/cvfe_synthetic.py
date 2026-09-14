"""CVFE テスト用の合成ティック生成（テスト専用・src からは参照しない）。

生成モデル:
    バー ``i`` の場中を等間隔 ``tick_sec`` でサンプルした対数価格ブラウン運動とする。
    バー内の総分散が ``sigma_bar[i]**2`` になるよう 1 ステップの標準偏差を
    ``sigma_bar[i] / sqrt(n_step)`` に取る（＝バー分散 = sigma_bar**2 が真値）。

    観測値には次を任意で重畳できる。
      * マイクロストラクチャノイズ  p_obs = p_true + omega * eps,
        ``omega = noise_omega_ratio * (1 ステップの真の収益 sd)``
        （凍結より先に適用する。凍結は観測値が更新されない現象であるため）
      * ジャンプ  指定バーの場中央で ``jump_size_sigma * sigma_bar[i]`` を加算
      * 気配凍結  ``freeze_fraction`` の時間割合だけ mid を直前値で固定
      * ギャップ  ``session_sec < bar_sec`` のとき場間が空き、バー始値に
        ``N(0, gap_sigma**2)`` のギャップを与える
      * 部分的なギャップ  ``early_close_bars`` に指定したバーだけ引けを早める
        （§4.7-1 条件 2 は `bar_edges[t] − バー t−1 の最後のティック` で判定するため、
         バー ``i`` を早仕舞いさせるとバー ``i+1`` がギャップ保有になる）。
      * 寄り遅れ        ``late_open_bars`` に指定したバーだけ最初の
        ``late_open_sec`` 秒のティックを落とす（当該バーの寄りを遅らせる）。
        ISSUE-216 の裁定後、これはギャップ判定には効かない（条件 2 が当該バーの
        ティックを見ないため）。バー内サンプル数を減らす用途で用いる

すべて ``numpy`` の Generator（シード固定）で生成し、同一シードで bit 再現する。
"""

from __future__ import annotations

import numpy as np

# 2010-01-01T00:00:00Z。仕様の unix 秒入力に合わせた既定の系列開始時刻。
T0_DEFAULT = 1_262_304_000.0

# E[-min(z, 0)] = 1/sqrt(2π)（標準正規の下側半期待値）。レバレッジ項の中心化に用いる。
_HALF_ABS_Z_MEAN = float(1.0 / np.sqrt(2.0 * np.pi))


def make_dataset(
    n_bars: int,
    *,
    bar_sec: int = 3600,
    tick_sec: int = 5,
    session_sec: int | None = None,
    seed: int = 0,
    sigma_bar: float | np.ndarray = 0.01,
    gap_sigma: float = 0.0,
    noise_omega_ratio: float = 0.0,
    freeze_fraction: float = 0.0,
    freeze_block_sec: int = 120,
    empty_bars: tuple[int, ...] = (),
    late_open_bars: tuple[int, ...] = (),
    late_open_sec: int = 1_200,
    early_close_bars: tuple[int, ...] = (),
    early_close_sec: int = 1_200,
    jump_bars: tuple[int, ...] = (),
    jump_size_sigma: float = 0.0,
    t0: float = T0_DEFAULT,
    base_price: float = 10_000.0,
):
    """合成ティックとバー境界を返す。

    Returns
    -------
    ticks : np.ndarray shape (K, 2) float64
        列 ``[unix_time_sec, mid_price]``。時刻は狭義単調増加・価格は正。
    bar_edges : np.ndarray shape (n_bars + 1,) float64
        等間隔のバー境界。
    """
    if session_sec is None:
        session_sec = bar_sec
    if session_sec > bar_sec:
        raise ValueError("session_sec は bar_sec 以下でなければならない")

    rng = np.random.default_rng(seed)
    n_step = int(session_sec // tick_sec)
    if n_step < 2:
        raise ValueError("1 バーに 2 点以上必要")

    sig = np.asarray(sigma_bar, dtype=np.float64)
    if sig.ndim == 0:
        sig = np.full(n_bars, float(sig))
    if sig.shape != (n_bars,):
        raise ValueError("sigma_bar は スカラ または shape (n_bars,)")

    bar_edges = t0 + np.arange(n_bars + 1, dtype=np.float64) * float(bar_sec)
    offsets = np.arange(n_step, dtype=np.float64) * float(tick_sec)

    empty = set(int(i) for i in empty_bars)
    late = set(int(i) for i in late_open_bars)
    # ISSUE-216 の裁定後、§4.7-1 の条件 2 は `bar_edges[t] − バー t−1 の最後のティック` で
    #   判定する。したがって「バー t をギャップ保有にする」操作子は **バー t−1 の早仕舞い**
    #   である（寄り遅れ late_open は当該バーのティックを見ないため判定に効かない）。
    early = set(int(i) for i in early_close_bars)
    jumps = set(int(i) for i in jump_bars)

    times_all: list[np.ndarray] = []
    logp_all: list[np.ndarray] = []
    p_last = np.log(base_price)

    for i in range(n_bars):
        step_sd = sig[i] / np.sqrt(n_step)
        incr = rng.standard_normal(n_step) * step_sd
        if gap_sigma > 0.0:
            incr[0] += rng.standard_normal() * gap_sigma
        if i in jumps:
            incr[n_step // 2] += jump_size_sigma * sig[i]

        p_true = p_last + np.cumsum(incr)
        p_last = p_true[-1]

        # ノイズ → 凍結 の順に適用する。気配凍結（スロットリング）は「観測値が
        # 更新されない」現象であり、凍結中の値にノイズが乗り続けることはない。
        # 逆順にすると凍結区間がノイズで壊れ、凍結率が測れなくなる。
        p_obs = p_true
        if noise_omega_ratio > 0.0:
            omega = noise_omega_ratio * step_sd
            p_obs = p_obs + rng.standard_normal(n_step) * omega
        if freeze_fraction > 0.0:
            p_obs = _apply_freeze(p_obs, tick_sec, freeze_block_sec, freeze_fraction, rng)

        t = bar_edges[i] + offsets
        if i in late:                       # 当該バーだけ寄り付きを遅らせる
            keep = offsets >= float(late_open_sec)
            t, p_obs = t[keep], p_obs[keep]
        if i in early:                      # 当該バーだけ早く引ける（＝次バーがギャップ保有）
            keep = (t - bar_edges[i]) < float(session_sec - early_close_sec)
            if keep.sum() >= 2:
                t, p_obs = t[keep], p_obs[keep]
        if i in empty:
            t, p_obs = t[:1], p_obs[:1]     # ティック 1 本 ＝ E06 の条件

        times_all.append(t)
        logp_all.append(p_obs)

    times = np.concatenate(times_all)
    logp = np.concatenate(logp_all)
    ticks = np.empty((times.size, 2), dtype=np.float64)
    ticks[:, 0] = times
    ticks[:, 1] = np.exp(logp)
    return ticks, bar_edges


def _apply_freeze(p, tick_sec, block_sec, fraction, rng):
    """``fraction`` の時間割合だけ、長さ ``block_sec`` の区間で mid を直前値に固定する。

    仕様 §4.1-4 の凍結判定（60 秒以上の連続不変）を成立させるため ``block_sec`` は
    既定 120 秒（>= 60 秒）とする。
    """
    n = p.size
    per_block = max(2, int(block_sec // tick_sec))
    n_blocks = int(round(fraction * n / per_block))
    if n_blocks <= 0:
        return p
    out = p.copy()
    # ブロック開始位置を重複なく等間隔に配置する（乱数依存を最小化し再現性を保つ）。
    stride = max(per_block, n // max(1, n_blocks))
    starts = np.arange(0, n - per_block, stride)[:n_blocks]
    for s in starts:
        out[s : s + per_block] = out[s]
    return out


def stochastic_vol_series(n_bars: int, *, seed: int, phi: float = 0.98,
                          sd_ln_sigma: float = 0.30, mean_ln_sigma: float = np.log(0.01)):
    """`ln σ_t` が AR(1)（係数 ``phi``）に従う確率ボラ系列を返す（§9 段階 2）。

    定常分布の標準偏差が ``sd_ln_sigma`` になるようイノベーション分散を設定する。
    """
    rng = np.random.default_rng(seed)
    eta = sd_ln_sigma * np.sqrt(1.0 - phi * phi)
    x = np.empty(n_bars, dtype=np.float64)
    x[0] = rng.standard_normal() * sd_ln_sigma
    for i in range(1, n_bars):
        x[i] = phi * x[i - 1] + rng.standard_normal() * eta
    return np.exp(mean_ln_sigma + x)


def make_sv_dataset(n_bars: int, *, bar_sec: int = 3600, tick_sec: int = 5,
                    seed: int = 0, phi: float = 0.98, sd_ln_sigma: float = 0.30,
                    mean_ln_sigma: float = float(np.log(0.01)),
                    leverage: float = 0.0, jump_prob: float = 0.0,
                    jump_size_sigma: float = 0.0, base_price: float = 10_000.0,
                    t0: float = T0_DEFAULT):
    """`ln σ_t` の AR(1) に、レバレッジ項と稀なジャンプを任意で加えた DGP。

    ``ln σ_t = mean + x_t``、
    ``x_t = φ x_{t−1} + lev · ( −min(ρ_{t−1}, 0)/σ_{t−1} − E[|z|]/2 ) + η ε_t``。
    レバレッジ項は平均 0 に中心化する（``E[−min(z,0)] = 1/sqrt(2π)``）。中心化しないと
    ``φ = 0.98`` の持続性の下で ``x`` が発散する。
    ``ρ_t`` は当該バーの終値対数収益（＝実現値）であり、σ の駆動と価格の生成が
    同一の経路上で閉じる（レバレッジ効果が真に存在する DGP になる）。

    ジャンプは各バー独立に確率 ``jump_prob`` で発生し、場中央に
    ``jump_size_sigma × σ_t`` を加算する。
    """
    rng = np.random.default_rng(seed)
    n_step = int(bar_sec // tick_sec)
    eta = sd_ln_sigma * np.sqrt(1.0 - phi * phi)

    bar_edges = t0 + np.arange(n_bars + 1, dtype=np.float64) * float(bar_sec)
    offsets = np.arange(n_step, dtype=np.float64) * float(tick_sec)

    times_all, logp_all = [], []
    p_last = np.log(base_price)
    x = float(rng.standard_normal() * sd_ln_sigma)
    rho_prev, sig_prev = 0.0, float(np.exp(mean_ln_sigma))

    for i in range(n_bars):
        if i > 0:
            lev_term = (-min(rho_prev, 0.0) / sig_prev) - _HALF_ABS_Z_MEAN
            x = phi * x + leverage * lev_term + float(rng.standard_normal()) * eta
        sig = float(np.exp(mean_ln_sigma + x))

        incr = rng.standard_normal(n_step) * (sig / np.sqrt(n_step))
        if jump_prob > 0.0 and rng.random() < jump_prob:
            incr[n_step // 2] += jump_size_sigma * sig

        p = p_last + np.cumsum(incr)
        rho_prev, sig_prev, p_last = float(p[-1] - p_last), sig, float(p[-1])

        times_all.append(bar_edges[i] + offsets)
        logp_all.append(p)

    times = np.concatenate(times_all)
    logp = np.concatenate(logp_all)
    ticks = np.empty((times.size, 2), dtype=np.float64)
    ticks[:, 0] = times
    ticks[:, 1] = np.exp(logp)
    return ticks, bar_edges
