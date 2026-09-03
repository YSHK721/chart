"""stats — 疑似VWAP 検証の統計検定（純関数・ISSUE-479 Wave2 M-4）。

帰無は一貫して**ブロック順列**（構成上の自己相関を保存する）で、標本は重ならないよう間引く。
numpy / pandas と因果ローリング分位バンド（``common.marod_bands``）だけに依存し、素材の出所を
知らない。
"""
from __future__ import annotations

import math
from typing import Any

import numpy as np

# 乖離率の閾値は既存の因果ローリング分位バンド（当該バー除外・非リペイント）を使う。
from common.marod_bands import quantile_bands


def _block_permute(x: np.ndarray, block: int, rng: np.random.Generator) -> np.ndarray:
    """長さ block のブロック単位で並べ替える（ブロック内の自己相関を保存する帰無）。"""
    n = x.size
    nb = max(1, math.ceil(n / block))
    blocks = [x[i * block : (i + 1) * block] for i in range(nb)]
    order = rng.permutation(len(blocks))
    return np.concatenate([blocks[i] for i in order])[:n]


def signed_state_test(
    close: np.ndarray, level: np.ndarray, h: int, block_bars: int, perms: int, seed: int
) -> "dict[str, Any]":
    """s=sign(close−level) の将来 h 本リターンに対する情報量を非重複標本 + ブロック順列で検定する。"""
    n = close.size
    valid = np.isfinite(level)
    start = int(np.argmax(valid)) if valid.any() else n
    idx = np.arange(start, n - h, h)          # 非重複標本（重なり窓の自己相関を作らない）
    idx = idx[np.isfinite(level[idx])]
    if idx.size < 30:
        return {"n": int(idx.size), "skipped": "標本不足"}
    s = np.sign(close[idx] - level[idx])
    r = np.log(close[idx + h] / close[idx])
    keep = (s != 0) & np.isfinite(r)
    s, r = s[keep], r[keep]
    if s.size < 30 or (s > 0).sum() < 5 or (s < 0).sum() < 5:
        return {"n": int(s.size), "skipped": "標本不足"}

    def stat(sv: np.ndarray) -> float:
        return float(r[sv > 0].mean() - r[sv < 0].mean())

    obs = stat(s)
    block = max(1, math.ceil(block_bars / h))  # バー単位のブロック長を標本単位へ換算
    rng = np.random.default_rng(seed)
    null = np.empty(perms, dtype="float64")
    for i in range(perms):
        null[i] = stat(_block_permute(s, block, rng))
    p = float((1 + np.sum(np.abs(null) >= abs(obs))) / (perms + 1))
    return {
        "n": int(s.size),
        "n_up": int((s > 0).sum()),
        "n_dn": int((s < 0).sum()),
        "above_bp": float(r[s > 0].mean() * 1e4),
        "below_bp": float(r[s < 0].mean() * 1e4),
        "diff_bp": float(obs * 1e4),
        "null_sd_bp": float(null.std() * 1e4),
        "p": p,
        "block_samples": int(block),
    }


def _non_overlapping(flags: np.ndarray, h: int, limit: int) -> np.ndarray:
    """成立バーから重ならないエントリー列を取る（採用したら h 本は次を取らない）。"""
    out: "list[int]" = []
    nxt = 0
    for t in np.flatnonzero(flags):
        if t < nxt or t >= limit:
            continue
        out.append(int(t))
        nxt = t + h
    return np.asarray(out, dtype=np.int64)


def deviation_test(
    close: np.ndarray,
    pvwap: np.ndarray,
    sma: np.ndarray,
    *,
    n: int,
    h: int,
    q: float,
    band_window: int,
    perms: int,
    seed: int,
) -> "list[dict[str, Any]]":
    """下方乖離ロング（押し目買い）で 疑似VWAP乖離率 と SMA乖離率 を比較する。

    閾値は乖離率の**因果ローリング経験分位**（当該バー除外・`common.marod_bands.quantile_bands`）。
    エントリーは h 本重ならないよう間引き、帰無は将来リターン系列のブロック順列
    （ブロック長 10N 本）＝エントリー時刻が将来リターンと無関係、という帰無。

    集合: pvwap 全体 / sma 全体 / pvwap のみ成立 / sma のみ成立 / 両方成立。
    「pvwap のみ成立」にエッジがあるかが、pv 列を足す価値の直接の判定になる。
    """
    dev_p = close / pvwap - 1.0
    dev_s = close / sma - 1.0
    lo_p, _ = quantile_bands(dev_p, window_n=band_window, q_low=q, q_high=1.0 - q)
    lo_s, _ = quantile_bands(dev_s, window_n=band_window, q_low=q, q_high=1.0 - q)
    sig_p = np.isfinite(lo_p) & np.isfinite(dev_p) & (dev_p <= lo_p)
    sig_s = np.isfinite(lo_s) & np.isfinite(dev_s) & (dev_s <= lo_s)

    limit = close.size - h
    r = np.full(close.size, np.nan, dtype="float64")
    r[:limit] = np.log(close[h:] / close[:limit])

    sets = {
        "pvwap": sig_p,
        "sma": sig_s,
        "pvwap_only": sig_p & ~sig_s,
        "sma_only": sig_s & ~sig_p,
        "both": sig_p & sig_s,
    }
    idxs = {k: _non_overlapping(v, h, limit) for k, v in sets.items()}
    obs = {k: (float(np.nanmean(r[i])) if i.size else float("nan")) for k, i in idxs.items()}

    base_idx = np.arange(0, limit, h)
    base = float(np.nanmean(r[base_idx]))

    rng = np.random.default_rng(seed)
    block = max(1, 10 * n)
    null = {k: np.empty(perms, dtype="float64") for k in sets}
    for j in range(perms):
        rp = _block_permute(r, block, rng)
        for k, i in idxs.items():
            null[k][j] = float(np.nanmean(rp[i])) if i.size else np.nan

    rows = []
    for k, i in idxs.items():
        if i.size < 20 or not math.isfinite(obs[k]):
            rows.append({"set": k, "n_entries": int(i.size), "skipped": "標本不足"})
            continue
        p = float((1 + np.sum(np.abs(null[k] - base) >= abs(obs[k] - base))) / (perms + 1))
        rows.append({
            "set": k,
            "n_entries": int(i.size),
            "mean_bp": obs[k] * 1e4,
            "base_bp": base * 1e4,
            "excess_bp": (obs[k] - base) * 1e4,
            "null_sd_bp": float(np.nanstd(null[k]) * 1e4),
            "p": p,
        })
    return rows


