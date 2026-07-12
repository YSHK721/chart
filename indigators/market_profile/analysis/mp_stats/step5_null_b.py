"""Step5: 超過占有の定義 — Null B ブートストラップ → z(p)、POC* = argmax_p z(p)。

原子 = 分単位滞在（ffill 分グリッド close の行占有分数。Step1-3 後の合意により
最終原子は滞在時間へ帰結。実ティック滞在秒との照合は tick_dwell_check 参照）。

Null B（依頼者設計・Step2d と統合）:
  ブラケット b ごとに「日を跨いで」リサンプル — 分 j のステップを、j と同じ
  ブラケットスロットの一様ランダムな日 d' から取り、当日 open から乗法連鎖する。
  ŝ(b)（時間帯別ボラ）は保存され、日固有の水準再訪構造のみ破壊される。
  Null A（全日全ブラケット iid）は「必ず棄却・無情報」のため用いない（仕様）。

  z_d(p) = (N_obs(p) − Ê_NullB[N(p)]) / ŝd_NullB[N(p)]、POC*_d = argmax_p z_d(p)。

正規化: Step4 の指数により窓長 n の比較では N/n^{1−Ĥ} を用いる（本ステップは
n=1 日固定なので値に影響しない。指数はレポートへ引き渡す）。打ち切り条件なし。
"""

from __future__ import annotations

import numpy as np

from .data_prep import (
    DailyFeatures,
    SessionData,
    calendar_bracket_of_mod,
    ffill_close_grid,
    SESSION_OPEN_MOD,
    SESSION_CLOSE_MOD,
)
from .report import StepResult

N_ROWS = 40          # 主変種 raw_r40 と同一の行数
CHUNK = 2_000        # サロゲートのチャンク幅（メモリ有界化）


def _grid_mods() -> "np.ndarray":
    return np.arange(SESSION_OPEN_MOD, SESSION_CLOSE_MOD + 1, dtype=np.int32)


def _row_index(x: "np.ndarray", low: float, row_w: float, n_rows: int) -> "np.ndarray":
    """価格 → 行 index。範囲外は -1（帰無側は棄却、観測側は clip して使う）。"""
    idx = np.floor((x - low) / row_w).astype(np.int64)
    out = np.where((x >= low) & (idx <= n_rows - 1) & (idx >= 0), idx, -1)
    # 上端ちょうど（x == high）は最終行に含める
    return np.where(x == low + row_w * n_rows, n_rows - 1, out)


def observed_row_counts(grid_d: "np.ndarray", low: float, high: float) -> "np.ndarray":
    """観測日の行占有分数 N_obs(p)（(N_ROWS,)）。"""
    span = high - low
    row_w = span / N_ROWS if span > 0 else 1.0
    idx = _row_index(grid_d, low, row_w, N_ROWS)
    idx = np.clip(idx, 0, N_ROWS - 1)  # 観測 close は [low,high] 内（clip は端数保護）
    return np.bincount(idx, minlength=N_ROWS).astype(float)


def build_step_matrix(sd: SessionData, f: DailyFeatures) -> "np.ndarray":
    """(D, G) の分ステップ行列 S。S[d,0]=ln(grid[d,0]/open_d)、以降は隣接 log 差。

    サロゲートは S[d'(b), j] を分 j（ブラケット b(j)）ごとに集めて
    ln(open_d) + cumsum で連鎖する。
    """
    grid = ffill_close_grid(sd)
    lg = np.log(grid)
    S = np.empty_like(lg)
    S[:, 0] = lg[:, 0] - np.log(f.o)
    S[:, 1:] = np.diff(lg, axis=1)
    return S


def null_b_day(
    S: "np.ndarray",
    b_of_minute: "np.ndarray",
    d: int,
    open_d: float,
    low: float,
    high: float,
    *,
    rng,
    m_reps: int,
) -> "tuple[np.ndarray, np.ndarray]":
    """日 d の Null B 帰無占有の (mean(p), sd(p)) を返す（チャンク逐次・メモリ有界）。"""
    D, G = S.shape
    span = high - low
    row_w = span / N_ROWS if span > 0 else 1.0
    ssum = np.zeros(N_ROWS)
    ssq = np.zeros(N_ROWS)
    done = 0
    log_open = np.log(open_d)
    col = np.arange(G)[None, :]
    while done < m_reps:
        m = min(CHUNK, m_reps - done)
        days = rng.integers(0, D, size=(m, b_of_minute.max() + 1))
        day_mat = days[:, b_of_minute]                    # (m, G)
        s_surr = S[day_mat, col]                          # (m, G)
        prices = np.exp(log_open + np.cumsum(s_surr, axis=1))
        idx = _row_index(prices.ravel(), low, row_w, N_ROWS).reshape(m, G)
        # 行カウント（範囲外 -1 は捨てる）: 行 offset トリックで一括 bincount
        valid = idx >= 0
        flat = (idx + np.arange(m)[:, None] * N_ROWS)[valid]
        counts = np.bincount(flat, minlength=m * N_ROWS).reshape(m, N_ROWS).astype(float)
        ssum += counts.sum(axis=0)
        ssq += (counts**2).sum(axis=0)
        done += m
    mean = ssum / m_reps
    var = np.maximum(ssq / m_reps - mean**2, 0.0)
    return mean, np.sqrt(var)


