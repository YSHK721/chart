"""market_profile_zp — 超過占有スコア z(p) プロファイル計算（src=zp・純関数コア）。

z(p) = (N_obs(p) − Ê_NullB[N(p)]) / √(Var_NullB[N(p)])。
原子＝分単位滞在（ティック → 分末 mid の ffill グリッド 1378 分 → GRID_W 絶対価格グリッド級の
分数カウント）。帰無 Null B ＝ 30 分ブラケット別に「日を跨いで」分ステップ列をリサンプルし当日
open から乗法連鎖したサロゲート（時間帯別ボラ ŝ(b) を保存し、日固有の水準受容構造のみ破壊）。
観測と帰無は同一分解能（分・GRID_W セル）で対称に測る。

数学の出典は検定パイプライン（indigators/market_profile/analysis/mp_stats/step5_null_b.py・
実データ検証済み）。本モジュールは live API 用の移植であり analysis 層には依存しない（定数は
複製し、パリティテスト test_zp_step5_parity.py で数値一致とドリフト防止を担保する）。

オフライン検定との差分（意図的）:
  - 帰無ソースは当日から遡る直近 NULL_HIST_DAYS 完了日のみ（因果・ルックアヘッドなし。
    オフラインは全日プールだった）。
  - グリッドは日相対 40 行でなく GRID_W 絶対グリッド（dwell rollup と同一 kmin 量子化）。
    これにより日間の窓合算 z_win = (Σobs − Σmean)/√(Σvar) が同一セル上で成立する
    （日間・セル間とも独立近似。サロゲート内セル計数は総分数制約で負相関優勢のため
    √Σvar は sd の過大評価側＝z は 0 側へ保守的）。

依存方向: numpy のみ＋ :mod:`market_profile_dwell` の GRID_W（単一定義）を import。I/O なし
（ロールアップ・キャッシュ・profile 組み立ては本モジュール下段のオーケストレーション関数が担い、
ティック読込は market_profile_dwell._load_window_ticks を call-time 参照で再利用する）。
"""

from __future__ import annotations

import time as _time
import zlib as _zlib
from pathlib import Path as _Path
from typing import Any

import numpy as np
import pandas as pd

# GRID_W（絶対価格グリッド）と tick ローダ・symbol 解決は dwell 側の単一定義を再利用する。
# _load_window_ticks は call-time 参照（_mpd._load_window_ticks(...)）＝既存テストの
# monkeypatch 単一注入点を温存する。
from market_profile_api.compute import market_profile_dwell as _mpd
from market_profile_api.compute.market_profile import _value_area
from market_profile_api.compute.market_profile_dwell import GRID_W
from market_profile_api.compute.market_profile_zp_store import ZpStore

# repo 根は _mpd の import 時に sys.path へ挿入済み（marketdata 解決）。
from marketdata import paths as _paths  # noqa: E402
from marketdata.tick_m1 import day_parquet_files  # noqa: E402
# セッション日境界（ISSUE-078・NY17:00 ET 基準）。日切り・完了判定・ラベルの唯一の規則源。
from marketdata.session_day import (  # noqa: E402
    next_session_day_start,
    session_date_label,
    session_day_start,
)

# セッション窓（ブローカー分オフセット＝セッション日始端 NY17:00 ET からの経過分・ISSUE-078）。
#   実測（JP225 CFD）: オープン=ブローカー01:00（冬は 23:00 UTC ちょうど・夏は 22:01-22:06 UTC＝01:01-06）、
#   クローズ=23:14（夏 20:14 UTC / 冬 21:14 UTC＝ともに同一ブローカー分）。UTC 日切り時代（旧 61..1438）と
#   異なり夏冬で窓が揺れない（季節安定）。DST 切替日（年2回・23h/25h セッション）のみ経過分と壁時計分が
#   ±60 分ずれる近似を許容する（mod は経過分＝壁時計変換の per-tick tz コストを避ける・設計判断）。
#   旧 analysis/mp_stats（UTC 窓）とは窓が意図的に異なる（math パリティは test_zp_step5_parity が
#   ブラケット規則・モーメント計算の同一性で担保する）。
SESSION_OPEN_MOD = 60      # ブローカー 01:00（セッション始端から 60 分）
SESSION_CLOSE_MOD = 1394   # ブローカー 23:14（最終取引可能分・夏冬同値）
BRACKET_BASE_MOD = 60      # ブラケット起点 ブローカー 01:00
BRACKET_MIN = 30
G_MINUTES = SESSION_CLOSE_MOD - SESSION_OPEN_MOD + 1                    # 1335
K_BRACKETS = (SESSION_CLOSE_MOD - BRACKET_BASE_MOD) // BRACKET_MIN + 1  # 45