def contrast_test(
    state: np.ndarray,
    outcome: np.ndarray,
    *,
    n: int,
    h: int,
    q: float,
    band_window: int,
    perms: int,
    seed: int,
    mask: "np.ndarray | None" = None,
) -> "dict[str, Any]":
    """状態変数の因果分位で上位群 / 下位群へ分け、将来量の群間差を検定する。

    上位群 = state >= 因果ローリング (1−q) 分位、下位群 = state <= 因果ローリング q 分位
    （いずれも当該バー除外・`quantile_bands`）。標本は h 本ごとに重ならないよう間引く。
    帰無は将来量系列のブロック順列（ブロック長 10N 本）＝「群の時刻が将来量と無関係」。
    """
    lo, hi = quantile_bands(state, window_n=band_window, q_low=q, q_high=1.0 - q)
    limit = int(np.sum(np.isfinite(outcome)))
    limit = outcome.size - h
    top = np.isfinite(hi) & np.isfinite(state) & (state >= hi)
    bot = np.isfinite(lo) & np.isfinite(state) & (state <= lo)
    if mask is not None:
        top = top & mask
        bot = bot & mask
    i_top = _non_overlapping(top, h, limit)
    i_bot = _non_overlapping(bot, h, limit)
    i_top = i_top[np.isfinite(outcome[i_top])] if i_top.size else i_top
    i_bot = i_bot[np.isfinite(outcome[i_bot])] if i_bot.size else i_bot
    if i_top.size < 20 or i_bot.size < 20:
        return {"n_top": int(i_top.size), "n_bot": int(i_bot.size), "skipped": "標本不足"}

    def diff(y: np.ndarray) -> float:
        return float(np.nanmean(y[i_top]) - np.nanmean(y[i_bot]))

    obs = diff(outcome)
    rng = np.random.default_rng(seed)
    block = max(1, 10 * n)
    null = np.array([diff(_block_permute(outcome, block, rng)) for _ in range(perms)])
    return {
        "n_top": int(i_top.size), "n_bot": int(i_bot.size),
        "top": float(np.nanmean(outcome[i_top])),
        "bot": float(np.nanmean(outcome[i_bot])),
        "diff": obs,
        "null_sd": float(np.nanstd(null)),
        "p": float((1 + np.sum(np.abs(null) >= abs(obs))) / (perms + 1)),
    }


def entry_test(
    flags: np.ndarray,
    outcome: np.ndarray,
    *,
    n: int,
    h: int,
    perms: int,
    seed: int,
) -> "dict[str, Any]":
    """成立バーの将来量が、全時点の基準値を超えるかを検定する（ブロック順列帰無）。"""
    limit = outcome.size - h
    idx = _non_overlapping(flags, h, limit)
    idx = idx[np.isfinite(outcome[idx])] if idx.size else idx
    if idx.size < 20:
        return {"n_entries": int(idx.size), "skipped": "標本不足"}
    base = float(np.nanmean(outcome[np.arange(0, limit, h)]))
    obs = float(np.nanmean(outcome[idx]))
    rng = np.random.default_rng(seed)
    block = max(1, 10 * n)
    null = np.array([float(np.nanmean(_block_permute(outcome, block, rng)[idx]))
                     for _ in range(perms)])
    return {
        "n_entries": int(idx.size), "mean_bp": obs * 1e4, "base_bp": base * 1e4,
        "excess_bp": (obs - base) * 1e4, "null_sd_bp": float(np.nanstd(null) * 1e4),
        "p": float((1 + np.sum(np.abs(null - base) >= abs(obs - base))) / (perms + 1)),
    }


def holm(pvals: "list[float]") -> "list[float]":
    """Holm 補正後の p 値（単調化込み）。"""
    m = len(pvals)
    order = np.argsort(pvals)
    adj = np.empty(m, dtype="float64")
    run = 0.0
    for rank, i in enumerate(order):
        run = max(run, (m - rank) * pvals[i])
        adj[i] = min(1.0, run)
    return [float(v) for v in adj]
