"""market_profile_zp_kernel — z(p) 超過占有スコアの純数学コア（ISSUE-133 SRP）。

ISSUE-133（SRP）: :mod:`market_profile_zp` に同居していた「統計コア」アクター（分グリッド化・観測占有・
Null B モーメント・fine z・POC*・sessions エントリ）を、キャッシュ協調／運用バッチ アクターから分離した。
本モジュールは I/O・ディスク/メモリキャッシュ・serving オーケストレーションを一切持たない純関数と、
その内部格子定数のみを持つ（numpy と共有カーネル :mod:`null_b_kernel`・POC/VA 単一定義のみに依存）。

``market_profile_zp`` は本モジュールの全公開シンボルを再エクスポートし、既存の呼出面
（``zp.minute_close_grid`` / ``zp.null_b_moments_abs`` / ``zp.W_LOG`` 等）と数値を完全に温存する。
数学の出典・格子定義（bp 相対 log 一様）の詳細は :mod:`market_profile_zp` の module docstring を参照。
"""
from __future__ import annotations

import math as _math
import zlib as _zlib

import numpy as np

from market_profile_api.compute import null_b_kernel as _null_b  # 帰無サロゲート純カーネル（step5 と共有）
from market_profile_api.compute.market_profile import _value_area

# セッション窓（ブローカー分オフセット＝セッション日始端 NY17:00 ET からの経過分・ISSUE-078）。
SESSION_OPEN_MOD = 60      # ブローカー 01:00（セッション始端から 60 分）
SESSION_CLOSE_MOD = 1394   # ブローカー 23:14（最終取引可能分・夏冬同値）
BRACKET_BASE_MOD = 60      # ブラケット起点 ブローカー 01:00
BRACKET_MIN = 30
G_MINUTES = SESSION_CLOSE_MOD - SESSION_OPEN_MOD + 1                    # 1335
K_BRACKETS = (SESSION_CLOSE_MOD - BRACKET_BASE_MOD) // BRACKET_MIN + 1  # 45

# 内部格子（bp 相対・log 一様）。
ZP_BP = 1.0                          # セル幅（価格比 bp）。校正スキャン＋依頼者裁定（2026-07-15）。
W_LOG = _math.log1p(ZP_BP / 1e4)     # log 格子幅。k = floor(ln(price)/W_LOG)。

CHUNK = _null_b.CHUNK  # サロゲートのチャンク幅（step5 と共有カーネル＝rng 消費順一致・二重実装解消）

# 分オフセット（セッション窓内 index 0..G-1）→ 暦時間ブラケット index（0..K-1）。
_B_OF_MINUTE = (
    (np.arange(SESSION_OPEN_MOD, SESSION_CLOSE_MOD + 1) - BRACKET_BASE_MOD) // BRACKET_MIN
).astype(np.int32)


def day_seed(symbol: str, day_start: int) -> int:
    """決定論 seed（プロセス跨ぎ再現・hash() のランダム化非依存）。"""
    return _zlib.crc32(f"zp:{symbol}:{int(day_start)}".encode())


# --------------------------------------------------------------------------- #
# 分グリッド（ティック → 分末 mid ffill）
# --------------------------------------------------------------------------- #
def minute_close_grid(
    secs: "np.ndarray", mids: "np.ndarray", day_start: int
) -> "tuple[np.ndarray, float] | None":
    """ティック → セッション窓 1378 分の ffill close グリッド ``(closes[G], open_d)``。

    分 j の close = その分内**最後**の tick mid（secs 昇順前提・決定論）。欠測分は直前値 ffill、
    先頭欠測は当日最初のセッション窓 tick の mid（= open_d）。窓内 tick ゼロは None。
    """
    secs = np.asarray(secs, dtype=np.int64)
    mids = np.asarray(mids, dtype=np.float64)
    if secs.size == 0:
        return None
    mod = (secs - int(day_start)) // 60
    keep = (mod >= SESSION_OPEN_MOD) & (mod <= SESSION_CLOSE_MOD)
    if not np.any(keep):
        return None
    m = (mod[keep] - SESSION_OPEN_MOD).astype(np.int64)
    v = mids[keep]
    grid = np.full(G_MINUTES, np.nan)
    # 分内最後の tick を採る（m は昇順 → searchsorted 右端-1 が最後の occurrence・決定論）。
    uniq = np.unique(m)
    pos = np.searchsorted(m, uniq, side="right") - 1
    grid[uniq] = v[pos]
    open_d = float(v[0])
    mask = np.isnan(grid)
    idx = np.where(~mask, np.arange(G_MINUTES), -1)
    np.maximum.accumulate(idx, out=idx)
    filled = np.where(idx >= 0, grid[np.maximum(idx, 0)], open_d)
    return filled, open_d