# 帰無パラメータ（因果・決定論）。
NULL_HIST_DAYS = 250   # ステップ行列のソース窓（当日から遡る完了日数）
NULL_MIN_DAYS = 60     # これ未満は z 未定義（rollup None 扱い）
M_REPS_DAY = 2000      # 完了日 null の反復数（ディスクキャッシュされ一度きり）
M_REPS_LIVE = 1000     # 当日/部分日の都度計算用
CHUNK = 2000           # サロゲートのチャンク幅（step5 と同値・パリティの rng 消費順一致に必要）

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
    """観測の行占有分数 N_obs(k)（(khi-klo+1,)）。k = floor(close/GRID_W) を [klo,khi] へ clip。

    col_lo/col_hi で部分日（当日 forming・replay 境界日）のカラム範囲に限定できる。
    観測 close は集計元の日レンジ内に自然に収まるため clip は端数保護のみ。
    """
    c = np.asarray(closes, dtype=float)
    c = c[col_lo : (None if col_hi is None else col_hi)]
    k = np.clip(np.floor(c / GRID_W).astype(np.int64) - int(klo), 0, int(khi) - int(klo))
    return np.bincount(k, minlength=int(khi) - int(klo) + 1).astype(float)


# --------------------------------------------------------------------------- #
# ステップ行列と Null B モーメント（絶対グリッド版）
# --------------------------------------------------------------------------- #
def build_step_matrix(mgrids: "np.ndarray", opens: "np.ndarray") -> "np.ndarray":
    """(L, G) の分ステップ行列 S。S[:,0]=ln(grid[:,0]/open)、以降は隣接 log 差。

    analysis/mp_stats/step5_null_b.build_step_matrix と同式（入力が SessionData でなく
    mgrid/open 配列な点のみ違う）。
    """
    lg = np.log(np.asarray(mgrids, dtype=float))
    S = np.empty_like(lg)
    S[:, 0] = lg[:, 0] - np.log(np.asarray(opens, dtype=float))
    S[:, 1:] = np.diff(lg, axis=1)
    return S


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
    col = np.arange(G)[None, :]
    done = 0
    while done < m_reps:
        m = min(CHUNK, m_reps - done)
        days = rng.integers(0, L, size=(m, K_BRACKETS))
        s_surr = S[days[:, _B_OF_MINUTE], col]
        prices = np.exp(log_open + np.cumsum(s_surr, axis=1))[:, lo:hi]
        idx = np.floor(prices / GRID_W).astype(np.int64) - int(klo)
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
    col = np.arange(G)[None, :]
    done = 0
    while done < m_reps:
        m = min(CHUNK, m_reps - done)
        days = rng.integers(0, L, size=(m, K_BRACKETS))
        s_surr = S[days[:, _B_OF_MINUTE], col]
        prices = np.exp(log_open + np.cumsum(s_surr, axis=1))
        idx_all = np.floor(prices / GRID_W).astype(np.int64) - int(klo)
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
# ディスク永続キャッシュ（ZpStore）とプロセス内キャッシュ
# --------------------------------------------------------------------------- #
_ZP_CACHE_VERSION = 2  # v2: セッション日切り（ISSUE-078）＝旧 UTC 日 mgrid/znull を全無効化。
_ZP_CACHE_ROOT: "_Path | None" = None  # None=既定(DATA_DIR/cache/market_profile_zp)。テストは tmp を注入。

