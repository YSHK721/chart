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
import math as _math
import zlib as _zlib
from pathlib import Path as _Path
from typing import Any

import numpy as np
import pandas as pd

# bp 相対格子（ISSUE-079 単位②・依頼者承認 2026-07-15）: zp の内部格子を絶対 pt（GRID_W）から
# 「価格比 1bp の log 一様格子」へ再定義する。セル index k = floor(ln(price) / W_LOG)、
# W_LOG = ln(1+ZP_BP/1e4)。log 空間の絶対一様格子＝跨日 Σobs/Σmean/Σvar の窓合算は従来と同型。
# 校正スキャン（analysis/zp_grid_scan.md）実測: FPR(z>=3) は細分化で膨張せず（現行10pt≈2.2% 基準・
# 0.5bp まで合格）、1bp は 15m 周期（原子15分）で意味の立つ最細（約1.5分/セル）。絶対格子の時代
# ドリフト（分/セル: 直近8 vs 2013年17.5）を解消する。dwell の内部格子（GRID_W）は対象外（不変）。
# tick ローダ・symbol 解決は dwell 側の単一定義を再利用する。
# _load_window_ticks は call-time 参照（_mpd._load_window_ticks(...)）＝既存テストの
# monkeypatch 単一注入点を温存する。
from market_profile_api.compute import market_profile_dwell as _mpd
from market_profile_api.compute import null_b_kernel as _null_b  # ISSUE-094 🔴-2: 帰無サロゲート純カーネル（step5 と共有）
from market_profile_api.compute.market_profile import _value_area
from market_profile_api.compute.market_profile_dwell import GRID_W  # noqa: F401  (dwell 互換・zp 内部では不使用)
from market_profile_api.gateway.zp_store import ZpStore  # ISSUE-092 ④: gateway 移設（旧 compute パスは互換シム）

# ISSUE-091 🔴-2: ティック物理格納への依存は compute 所有の TickStorePort へ逆転（dwell と同規律）。
from market_profile_api.compute.tick_store_port import tick_store as _tick_store
# セッション日境界（ISSUE-078・NY17:00 ET 基準）。日切り・完了判定・ラベルの唯一の規則源
#（marketdata の純業務規則＝I/O 非依存のため内側 import を許容）。
from marketdata.session_day import (  # noqa: E402
    next_session_day_start,
    session_date_label,
    session_day_start,
)

# ISSUE-133（SRP）: 統計コア（純数学）は market_profile_zp_kernel へ分離した。本モジュール
# （キャッシュ協調）は全公開シンボルを再エクスポートし、呼出面（``zp.minute_close_grid`` /
# ``zp.null_b_moments_abs`` / ``zp.W_LOG`` 等）と数値を完全に温存する（cache 協調関数は下段で
# これらを bare name で呼ぶ＝再エクスポート先が zp 名前空間に載るため既存挙動不変）。
from market_profile_api.compute.market_profile_zp_kernel import (  # noqa: E402,F401
    BRACKET_BASE_MOD,
    BRACKET_MIN,
    CHUNK,
    G_MINUTES,
    K_BRACKETS,
    SESSION_CLOSE_MOD,
    SESSION_OPEN_MOD,
    W_LOG,
    ZP_BP,
    _B_OF_MINUTE,
    _fine_z,
    _poc_star_from_fine,
    _session_entry_zp,
    build_step_matrix,
    day_seed,
    minute_close_grid,
    null_b_moments_abs,
    null_b_period_moments,
    obs_cell_counts,
)


def day_parquet_files(lo_day, hi_day, *, symbol: str):
    """正準ティック日別ファイルの列挙（TickStorePort へ委譲・read-only）。

    既存テストの monkeypatch 単一注入点（``zp.day_parquet_files``）を module 属性として温存する。
    """
    return _tick_store().day_files(lo_day, hi_day, symbol=symbol)

# 帰無パラメータ（因果・決定論・キャッシュ協調 アクター所有＝serving 時に monkeypatch される）。
NULL_HIST_DAYS = 250   # ステップ行列のソース窓（当日から遡る完了日数）
NULL_MIN_DAYS = 60     # これ未満は z 未定義（rollup None 扱い）
M_REPS_DAY = 2000      # 完了日 null の反復数（ディスクキャッシュされ一度きり）
M_REPS_LIVE = 1000     # 当日/部分日の都度計算用
# セッション窓定数（SESSION_OPEN_MOD 等）・内部格子（ZP_BP/W_LOG）・CHUNK・_B_OF_MINUTE、および
# 純数学関数（day_seed / minute_close_grid / obs_cell_counts / build_step_matrix / null_b_moments_abs /
# null_b_period_moments / _fine_z / _poc_star_from_fine / _session_entry_zp）は market_profile_zp_kernel
# から再エクスポート済み（ISSUE-133 SRP・冒頭 import 参照）。下段のキャッシュ協調関数は bare name で呼ぶ。