def null_b_day_peaks(
    S: "np.ndarray",
    b_of_minute: "np.ndarray",
    d: int,
    open_d: float,
    low: float,
    high: float,
    *,
    rng,
    m_reps: int,
) -> "np.ndarray":
    """日 d の Null B サロゲート各回の「停止位置」（滞在最頻行の中心価格）を返す。

    Step6 移動先検定の帰無: 季節性を保存したデタラメな一筆書きが最も長く留まった
    場所。観測 POC* が過去の受容水準へ偶然以上に引き寄せられているかを、この
    帰無停止位置との距離比較で測る。行グリッドは観測と同一（[low,high] を N_ROWS
    等分）。範囲外に出たサロゲート分は棄却（全分が範囲外なら NaN）。
    """
    D, G = S.shape
    span = high - low
    row_w = span / N_ROWS if span > 0 else 1.0
    mid = (high + low) / 2.0
    centers = low + (np.arange(N_ROWS) + 0.5) * row_w
    # タイ規約（日中間値に近い行）を argmax で実現するための安定ソート順
    order = np.argsort(np.abs(centers - mid), kind="stable")
    log_open = np.log(open_d)
    col = np.arange(G)[None, :]
    out = np.empty(m_reps)
    done = 0
    while done < m_reps:
        m = min(CHUNK, m_reps - done)
        days = rng.integers(0, D, size=(m, b_of_minute.max() + 1))
        s_surr = S[days[:, b_of_minute], col]
        prices = np.exp(log_open + np.cumsum(s_surr, axis=1))
        idx = _row_index(prices.ravel(), low, row_w, N_ROWS).reshape(m, G)
        valid = idx >= 0
        flat = (idx + np.arange(m)[:, None] * N_ROWS)[valid]
        counts = np.bincount(flat, minlength=m * N_ROWS).reshape(m, N_ROWS)
        # mid 近接順に並べた counts の argmax → タイ時に mid 寄りが選ばれる
        c_ord = counts[:, order]
        best = order[np.argmax(c_ord, axis=1)]
        peak = centers[best]
        peak[counts.sum(axis=1) == 0] = np.nan
        out[done : done + m] = peak
        done += m
    return out


def run_step5(
    sd: SessionData,
    f: DailyFeatures,
    *,
    seed: int,
    m_reps: int = 10_000,
    normalization_exponent: "float | None" = None,
) -> "tuple[StepResult, dict[str, np.ndarray]]":
    """Step5 実行。全営業日の z_d(p) を構成し POC*_d を確定する。

    Returns:
        (StepResult, {"poc_star": (D,), "z_max": (D,), "poc_star_row": (D,),
                      "raw_poc_row_dist": (D,)})。Step6 の入力。
    """
    D = sd.n_days
    S = build_step_matrix(sd, f)
    grid = ffill_close_grid(sd)
    b_of_minute = calendar_bracket_of_mod(_grid_mods())
    rng = np.random.default_rng(seed)

    poc_star = np.full(D, np.nan)
    poc_star_row = np.full(D, np.nan)
    z_max = np.full(D, np.nan)
    raw_dist = np.full(D, np.nan)
    raw_poc = f.poc_price.get("raw_r40")

    for d in range(D):
        low, high = float(f.day_low[d]), float(f.day_high[d])
        span = high - low
        if span <= 0:
            continue
        row_w = span / N_ROWS
        n_obs = observed_row_counts(grid[d], low, high)
        mean, sd_null = null_b_day(
            S, b_of_minute, d, float(f.o[d]), low, high, rng=rng, m_reps=m_reps
        )
        with np.errstate(invalid="ignore", divide="ignore"):
            z = (n_obs - mean) / sd_null
        z[~np.isfinite(z)] = -np.inf  # sd=0 行は除外
        if not np.any(np.isfinite(z) & (z > -np.inf)):
            continue
        zmax = float(np.max(z))
        cand = np.flatnonzero(z == zmax)
        mid = (high + low) / 2.0
        centers = low + (np.arange(N_ROWS) + 0.5) * row_w
        row = int(cand[np.argmin(np.abs(centers[cand] - mid))])
        poc_star[d] = centers[row]
        poc_star_row[d] = row
        z_max[d] = zmax
        if raw_poc is not None:
            raw_dist[d] = abs(raw_poc[d] - centers[row]) / row_w

    valid = np.isfinite(z_max)
    disagree = raw_dist[valid] > 1.0  # 生 POC と 1 行超乖離
    stats: "dict[str, object]" = {
        "n_days": int(valid.sum()),
        "m_reps": m_reps,
        "z_max_median": float(np.median(z_max[valid])),
        "z_max_p05": float(np.percentile(z_max[valid], 5)),
        "z_max_p95": float(np.percentile(z_max[valid], 95)),
        "raw_poc_disagree_rate": float(np.mean(disagree)),
        "raw_poc_row_dist_median": float(np.median(raw_dist[valid])),
        "normalization_exponent": normalization_exponent,
        "n_rows": N_ROWS,
        "atom": "minute_dwell_ffill_close",
    }
    notes = (
        "定義ステップ（打ち切りなし）。POC* = argmax_p z(p)（生カウント最頻値ではない・"
        "仕様確定）。帰無は Null B（ブラケット別・日跨ぎリサンプル・ŝ(b) 保存・"
        "当日 open 連鎖）。原子は分単位滞在（実ティック滞在秒との照合は "
        "tick_dwell_check の結果を参照）。z_max が大きい日ほど、季節性では説明"
        "できない水準固有の受容が強い。POC* 系列は Step6 の入力。"
    )
    result = StepResult(
        step=5,
        name="excess_occupancy_null_b",
        decision="estimated",
        statistics=stats,
        notes=notes,
    )
    return result, {
        "poc_star": poc_star,
        "poc_star_row": poc_star_row,
        "z_max": z_max,
        "raw_poc_row_dist": raw_dist,
    }