_STORE = ZpStore(
    root_provider=lambda: _ZP_CACHE_ROOT,
    default_root_provider=lambda: _paths.DATA_DIR / "cache" / "market_profile_zp",
    grid_w=GRID_W,
    hist_days=NULL_HIST_DAYS,
    m_reps=M_REPS_DAY,
    cache_version_provider=lambda: _ZP_CACHE_VERSION,
    day_parquet_files=lambda *a, **k: day_parquet_files(*a, **k),
)
_CACHE_MISS = ZpStore.CACHE_MISS

# プロセス内キャッシュ。完了日のみメモ化（Y2a・dwell と同規約）。
_MGRID_CACHE: "dict[tuple[str, int], tuple[np.ndarray, float] | None]" = {}
_NULL_CACHE: "dict[tuple[str, int], dict | None]" = {}
# 当日/部分窓の都度計算メモ（同一クエリ内・同一分内の再計算吸収。キーに範囲・経過分を含む）。
_LIVE_CACHE: "dict[tuple, dict | None]" = {}
_LIVE_CACHE_MAX = 32
# 直近 S（ステップ行列）の単一エントリメモ（当日 refresh の連続呼び出し用）。
_S_CACHE: "dict[str, tuple[int, np.ndarray]]" = {}


def _reset_caches() -> None:
    """プロセス内キャッシュを全消去する（テスト隔離・データ更新時の明示無効化用）。"""
    _MGRID_CACHE.clear()
    _NULL_CACHE.clear()
    _LIVE_CACHE.clear()
    _S_CACHE.clear()


def _live_memo_put(key: tuple, value: "dict | None") -> None:
    if len(_LIVE_CACHE) >= _LIVE_CACHE_MAX:
        _LIVE_CACHE.pop(next(iter(_LIVE_CACHE)))
    _LIVE_CACHE[key] = value


# --------------------------------------------------------------------------- #
# 日別 mgrid（メモリ → ディスク → 計算）
# --------------------------------------------------------------------------- #
def _mgrid_of_day(symbol: str, day_start: int, now: float) -> "tuple[np.ndarray, float] | None":
    """完了日は メモリ→ディスク→計算(+保存)、当日は都度計算（Y2a・dwell _day_rollup と同規約）。"""
    key = (symbol, int(day_start))
    if key in _MGRID_CACHE:
        return _MGRID_CACHE[key]
    day_end = next_session_day_start(int(day_start))  # ISSUE-078: DST 切替日は 23h/25h。
    completed = day_end <= now
    path = _STORE.mgrid_path(symbol, int(day_start))
    cur_sig = _STORE.day_source_signature(symbol, int(day_start)) if completed else ""
    if completed:
        disk, cached_sig = _STORE.load_mgrid(path)
        if disk is not _CACHE_MISS and cached_sig == cur_sig:
            if disk is not None:
                _MGRID_CACHE[key] = disk  # 非空のみメモ化（stale-empty はディスク署名照合に委ねる）。
            return disk
    secs, mids = _mpd._load_window_ticks(symbol, day_start, day_end)
    grid = minute_close_grid(secs, mids, day_start)
    if completed:
        if grid is not None:
            _MGRID_CACHE[key] = grid
        try:
            _STORE.save_mgrid(path, grid, cur_sig)
        except Exception:
            pass
    return grid