# --------------------------------------------------------------------------- #
# ディスク永続キャッシュ（ZpStore）とプロセス内キャッシュ
# --------------------------------------------------------------------------- #
_ZP_CACHE_VERSION = 3  # v3: bp 相対 log 格子（ISSUE-079）＝znull を全無効化（mgrid は格子非依存で温存）。
_ZP_CACHE_ROOT: "_Path | None" = None  # None=既定(DATA_DIR/cache/market_profile_zp)。テストは tmp を注入。

_STORE = ZpStore(
    root_provider=lambda: _ZP_CACHE_ROOT,
    default_root_provider=lambda: _tick_store().data_dir() / "cache" / "market_profile_zp",
    grid_w=ZP_BP,  # ISSUE-079: znull パスの格子タグは bp 値（b1 等・旧 g10 と不混在）。
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
    # ISSUE-128: now（as-of）より未来に始まるセッション日は観測ゼロ＝寄与なし。ガードしないと
    #   下の max(1, elapsed) の下限 1 が「未来日の最初の 1 分」を混入させる（as-of 因果違反）。
    if now < int(day_start):
        return None
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
    klo = int(np.floor(np.log(float(obs_closes.min())) / W_LOG))
    khi = int(np.floor(np.log(float(obs_closes.max())) / W_LOG))
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
    # ISSUE-127: メモは「完了窓（hi<=now）」でのみ有効。完了窓の roll は now 非依存（経過分クランプが
    #   効かない）ため安全に共有できるが、未完了窓（as-of 部分・now<hi）は now ごとに結果が異なるため
    #   キャッシュを読まず都度計算する（ライブ当日と同一規約）。読み出しゲートが無いと、実時計要求が
    #   メモ化した全日 roll を以後の asof 要求が同 (lo,hi) キーで受け取り、全日確定形へ化ける（毒）。
    if int(hi) <= now and key in _LIVE_CACHE:
        return _LIVE_CACHE[key]
    day_start = session_day_start(int(lo))  # ISSUE-078: 属セッションの始端。
    # ISSUE-128: now より未来に始まるセッション日は観測ゼロ＝寄与なし（_zp_day_rollup と同一ガード。
    #   max(1, elapsed) の下限 1 による「未来日の最初の 1 分」混入を遮断する）。
    if now < day_start:
        return None
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
            klo = int(np.floor(np.log(float(seg.min())) / W_LOG))
            khi = int(np.floor(np.log(float(seg.max())) / W_LOG))
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
#   _session_entry_zp / _fine_z / _poc_star_from_fine（純数学）は market_profile_zp_kernel から
#   再エクスポート済み（ISSUE-133 SRP・冒頭 import 参照）。以下 compute_zp_profile は bare name で呼ぶ。
# --------------------------------------------------------------------------- #
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
    # ISSUE-079: log 格子は正の価格が前提。空 candles 経路（controller が 0.0/0.0 を渡す）や
    #   非正レンジは log(0)=-inf で即死するため、正の最小値へクランプする（旧線形格子では
    #   floor(0/GRID_W)=0 で潜伏していた欠陥の顕在化・防御）。
    if not (price_min > 0):
        price_min = 1.0
    if price_max <= price_min:
        price_max = price_min + 1.0
    n_bins = max(1, int(n_bins))

    edges = np.linspace(price_min, price_max, n_bins + 1)
    centers = (edges[:-1] + edges[1:]) / 2.0
    binw = (price_max - price_min) / n_bins

    win_to = int(t1) + int(bar_sec)
    win_from = int(t0)

    kw0 = int(np.floor(np.log(price_min) / W_LOG))
    size = int(np.floor(np.log(price_max) / W_LOG)) - kw0 + 1
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
                    cd = np.exp((kw0 + np.arange(size) + 0.5) * W_LOG)
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
    centers_fine = np.exp((kw0 + np.arange(size) + 0.5) * W_LOG)
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
# ウォーマー（運用バッチ）: 実体は market_profile_zp_warmer へ分離（ISSUE-133 SRP）
# --------------------------------------------------------------------------- #
# 完了日 mgrid＋znull の一括ビルド（運用バッチ アクター）は :mod:`market_profile_zp_warmer` へ移設した。
# 本 module 属性 ``warm_zp_cache`` は既存 import 面を温存する薄い遅延委譲（module ロード時の循環 import を
# 避けるため関数内 import）。CLI は tools/warm_market_profile_cache へ分離した。
def warm_zp_cache(
    symbol: str, start: Any = None, end: Any = None, now: float | None = None
) -> dict:
    """完了日 mgrid＋znull の一括ビルドへの遅延委譲（実体は :mod:`market_profile_zp_warmer`）。"""
    from market_profile_api.compute.market_profile_zp_warmer import warm_zp_cache as _impl

    return _impl(symbol, start=start, end=end, now=now)