def obs_cell_counts(
    closes: "np.ndarray", klo: int, khi: int, *, col_lo: int = 0, col_hi: "int | None" = None
) -> "np.ndarray":
    """観測の行占有分数 N_obs(k)（(khi-klo+1,)）。k = floor(ln(close)/W_LOG) を [klo,khi] へ clip。

    ISSUE-079: bp 相対（log 一様）格子。col_lo/col_hi で部分日（当日 forming・replay 境界日）の
    カラム範囲に限定できる。観測 close は集計元の日レンジ内に自然に収まるため clip は端数保護のみ。
    """
    c = np.asarray(closes, dtype=float)
    c = c[col_lo : (None if col_hi is None else col_hi)]
    k = np.clip(np.floor(np.log(c) / W_LOG).astype(np.int64) - int(klo), 0, int(khi) - int(klo))
    return np.bincount(k, minlength=int(khi) - int(klo) + 1).astype(float)


# --------------------------------------------------------------------------- #
# ステップ行列と Null B モーメント（絶対グリッド版）
# --------------------------------------------------------------------------- #
def build_step_matrix(mgrids: "np.ndarray", opens: "np.ndarray") -> "np.ndarray":
    """(L, G) の分ステップ行列 S（共有カーネル :func:`null_b_kernel.build_step_matrix` へ委譲）。

    ISSUE-094 🔴-2: step5_null_b と同式（入力が SessionData でなく mgrid/open 配列な点のみ違う）を
    純カーネルへ一元化した。zp は配列を直接渡す（step5 は ffill_close_grid(sd)/f.o を渡す薄い協調部）。
    """
    return _null_b.build_step_matrix(mgrids, opens)


def null_b_moments_abs(
    S: "np.ndarray",
    open_d: float,
    klo: int,
    khi: int,
    *,
    rng,
    m_reps: int,
    col_lo: int = 0,
    col_hi: "int | None" = None,
) -> "tuple[np.ndarray, np.ndarray]":
    """絶対グリッド版 Null B モーメント ``(mean(k), var(k))``（(C,)、C=khi−klo+1）。

    step5_null_b.null_b_day と同一アルゴリズム（ブラケット別・日跨ぎリサンプル・open 連鎖・
    レンジ外棄却・チャンク逐次でメモリ有界）。rng の消費順も同一（days → チャンク単位）。
    col_lo/col_hi はサロゲート占有の累積カラム範囲（部分日は経過分までに限定しないと
    mean が全日分に膨らみ z が負へ系統偏向する）。sd でなく var を返す（窓合算 Σvar 用）。
    """
    L, G = S.shape
    hi = G if col_hi is None else int(col_hi)
    lo = int(col_lo)
    C = int(khi) - int(klo) + 1
    ssum = np.zeros(C)
    ssq = np.zeros(C)
    log_open = np.log(float(open_d))
    done = 0
    while done < m_reps:
        m = min(CHUNK, m_reps - done)
        # ISSUE-094 🔴-2: サロゲート log 価格連鎖は共有カーネルへ委譲（step5 と同一 rng 消費順）。
        logp = _null_b.surrogate_logprice_chunk(S, log_open, _B_OF_MINUTE, rng=rng, m=m)[:, lo:hi]
        # ISSUE-079: log 格子＝log 価格のまま floor（exp 不要・絶対格子時代は exp→floor(price/GRID_W)）。
        idx = np.floor(logp / W_LOG).astype(np.int64) - int(klo)
        valid = (idx >= 0) & (idx < C)
        flat = (idx + np.arange(m)[:, None] * C)[valid]
        counts = np.bincount(flat, minlength=m * C).reshape(m, C).astype(float)
        ssum += counts.sum(axis=0)
        ssq += (counts**2).sum(axis=0)
        done += m
    mean = ssum / m_reps
    var = np.maximum(ssq / m_reps - mean**2, 0.0)
    return mean, var