def _hist_step_matrix(symbol: str, day_start: int, now: float) -> "np.ndarray | None":
    """day_start より前の直近 NULL_HIST_DAYS 完了日から S を作る（因果・ルックアヘッドなし）。

    暦日を降順に走査し、mgrid が得られた完了日を NULL_HIST_DAYS 日ぶん集める（休場・欠測は
    スキップ。走査上限は暦 NULL_HIST_DAYS*2+30 日）。NULL_MIN_DAYS 未満は None（z 未定義）。
    直近呼び出しの S は symbol 単位で単一エントリメモ（当日 refresh の連続呼び出し用）。
    """
    cached = _S_CACHE.get(symbol)
    if cached is not None and cached[0] == int(day_start):
        return cached[1]
    grids: "list[np.ndarray]" = []
    opens: "list[float]" = []
    day = session_day_start(int(day_start) - 1)  # 直前セッション（ISSUE-078）。
    scanned = 0
    max_scan = NULL_HIST_DAYS * 2 + 30
    while len(grids) < NULL_HIST_DAYS and scanned < max_scan:
        if next_session_day_start(day) <= now:  # 完了セッションのみ帰無ソースにする。
            g = _mgrid_of_day(symbol, day, now)
            if g is not None:
                grids.append(g[0])
                opens.append(g[1])
        day = session_day_start(day - 1)
        scanned += 1
    if len(grids) < NULL_MIN_DAYS:
        return None
    # 走査は降順 → 時系列昇順に並べ替え（S の行順は帰無サンプリング上は無順序だが決定論のため固定）。
    S = build_step_matrix(np.asarray(grids[::-1]), np.asarray(opens[::-1]))
    _S_CACHE[symbol] = (int(day_start), S)
    return S


