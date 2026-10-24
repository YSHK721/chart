"""cvfe の HAR 係数だけを替えて σ̂ を出す（RESULTS.md §9）。測定量・窓の成分・予測の式は cvfe の部品をそのまま使う。

観測の境界: ブートストラップの係数推定は ``bootstrap_fit(fit=...)`` の注入点だけを通る。
"""
from __future__ import annotations

from dataclasses import replace

import numpy as np

from indigators.cvfe.src.dto import HAR_LAG_MONTH, CvfeParams
from indigators.cvfe.src.engine import CvfeSequential, build_training_sample, fit_state
from indigators.cvfe.src.har import har_fit
from indigators.cvfe.src.ohlc import bar_edges_from_times, infer_bar_interval_sec, measures_from_ohlc, ohlc_quality_report

N_HAR = 500


def prepare(o, h, l, c, times, refit_every: int = 0):
    """OHLC から cvfe の測定量の列・パラメータ・品質報告を作る（compute_cvfe_from_ohlc と同じ手順）。"""
    interval = infer_bar_interval_sec(times)
    params = CvfeParams(bar_interval_sec=interval, n_har=N_HAR, lam_gap=0.97, refit_every=refit_every)
    edges = bar_edges_from_times(times, interval)
    return measures_from_ohlc(o, h, l, c, edges), params, ohlc_quality_report()


def sigma_series(measures, params, quality, beta=None, s2=None) -> np.ndarray:
    """各足の σ̂（足 t − 1 までの情報で出した足 t の見積もり）。beta・s2 を渡すと HAR 係数だけ差し替える。"""
    state = fit_state(measures, quality, params)
    if beta is not None:
        state = replace(state, har_coef=np.asarray(beta, dtype=np.float64), har_resid_var=float(s2))
    seq = CvfeSequential(state, params)
    out = np.full(len(measures), np.nan)
    for m in measures:
        s, _, _, ok = seq.push(m)
        if ok:
            out[m.index] = s
    return out


def training_rows(measures):
    """系列の全部の足を学習標本にした (X, y)（cvfe の build_training_sample をそのまま使う）。"""
    c = np.array([m.c for m in measures], dtype=np.float64)
    j = np.array([m.j for m in measures], dtype=np.float64)
    p = np.array([m.p_close for m in measures], dtype=np.float64)
    return build_training_sample(c, j, p, HAR_LAG_MONTH - 1, len(measures) - 2)


def block_indices(n_rows: int, block: int, rng: np.random.Generator) -> np.ndarray:
    """長さ block の連続した行のまとまりを復元抽出し、n_rows 行にそろえた添字。"""
    n_blocks = int(np.ceil(n_rows / block))
    starts = rng.integers(0, n_rows - block + 1, size=n_blocks)
    return (starts[:, None] + np.arange(block)[None, :]).ravel()[:n_rows]


def bootstrap_fit(x, y, block: int, n_boot: int, rng: np.random.Generator, fit=har_fit):
    """ブロックブートストラップで n_boot 回係数を決め、β と s² の平均、平均の見積もり（対数分散）の列を返す。"""
    betas, s2s, level = [], [], []
    for _ in range(n_boot):
        idx = block_indices(len(y), block, rng)
        b, s2 = fit(x[idx], y[idx])
        betas.append(b)
        s2s.append(s2)
        level.append(float(b[0] + x.mean(axis=0) @ b[1:]))
    return np.mean(betas, axis=0), float(np.mean(s2s)), np.array(level)