def null_b_period_moments(
    S: "np.ndarray",
    open_d: float,
    klo: int,
    khi: int,
    period_col_bounds: "list[tuple[int, int]]",
    *,
    rng,
    m_reps: int,
) -> "list[tuple[np.ndarray, np.ndarray]]":
    """周期別 Null B モーメント（tf-period 列用）。1 回のサロゲート生成を周期カラム範囲で分割集計。

    period_col_bounds: 各周期の (col_lo, col_hi)（半開・セッション窓内 index）。
    Returns: 周期ごとの (mean(k), var(k))。
    """
    L, G = S.shape
    C = int(khi) - int(klo) + 1
    P = len(period_col_bounds)
    ssum = np.zeros((P, C))
    ssq = np.zeros((P, C))
    log_open = np.log(float(open_d))
    done = 0
    while done < m_reps:
        m = min(CHUNK, m_reps - done)
        # ISSUE-094 🔴-2: サロゲート log 価格連鎖は共有カーネルへ委譲（step5 と同一 rng 消費順）。
        logp = _null_b.surrogate_logprice_chunk(S, log_open, _B_OF_MINUTE, rng=rng, m=m)  # ISSUE-079: log 格子。
        idx_all = np.floor(logp / W_LOG).astype(np.int64) - int(klo)
        row_off = np.arange(m)[:, None] * C
        for j, (lo, hi) in enumerate(period_col_bounds):
            idx = idx_all[:, int(lo):int(hi)]
            valid = (idx >= 0) & (idx < C)
            flat = (idx + row_off)[valid]
            counts = np.bincount(flat, minlength=m * C).reshape(m, C).astype(float)
            ssum[j] += counts.sum(axis=0)
            ssq[j] += (counts**2).sum(axis=0)
        done += m
    out = []
    for j in range(P):
        mean = ssum[j] / m_reps
        var = np.maximum(ssq[j] / m_reps - mean**2, 0.0)
        out.append((mean, var))
    return out


# --------------------------------------------------------------------------- #
# fine z・POC*・sessions エントリ（純数学）
# --------------------------------------------------------------------------- #
def _session_entry_zp(date: str, z_disp: "np.ndarray", poc_price: float, centers, va_pct: float) -> dict:
    """zp 版の sessions エントリ。z は負値を含むため VA は clip(z,0) に既存 _value_area を適用。"""
    clipped = np.maximum(z_disp, 0.0)
    va_low, va_high = _value_area(clipped, centers, va_pct)
    return {
        "date": date,
        "tpo": [round(float(v), 2) for v in z_disp],
        "poc": round(float(poc_price), 2),
        "va_low": round(float(va_low), 2),
        "va_high": round(float(va_high), 2),
    }


def _fine_z(obs: "np.ndarray", mean: "np.ndarray", var: "np.ndarray") -> "np.ndarray":
    """z = (obs − mean)/√var（var<=0 のセルは z=0＝帰無情報なし）。"""
    with np.errstate(invalid="ignore", divide="ignore"):
        z = (obs - mean) / np.sqrt(var)
    z[~np.isfinite(z)] = 0.0
    return z


def _poc_star_from_fine(z: "np.ndarray", kw0: int, mid_price: float) -> float:
    """fine 解像度の POC* = argmax z（タイは窓中間値へ最も近いセル・step5 規約）。

    ISSUE-079: セル中心価格は exp((k+0.5)·W_LOG)（log 格子の価格化）。
    """
    zmax = float(z.max()) if z.size else 0.0
    cand = np.flatnonzero(z == zmax)
    centers = np.exp((kw0 + cand + 0.5) * W_LOG)
    return float(centers[np.argmin(np.abs(centers - mid_price))])