# --------------------------------------------------------------------------- #
# 日別 z ロールアップ（観測占有＋Null B モーメント）
# --------------------------------------------------------------------------- #
def _zp_day_rollup(symbol: str, day_start: int, now: float) -> "dict | None":
    """1 カレンダー日の ``{kmin, obs[], mean[], var[]}``。

    探索順: メモリ → ディスク（完了日のみ・署名照合） → 計算（完了日なら保存）。
    当日は経過分（col_hi）までに観測・帰無とも限定し、M_REPS_LIVE で都度計算する
    （帰無を全日分で評価すると mean が膨らみ z が負へ系統偏向するため）。
    """
    key = (symbol, int(day_start))
    completed = next_session_day_start(int(day_start)) <= now  # ISSUE-078。
    if completed and key in _NULL_CACHE:
        return _NULL_CACHE[key]
    path = _STORE.null_path(symbol, int(day_start))
    cur_sig = _STORE.day_source_signature(symbol, int(day_start)) if completed else ""
    if completed:
        disk, cached_sig = _STORE.load_null(path)
        if disk is not _CACHE_MISS and cached_sig == cur_sig:
            if disk is not None:
                _NULL_CACHE[key] = disk
            return disk

    grid = _mgrid_of_day(symbol, day_start, now)
    if grid is None:
        if completed:
            try:
                _STORE.save_null(path, None, cur_sig)
            except Exception:
                pass
        return None
    closes, open_d = grid
    if completed:
        col_hi = G_MINUTES
        m_reps = M_REPS_DAY
    else:
        elapsed = int((now - int(day_start)) // 60) - SESSION_OPEN_MOD + 1
        col_hi = max(1, min(G_MINUTES, elapsed))
        m_reps = M_REPS_LIVE
        live_key = (symbol, int(day_start), col_hi)
        if live_key in _LIVE_CACHE:
            return _LIVE_CACHE[live_key]
    obs_closes = closes[:col_hi]
    klo = int(np.floor(float(obs_closes.min()) / GRID_W))
    khi = int(np.floor(float(obs_closes.max()) / GRID_W))
    S = _hist_step_matrix(symbol, day_start, now)
    if S is None:
        roll = None
    else:
        rng = np.random.default_rng(day_seed(symbol, int(day_start)))
        mean, var = null_b_moments_abs(
            S, open_d, klo, khi, rng=rng, m_reps=m_reps, col_hi=col_hi
        )
        obs = obs_cell_counts(closes, klo, khi, col_hi=col_hi)
        roll = {"kmin": klo, "obs": obs, "mean": mean, "var": var}
    if completed:
        if roll is not None:
            _NULL_CACHE[key] = roll
        try:
            _STORE.save_null(path, roll, cur_sig)
        except Exception:
            pass
    else:
        _live_memo_put((symbol, int(day_start), col_hi), roll)
    return roll


def _zp_partial_rollup(symbol: str, lo: int, hi: int, now: float) -> "dict | None":
    """境界日（サブ日窓 ``[lo, hi)``）の部分 z ロールアップ（replay の from/to 途中日用）。

    観測・帰無ともカラム範囲 [col_lo, col_hi) に限定して評価する。完了窓（hi <= now）のみ
    メモ化（dwell _partial_rollup と同規約・ディスクには載せない＝キー空間が広いため）。
    """
    key = ("partial", symbol, int(lo), int(hi))
    if key in _LIVE_CACHE:
        return _LIVE_CACHE[key]
    day_start = session_day_start(int(lo))  # ISSUE-078: 属セッションの始端。
    grid = _mgrid_of_day(symbol, day_start, now)
    roll: "dict | None" = None
    if grid is not None:
        closes, open_d = grid
        col_lo = max(0, (int(lo) - day_start) // 60 - SESSION_OPEN_MOD)
        col_hi_t = min(G_MINUTES, (int(hi) - day_start) // 60 - SESSION_OPEN_MOD)
        # 当日はさらに経過分まで（未来分の ffill 幻影滞在を数えない）。
        if next_session_day_start(day_start) > now:  # ISSUE-078。
            elapsed = int((now - day_start) // 60) - SESSION_OPEN_MOD + 1
            col_hi_t = min(col_hi_t, max(1, elapsed))
        if col_hi_t > col_lo:
            seg = closes[col_lo:col_hi_t]
            klo = int(np.floor(float(seg.min()) / GRID_W))
            khi = int(np.floor(float(seg.max()) / GRID_W))
            S = _hist_step_matrix(symbol, day_start, now)
            if S is not None:
                rng = np.random.default_rng(day_seed(symbol, day_start) ^ 0x5A5A5A5A)
                mean, var = null_b_moments_abs(
                    S, open_d, klo, khi, rng=rng, m_reps=M_REPS_LIVE,
                    col_lo=col_lo, col_hi=col_hi_t,
                )
                obs = obs_cell_counts(closes, klo, khi, col_lo=col_lo, col_hi=col_hi_t)
                roll = {"kmin": klo, "obs": obs, "mean": mean, "var": var}
    if int(hi) <= now:
        _live_memo_put(key, roll)
    return roll


# --------------------------------------------------------------------------- #
# 公開 API: 窓合算 → fine z → 表示 bin 再集約
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
    """fine 解像度の POC* = argmax z（タイは窓中間値へ最も近いセル・step5 規約）。"""
    zmax = float(z.max()) if z.size else 0.0
    cand = np.flatnonzero(z == zmax)
    centers = (kw0 + cand + 0.5) * GRID_W
    return float(centers[np.argmin(np.abs(centers - mid_price))])


def compute_zp_profile(
    symbol: str,
    t0: Any,
    t1: Any,
    price_min: Any,
    price_max: Any,
    n_bins: int,
    va_pct: float = 0.70,
    bar_sec: int = 86400,
    now: float | None = None,
    want_today: bool = False,
    want_sessions: bool = False,
) -> dict:
    """超過占有 z(p) プロファイル（candle/dwell 版と同一スキーマ・値の意味のみ z）。

    実期間 ``[t0, t1+bar_sec)`` を日単位に走査し、日別 ``{obs, mean, var}`` を
    :func:`_zp_day_rollup`（完全日）/ :func:`_zp_partial_rollup`（境界日）で得て
    obs_sum/mean_sum/var_sum を GRID_W fine grid へ加算する。
    窓 z: z_win(k) = (Σobs − Σmean)/√(Σvar)（日間・セル間とも独立近似。サロゲート内の
    セル計数は総分数制約で負相関優勢のため √Σvar は sd の過大評価側＝z は 0 側へ保守的）。

    応答: bins[{price, tpo=round(z_b,2), norm=clip(z_b,0)/z_max_disp}], poc=POC*（fine argmax z）、
    va_low/va_high=clip(z,0) への _value_area、tpo_units=総観測分数。additive: z_max, poc_star。
    帰無未定義（履歴 NULL_MIN_DAYS 未満）や実データ無しの日は寄与ゼロでスキップする。
    """
    now_val = _time.time() if now is None else float(now)
    price_min = float(price_min)
    price_max = float(price_max)
    if price_max <= price_min:
        price_max = price_min + 1.0
    n_bins = max(1, int(n_bins))

    edges = np.linspace(price_min, price_max, n_bins + 1)
    centers = (edges[:-1] + edges[1:]) / 2.0
    binw = (price_max - price_min) / n_bins

    win_to = int(t1) + int(bar_sec)
    win_from = int(t0)

    kw0 = int(np.floor(price_min / GRID_W))
    size = int(np.floor(price_max / GRID_W)) - kw0 + 1
    obs_sum = np.zeros(max(size, 1))
    mean_sum = np.zeros(max(size, 1))
    var_sum = np.zeros(max(size, 1))
    last_z_disp: "np.ndarray | None" = None
    last_poc: float = float(centers[0])
    sessions: "list[dict]" = []

    def _accumulate(dst: "np.ndarray", arr: "np.ndarray", kmin: int) -> None:
        off = kmin - kw0
        lo = max(0, off)
        hi = min(size, off + len(arr))
        if hi > lo:
            dst[lo:hi] += arr[(lo - off):(hi - off)]

    day = session_day_start(win_from)
    while day < win_to:
        day_end_w = next_session_day_start(day)
        lo_t = max(day, win_from)
        hi_t = min(day_end_w, win_to)
        if lo_t < hi_t:
            if lo_t == day and hi_t == day_end_w:
                roll = _zp_day_rollup(symbol, day, now_val)
            else:
                roll = _zp_partial_rollup(symbol, lo_t, hi_t, now_val)
            if roll is not None:
                _accumulate(obs_sum, roll["obs"], roll["kmin"])
                _accumulate(mean_sum, roll["mean"], roll["kmin"])
                _accumulate(var_sum, roll["var"], roll["kmin"])
                if want_sessions or want_today:
                    z_day = _fine_z(roll["obs"], roll["mean"], roll["var"])
                    fine_day = np.zeros(max(size, 1))
                    _accumulate(fine_day, z_day, roll["kmin"])
                    disp_day = np.zeros(n_bins)
                    cd = (kw0 + np.arange(size) + 0.5) * GRID_W
                    dd = np.clip(((cd - price_min) / binw).astype(int), 0, n_bins - 1)
                    np.add.at(disp_day, dd, fine_day[:size])
                    poc_day = _poc_star_from_fine(
                        z_day, roll["kmin"], (price_min + price_max) / 2.0
                    )
                    last_z_disp = disp_day
                    last_poc = poc_day
                    if want_sessions:
                        ds = session_date_label(day)
                        sessions.append(
                            _session_entry_zp(ds, disp_day, poc_day, centers, va_pct)
                        )
        day = day_end_w

    z_fine = _fine_z(obs_sum[:size], mean_sum[:size], var_sum[:size])
    # fine → 表示 bin（obs/mean/var を bin 集約してから z を取り直す＝独立近似・docstring 参照）。
    centers_fine = (kw0 + np.arange(size) + 0.5) * GRID_W
    disp = np.clip(((centers_fine - price_min) / binw).astype(int), 0, n_bins - 1)
    OBS = np.zeros(n_bins)
    MEAN = np.zeros(n_bins)
    VAR = np.zeros(n_bins)
    np.add.at(OBS, disp, obs_sum[:size])
    np.add.at(MEAN, disp, mean_sum[:size])
    np.add.at(VAR, disp, var_sum[:size])
    z_disp = _fine_z(OBS, MEAN, VAR)

    z_pos = np.maximum(z_disp, 0.0)
    z_max_disp = float(z_pos.max()) if z_pos.max() > 0 else 1.0
    poc_star = _poc_star_from_fine(z_fine, kw0, (price_min + price_max) / 2.0)
    va_low, va_high = _value_area(z_pos, centers, va_pct)

    bins = [
        {
            "price": round(float(centers[i]), 2),
            "tpo": round(float(z_disp[i]), 2),
            "norm": round(float(z_pos[i]) / z_max_disp, 4),
        }
        for i in range(n_bins)
    ]
    out = {
        "bins": bins,
        "poc": round(poc_star, 2),
        "va_low": round(float(va_low), 2),
        "va_high": round(float(va_high), 2),
        "price_min": price_min,
        "price_max": price_max,
        "tpo_units": int(round(float(obs_sum.sum()))),
        "n_bins": n_bins,
        # additive（既存 8 キーの後・フロントは任意参照）
        "z_max": round(float(z_fine.max()) if z_fine.size else 0.0, 2),
        "poc_star": round(poc_star, 2),
    }
    if want_today:
        today = last_z_disp if last_z_disp is not None else np.zeros(n_bins)
        tpos = np.maximum(today, 0.0)
        out["today"] = [round(float(v), 3) for v in today]
        out["today_max"] = float(tpos.max()) if tpos.max() > 0 else 1.0
    if want_sessions:
        out["sessions"] = sessions
    return out


# --------------------------------------------------------------------------- #
# ウォーマー（事前ビルド）: 完了日の mgrid + znull をディスクへ一括構築（冪等）
# --------------------------------------------------------------------------- #
def warm_zp_cache(
    symbol: str, start: Any = None, end: Any = None, now: float | None = None
) -> dict:
    """全 or 指定期間の完了日 z 成果物（mgrid＋znull）をディスクへ一括構築する（冪等・進捗 print）。

    日付昇順に走査し、各完了日の mgrid → znull を構築・保存する。既にディスクにある完了日は
    スキップ（冪等）。ステップ行列 S は _hist_step_matrix 経由（mgrid ディスクヒットで高速）。
    """
    now_val = _time.time() if now is None else float(now)
    lo = pd.Timestamp("2000-01-01") if start is None else pd.Timestamp(start)
    hi = pd.Timestamp(now_val, unit="s").normalize() if end is None else pd.Timestamp(end)
    files = day_parquet_files(lo, hi, symbol=symbol)
    built = skipped = 0
    # ISSUE-078: 実在 parquet（UTC 日）から被覆セッション日集合を導出（dwell warm と同規則）。
    session_days = sorted({session_day_start(_mpd._day_start_from_tick_path(p)) for p in files}
                          | {session_day_start(_mpd._day_start_from_tick_path(p) + 86399) for p in files})
    for day_start in session_days:
        if next_session_day_start(day_start) > now_val:
            continue
        if _STORE.null_path(symbol, day_start).is_file():
            skipped += 1
            continue
        _zp_day_rollup(symbol, day_start, now_val)
        built += 1
        if built % 25 == 0:
            print(f"[warm-zp] {symbol}: {built} built / {skipped} skipped ...")
    print(f"[warm-zp] {symbol}: done — {built} built, {skipped} skipped, {len(files)} days enumerated")
    return {"built": built, "skipped": skipped, "days": len(files)}


if __name__ == "__main__":  # 例: python -m market_profile_api.compute.market_profile_zp --warm jp225_tick
    import argparse

    _parser = argparse.ArgumentParser(description="Market Profile z(p) 日別成果物のディスクキャッシュ・ウォーマー")
    _parser.add_argument("--warm", metavar="REF_OR_SYMBOL", required=True)
    _parser.add_argument("--start", default=None)
    _parser.add_argument("--end", default=None)
    _args = _parser.parse_args()
    _sym = _mpd.resolve_symbol(_args.warm) or _args.warm
    print(f"[warm-zp] cache root = {_STORE.cache_root()}")
    warm_zp_cache(_sym, start=_args.start, end=_args.end)
