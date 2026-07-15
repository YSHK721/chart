"""market_profile_dwell — 実ティック滞在（真の time-at-price・セッション認識）プロファイル計算。

``src=candle``（足レンジ TPO・:mod:`market_profile`）に対し、本モジュールは ``src=dwell`` を担う。
原子＝「価格帯の実ティック滞在秒」で集計する。応答スキーマ（bins/poc/va_low/va_high/price_min/
price_max/tpo_units/n_bins）は candle 版と同一に保つ（tpo は dwell 秒＝int へ丸め）。

セッション認識（休場自動除外）:
    (曜日×時) のティック密度から活発/休場を判定し、隣接ティック間ギャップのうち「活発な時間帯に
    属する秒」だけを滞在に計上する。これにより週末・日次メンテの休場帯を除外しつつ、取引中の
    静かな滞在は満額残す（試作 prototype_260630-01/mp_core.py が実証したアルゴリズムを本体作法へ移植）。

ディスク永続キャッシュ（全期間高速化・:func:`warm_dwell_cache`）:
    完了日（UTC 確定日）の固定グリッド日別ロールアップを ``DATA_DIR/cache/market_profile_dwell``
    （新規ディレクトリ・読み書きキャッシュ）へ ``.npz`` で永続化し、探索順「メモリ→ディスク→計算」で
    全期間でも初回ウォーム後は高速ロードする。既存の生データ/ticks/CSV は読むだけで触らない。
    caveat（dwell のセッション地図依存）: 永続化される ``dwell[]`` はビルド時の active table（直近
    :data:`_ACTIVE_TABLE_DAYS` 日から作る曜日×時のセッション地図）に依存する。地図は曜日×時で安定だが、
    地図を変えたい/変わった場合はキャッシュを破棄（cache ディレクトリ削除→再ウォーム）すること。
    ``cnt[]``（metric='count'/src=m1）はセッション地図に非依存で常に正しい。

perf（単一スレッド常駐サーバ保護）:
    - 集計窓は**全期間**（旧 ``_MAX_DWELL_DAYS`` によるサブ窓限定は撤廃）。全期間でも上記ディスク
      永続キャッシュにより初回ウォーム後は各完了日 O(1) ロードで数秒オーダー。**コールド（ウォーム未実行）
      時のみ** per-day parquet 逐次読込で日数比例に重く単一スレッドを占有するため、本番有効化前に
      :func:`warm_dwell_cache` を 1 回実行してキャッシュを構築しておくこと（運用手順）。
    - 固定グリッド日別ロールアップをメモリキャッシュし、同一プロセスの 2 回目以降を高速化する（走査した
      過去日ぶんが ``_DAY_CACHE`` / ``_PARTIAL_CACHE`` に累積。各エントリは小配列でメモリは緩く有界。
      現在進行中の当日は Y2a によりキャッシュせず都度計算する）。active table はプロセス内で 1 回だけ
      構築しキャッシュする。

依存方向: 本モジュールは numpy + pandas + :mod:`marketdata.tick_m1`（正準ティック経路・read-only）に
のみ依存し、:mod:`market_profile` の ``_value_area``（POC/VA の単一定義）を import して再利用する（DRY）。
marketdata は import して使うだけ（既存データは読むだけ・波及させない）。
"""

from __future__ import annotations

import sys as _sys
import time as _time
from pathlib import Path as _Path
from typing import Any

import numpy as np
import pandas as pd

# POC/VA は candle 版の単一定義を再利用する（DRY・同一定義）。
from market_profile_api.compute.market_profile import _session_entry, _value_area
# ディスクキャッシュ Repository（ISSUE-040(b) SRP 分離）。集計数学は本モジュール、永続化は Store が担う。
from market_profile_api.compute.market_profile_dwell_store import DwellRollupStore

# ISSUE-087 🟡-3: repo 根/MP api の解決は venv の .pth（tools/install_dev_paths.py）が担う（実行時 sys.path 改変を撤去）。
from marketdata import paths as _paths  # noqa: E402  (DATA_DIR 単一基点・cache 配置に使用)
from marketdata.tick_m1 import day_parquet_files  # noqa: E402  (正準ティック経路・read-only)
# セッション日境界（ISSUE-078・NY17:00 ET 基準）。日切り・完了判定・ラベルの唯一の規則源。
from marketdata.session_day import (  # noqa: E402
    next_session_day_start,
    session_date_label,
    session_day_start,
)

# datasetRef → 実ティック symbol 解決（forming_bar.TICK_REFS と整合。'jp225_tick'→'JP225'）。
TICK_REF_SYMBOLS: dict[str, str] = {"jp225_tick": "JP225"}

# セッション認識 dwell のパラメータ（試作と一致）。
_ACTIVE_FRAC = 0.10   # (曜日×時) のティック数が ピーク×この割合 未満なら「休場」とみなす。
GRID_W = 10.0         # 固定価格グリッド幅(pt)。日別集計→窓合算→表示 bin へ再集計する中間解像度。

# 全期間化（250日キャップ撤廃）。完了日はディスク/メモリキャッシュ経由で O(1) ロードされるため、
# 集計窓を直近日数に切り詰めず ``[t0, t1+bar_sec)`` の全日を集計する（初回ウォーム後は高速）。
# ``MAX_DWELL_DAYS`` は後方互換のため定数として残すが、compute_dwell_profile は窓クランプに使用しない。
MAX_DWELL_DAYS = 250      # （後方互換・非使用）かつて集計窓を直近ぶんに限定していた上限。
_MAX_DWELL_DAYS = MAX_DWELL_DAYS  # 後方互換の別名。
_ACTIVE_TABLE_DAYS = 120  # active table 構築に用いる直近日数（試作と同じ・一度だけ構築）。

# ディスク永続キャッシュ（日別ロールアップ）。既存の生データ/ticks/CSV は触らず、新規 cache
# ディレクトリのみに読み書きする。完了日（UTC 確定日）のみ永続化し、当日（未確定）は都度計算する。
_CACHE_VERSION = 3        # 形式バージョン。読込時に不一致なら無視して再計算（fail-safe）。
#   v3: セッション日切り（ISSUE-078・NY17:00 ET 基準）。日キーが UTC 深夜→セッション始端へ変わるため
#       旧 UTC 日ロールアップ（v2）を全無効化する。
#   v2: 日次ロールアップに「ソースティック署名(sig)」を併記。完了日を空でキャッシュした後にティックが
#       届いても署名変化で自動再計算する（無効化ロジック・stale-empty 修正）。v1 は不一致で全再計算。
_CACHE_ROOT: "_Path | None" = None  # None=既定(DATA_DIR/cache/market_profile_dwell)。テストは tmp を注入。

# ディスクキャッシュ Repository（ISSUE-040(b)）。永続化 I/O（save/load/署名/無効化・parquet/tempfile）は
# DwellRollupStore に分離した。可変設定（cache root / 形式バージョン / 正準ティック列挙）は provider の
# クロージャで注入し、本体 module 変数 _CACHE_ROOT / _CACHE_VERSION / day_parquet_files の **テスト注入
# （monkeypatch）経路を温存**する（call-time に読むため）。集計数学は本モジュールに残す（下段）。
_STORE = DwellRollupStore(
    root_provider=lambda: _CACHE_ROOT,
    default_root_provider=lambda: _paths.DATA_DIR / "cache" / "market_profile_dwell",
    grid_w=GRID_W,
    cache_version_provider=lambda: _CACHE_VERSION,
    day_parquet_files=lambda *a, **k: day_parquet_files(*a, **k),
)
_CACHE_MISS = DwellRollupStore.CACHE_MISS  # 番兵は Store と同一オブジェクト（identity 一致を保つ）。

# 生ティック parquet の必須列（marketdata.tick_m1._TICK_COLUMNS と同じ意味）。
_TICK_COLUMNS = ["timestamp", "bidPrice", "askPrice"]
_OUTLIER_FRAC = 0.30      # 窓内 mid 中央値 ±30% の外れ値除去（tick_window と同基準）。

# プロセス内キャッシュ（AB 兼用・perf）。走査した過去日ぶんが累積する（各エントリは小配列＝緩く有界）。
# 完了した過去日/窓のみ登録し、現在進行中の当日はキャッシュしない（Y2a・_day_rollup/_partial_rollup 参照）。
_DAY_CACHE: dict[tuple[str, int], "dict | None"] = {}      # (symbol, day_start) → rollup or None
_PARTIAL_CACHE: dict[tuple[str, int, int], "dict | None"] = {}  # (symbol, lo, hi) → rollup or None
_ACTIVE_TABLE: dict[str, np.ndarray] = {}                  # symbol → 7×24 bool 活動テーブル

_EMPTY_SECS = np.array([], dtype=np.int64)
_EMPTY_MIDS = np.array([], dtype=np.float64)


def resolve_symbol(ref: Any) -> "str | None":
    """datasetRef を実ティック symbol へ解決する（非 tick ref は None）。"""
    return TICK_REF_SYMBOLS.get(ref)


def get_active_table(symbol: str, now: float | None = None) -> list[list[int]]:
    """symbol の活動テーブル（7 曜日×24 時・True=活発）を list[list[int]] で露出する薄アクセサ。

    内部 :func:`_active_table`（プロセス内 1 回構築・直近 ``_ACTIVE_TABLE_DAYS`` 日から）をそのまま
    list（0/1）へ変換して返す。tick 逐次成長のクライアント側 dwell 積分で使う（活発秒の判定地図）。
    既存 :func:`compute_dwell_profile` の窓生成と同一定義（at_from/win_to）でテーブルを得る（DRY）。
    """
    now_val = _time.time() if now is None else float(now)
    win_to = int(now_val) + 86400
    at_from = win_to - _ACTIVE_TABLE_DAYS * 86400
    table = _active_table(symbol, at_from, win_to)
    return [[int(bool(v)) for v in row] for row in table]


def _reset_caches() -> None:
    """プロセス内キャッシュを全消去する（テスト隔離・データ更新時の明示無効化用）。"""
    _DAY_CACHE.clear()
    _PARTIAL_CACHE.clear()
    _ACTIVE_TABLE.clear()


# --------------------------------------------------------------------------- #
# 窓ティック読込（単一注入点。テストはここを monkeypatch して合成ティックを注入する）
# --------------------------------------------------------------------------- #
def _load_window_ticks(symbol: str, start: Any, end: Any) -> "tuple[np.ndarray, np.ndarray]":
    """``[start, end)`` の実ティックを ``(secs:int64, mids:float64)`` で返す（メモリ有界・時系列順）。

    正準ティック経路 :func:`marketdata.tick_m1.day_parquet_files` で日別 parquet を列挙し、各を
    ``timestamp/bidPrice/askPrice`` 列で読む → concat → tz 除去し UTC 秒 int64 へ → 窓 ``[start,end)``
    マスク → mid=(bid+ask)/2 → 窓内 mid 中央値 ±30% の外れ値除去 → secs で安定ソート。空なら空配列。
    """
    s, e = int(start), int(end)
    lo_day = pd.Timestamp(s, unit="s").normalize()
    hi_day = pd.Timestamp(max(s, e - 1), unit="s").normalize()
    files = day_parquet_files(lo_day, hi_day, symbol=symbol)
    if not files:
        return _EMPTY_SECS, _EMPTY_MIDS
    frames = [pd.read_parquet(p, columns=_TICK_COLUMNS) for p in files]
    tdf = pd.concat(frames, ignore_index=True) if len(frames) > 1 else frames[0]

    ts = pd.to_datetime(tdf["timestamp"])
    if getattr(ts.dt, "tz", None) is not None:
        ts = ts.dt.tz_convert("UTC").dt.tz_localize(None)
    secs = ts.to_numpy().astype("datetime64[s]").astype("int64")
    win = (secs >= s) & (secs < e)
    secs = secs[win]
    mids = ((tdf["bidPrice"].to_numpy(dtype="float64") + tdf["askPrice"].to_numpy(dtype="float64"))
            / 2.0)[win]
    if len(mids):
        m = float(np.median(mids))
        if m > 0:
            keep = np.abs(mids / m - 1.0) <= _OUTLIER_FRAC
            secs, mids = secs[keep], mids[keep]
    order = np.argsort(secs, kind="stable")
    return secs[order].astype(np.int64), mids[order].astype(np.float64)


# --------------------------------------------------------------------------- #
# セッション認識 dwell（活動テーブル + 活発秒の積分）
# --------------------------------------------------------------------------- #
def _build_active_table(secs: np.ndarray) -> np.ndarray:
    """ティックから (曜日0-6 × 時0-23) の活動テーブル（True=活発/False=休場）を作る。

    曜日 = ``((s//86400)+3)%7``（1970-01-01=木を Mon0 基準へ）、時 = ``(s%86400)//3600``。
    バケット別ティック数が ピーク×``_ACTIVE_FRAC`` 以上を活発とする。
    """
    s = np.asarray(secs, dtype=np.int64)
    wd = ((s // 86400) + 3) % 7
    hod = (s % 86400) // 3600
    cnt = np.zeros((7, 24), dtype=np.int64)
    np.add.at(cnt, (wd, hod), 1)
    thr = cnt.max() * _ACTIVE_FRAC
    return cnt >= thr


def _active_seconds_cross(a: int, b: int, table: np.ndarray) -> int:
    """``[a, b)`` のうち活発な (曜日×時) に属する秒数を時間境界で積分する（跨ぎギャップ用）。"""
    total = 0
    t = int(a)
    b = int(b)
    while t < b:
        nb = (t // 3600 + 1) * 3600
        seg = min(nb, b)
        wd = ((t // 86400) + 3) % 7
        if table[wd, (t % 86400) // 3600]:
            total += seg - t
        t = seg
    return total


def _session_dwell(secs: np.ndarray, table: np.ndarray) -> np.ndarray:
    """各隣接ティック間ギャップの「活発秒」を返す（``len = len(secs)-1``）。

    同一時内は活発なら ``gap``/休場なら 0。時境界を跨ぐギャップのみ :func:`_active_seconds_cross`
    で厳密に積分する。dwell[i] はギャップ始端のティック（価格 mids[i]）に帰属する。
    """
    s = np.asarray(secs, dtype=np.int64)
    if s.size < 2:
        return np.zeros(max(s.size - 1, 0), dtype=float)
    start = s[:-1]
    end = s[1:]
    gap = (end - start).astype(float)
    wd = ((start // 86400) + 3) % 7
    hod = (start % 86400) // 3600
    act_start = table[wd, hod]
    same_hour = (start // 3600) == (end // 3600)
    # 同一時内: 活発なら gap、休場なら 0。
    dwell = np.where(same_hour & act_start, gap, 0.0)
    # 時境界を跨ぐギャップのみ厳密に積分（件数は僅少）。
    for i in np.where(~same_hour)[0]:
        dwell[i] = _active_seconds_cross(int(start[i]), int(end[i]), table)
    return dwell


# --------------------------------------------------------------------------- #
# 固定グリッド日別ロールアップ（メモリキャッシュ）
# --------------------------------------------------------------------------- #
def _rollup_ticks(secs: np.ndarray, mids: np.ndarray, table: np.ndarray) -> "dict | None":
    """ティック配列を固定グリッド ``{kmin, dwell[], cnt[]}``（k=floor(mid/GRID_W)）へ集約する。空なら None。

    dwell[]: セッション認識の実ティック滞在秒（休場帯は 0）。metric='dwell'（既定）が使用する。
    cnt[]:   生ティック数（セッションマスク**非適用**＝休場帯もカウント）。metric='count'（src=m1）が使用する。
    """
    if len(secs) == 0:
        return None
    dwell = _session_dwell(secs, table)  # len = len(secs)-1
    k = np.floor(mids / GRID_W).astype(np.int64)
    kmin = int(k.min())
    size = int(k.max()) - kmin + 1
    dwell_arr = np.zeros(size, dtype=float)
    if dwell.size:
        np.add.at(dwell_arr, k[:-1] - kmin, dwell)  # dwell[i] は始端ティック価格 k[i] に帰属。
    cnt_arr = np.zeros(size, dtype=float)
    np.add.at(cnt_arr, k - kmin, 1.0)  # 生ティック数（全ティック・セッション非依存）。
    return {"kmin": kmin, "dwell": dwell_arr, "cnt": cnt_arr}


def _active_table(symbol: str, at_from: int, win_to: int) -> np.ndarray:
    """symbol の活動テーブルをプロセス内で 1 回だけ構築してキャッシュする（直近ぶんから）。"""
    cached = _ACTIVE_TABLE.get(symbol)
    if cached is not None:
        return cached
    secs, _ = _load_window_ticks(symbol, at_from, win_to)
    table = _build_active_table(secs) if len(secs) else np.ones((7, 24), dtype=bool)
    _ACTIVE_TABLE[symbol] = table
    return table


# --------------------------------------------------------------------------- #
# 日別ロールアップのディスク永続キャッシュ委譲（DwellRollupStore・新規 cache ディレクトリのみ・fail-safe）
# --------------------------------------------------------------------------- #
# 以下は :class:`DwellRollupStore`（永続化 Repository）への薄い委譲。公開/内部シンボル名は不変に保ち、
# 既存テストの monkeypatch 経路（`mpd._cache_path` / `_save_day_rollup` / `_load_day_rollup` /
# `_day_source_signature` / `_CACHE_ROOT` / `_CACHE_VERSION`）と byte 出力を温存する（ISSUE-040(b)）。
def _cache_root() -> _Path:
    """ディスクキャッシュの基点 ``DATA_DIR/cache/market_profile_dwell`` を返す（テストは _CACHE_ROOT で差替）。"""
    return _STORE.cache_root()


def _cache_path(symbol: str, day_start: int) -> _Path:
    """日別ロールアップの保存パス ``<root>/<symbol>/g<GRID_W>/<day_start>.npz``（Store へ委譲）。"""
    return _STORE.cache_path(symbol, day_start)


def _day_source_signature(symbol: str, day_start: int) -> str:
    """完了日 ``day_start`` のソースティック署名（無効化用・Store へ委譲）。ファイル無しは空文字。"""
    return _STORE.day_source_signature(symbol, day_start)


def _save_day_rollup(path: _Path, roll: "dict | None", sig: str = "") -> None:
    """ロールアップ（None=実データ無し完了日を含む）を ``.npz`` へ原子的に保存する（Store へ委譲）。"""
    _STORE.save_day_rollup(path, roll, sig)


def _load_day_rollup(path: _Path) -> "tuple[Any, str]":
    """ディスクから日別ロールアップと署名を読む（Store へ委譲）。未ヒット/破損/不整合は ``(_CACHE_MISS, "")``。"""
    return _STORE.load_day_rollup(path)


def _day_rollup(symbol: str, day_start: int, table: np.ndarray, now: float) -> "dict | None":
    """1 セッション日 ``[day_start, next_session_day_start)`` を固定グリッドへ集約する（ISSUE-078）。

    ``day_start`` はセッション日始端（NY17:00 ET＝夏21:00/冬22:00 UTC・session_day が唯一の規則源）。
    探索順: **メモリ → ディスク → 計算(＋完了日ならディスク保存)**。
    Y2a: 完了したセッション（``next_session_day_start(day_start) <= now``）のみキャッシュする。
    進行中の当日セッションはキャッシュせず毎回再計算し、新ティック到着による stale 化を防ぐ。
    """
    key = (symbol, int(day_start))
    if key in _DAY_CACHE:  # メモリ（プロセス内・最速）。**非空のみ**メモ化する（下記）。
        return _DAY_CACHE[key]
    day_end = next_session_day_start(int(day_start))  # DST 切替日は 23h/25h（+86400 固定は不可）。
    completed = day_end <= now  # 完了セッションのみ永続化対象。
    path = _cache_path(symbol, int(day_start))
    cur_sig = _day_source_signature(symbol, int(day_start)) if completed else ""
    if completed:  # ディスク（プロセス跨ぎ・ウォーム済みなら高速）。
        disk, cached_sig = _load_day_rollup(path)
        # ソースティック署名が一致するときのみディスクを信頼する。空でキャッシュした完了日に後から
        #   ティックが届く/更新された場合は署名が変わり再計算する（stale-empty の無効化）。
        if disk is not _CACHE_MISS and cached_sig == cur_sig:
            if disk is not None:
                _DAY_CACHE[key] = disk  # 非空のみメモ化。
            return disk
    secs, mids = _load_window_ticks(symbol, day_start, day_end)  # 計算。
    roll = _rollup_ticks(secs, mids, table)
    if completed:
        # ★空(None)はメモリにメモ化しない。ティック未着で空になった完了日を常駐プロセスがメモ保持すると、
        #   後からティックが届いても line 337 で早期 return し stale-empty が残るため（ディスクは署名照合で
        #   再計算されるので、空日は毎回ディスク照合に委ねる）。非空は従来どおりメモ化して高速維持。
        if roll is not None:
            _DAY_CACHE[key] = roll
        try:
            _save_day_rollup(path, roll, cur_sig)  # 完了日のみ保存（署名併記・保存失敗は次回吸収）。
        except Exception:
            pass
    return roll


def _partial_rollup(symbol: str, lo: int, hi: int, table: np.ndarray, now: float) -> "dict | None":
    """境界日（サブ日足）用の部分集計 ``[lo, hi)`` を固定グリッドへ集約する。

    Y2a: 窓終端が完了した（``hi <= now``）場合のみキャッシュする。当日の部分足（``hi > now``）は
    新ティックで stale 化しうるため毎回再計算する。
    """
    key = (symbol, int(lo), int(hi))
    if key in _PARTIAL_CACHE:
        return _PARTIAL_CACHE[key]
    secs, mids = _load_window_ticks(symbol, lo, hi)
    roll = _rollup_ticks(secs, mids, table)
    if int(hi) <= now:  # 完了した窓のみキャッシュ（未完了の当日部分は都度計算）。
        _PARTIAL_CACHE[key] = roll
    return roll


# --------------------------------------------------------------------------- #
# 公開 API: 窓合算 → 表示 bin 再集計
# --------------------------------------------------------------------------- #
def compute_dwell_profile(
    symbol: str,
    t0: Any,
    t1: Any,
    price_min: Any,
    price_max: Any,
    n_bins: int,
    va_pct: float = 0.70,
    bar_sec: int = 86400,
    now: float | None = None,
    metric: str = "dwell",
    want_today: bool = False,
    want_sessions: bool = False,
    want_fine: bool = False,
) -> dict:
    """実ティックプロファイルを計算する（candle 版と同一スキーマ）。

    ``metric='dwell'``（既定）: セッション認識の実ティック滞在秒（tpo=滞在秒・tpo_units=総滞在秒）。
    ``metric='count'``（src=m1）: 生ティック数（tpo=ティック数・tpo_units=総ティック数）。セッションマスク
        非適用のため、薄商いの時間帯（休場帯）の価格もカウントされ、dwell とは分布が異なる。

    実期間 ``[t0, t1+bar_sec)`` を日単位に走査する。完全日は :func:`_day_rollup`（メモリ→ディスク→計算）、
    境界日は :func:`_partial_rollup` で固定グリッド ``{dwell[], cnt[]}`` を得て、metric に対応する配列を
    ``fine[]`` に加算し、固定グリッド中心を表示 bin へ再集計して tpo[] を得る。POC/VA は
    :func:`market_profile._value_area` を再利用する（dwell/count で同一定義）。

    ``want_fine=True`` のとき、応答に ``fine[]``（表示 bin 再集計**前**の GRID_W 固定グリッド dwell/cnt・
    ``kw0=floor(price_min/GRID_W)`` 起点）／``fine_kmin``／``grid_w`` を付加する（tick 逐次成長のクライアント
    側忠実 binning 用・既定 False は不変＝キーを付けない）。

    ``want_sessions=True`` のとき、応答に ``sessions[]``（各カレンダー日の表示 bin プロファイル
    ``[{"date":"YYYY-MM-DD","tpo":[...]}]``・日付昇順）を付加する。走査中の日別ロールアップ
    ``roll[roll_key]``（metric に従い dwell/cnt）を表示 bin へ再集計し、境界分割日は同一 date で合算する
    （移植元 prototype_260630-01/mp_core.py want_sessions）。既定 False は不変（sessions キーを付けない）。

    全期間化: 250 日キャップは撤廃し ``[t0, t1+bar_sec)`` の全日を集計する。各完了日はディスク/メモリ
    キャッシュ経由で O(1) ロードされるため、一度ウォームすれば全期間でも高速（数秒）。
    perf 注意（初回コールド時のみ重い）: ディスク未ウォームの完了日は per-day parquet 逐次読込で
    日数比例のブロックとなる。事前に :func:`warm_dwell_cache` で全期間の完了日を構築しておくこと。
    """
    roll_key = "cnt" if metric == "count" else "dwell"  # src=m1 は生ティック数（セッション非依存）。
    now_val = _time.time() if now is None else float(now)  # Y2a: 当日判定の基準時刻（既定は現在時刻）。
    price_min = float(price_min)
    price_max = float(price_max)
    if price_max <= price_min:  # レンジ縮退はゼロ割回避のため +1。
        price_max = price_min + 1.0
    n_bins = max(1, int(n_bins))

    edges = np.linspace(price_min, price_max, n_bins + 1)
    centers = (edges[:-1] + edges[1:]) / 2.0
    binw = (price_max - price_min) / n_bins

    win_to = int(t1) + int(bar_sec)
    win_from = int(t0)  # 全期間化: キャップによる window クランプは行わない（全日を集計）。

    # active table は直近 _ACTIVE_TABLE_DAYS 日から構築する（要求窓の狭さに依存しない）。
    #   キャッシュはプロセス内 symbol 単位で 1 回。win_from で下限を切ると、初回要求が数日窓の場合に
    #   一部曜日が未カバーとなり「その曜日は全休場扱い→dwell=0」の欠陥マスクが恒久キャッシュされる。
    #   固定の直近スパンにすれば全曜日を確実にカバーする（試作の「直近120日」と一致）。
    at_from = win_to - _ACTIVE_TABLE_DAYS * 86400
    table = _active_table(symbol, at_from, win_to)

    kw0 = int(np.floor(price_min / GRID_W))
    size = int(np.floor(price_max / GRID_W)) - kw0 + 1
    fine = np.zeros(max(size, 1), dtype=float)
    last_roll = None  # want_today 用: 窓の最終日ぶんのロールアップ（スナップショット当日強調）。

    # want_sessions: セッション日 -> 表示 bin プロファイル（境界分割日は同日キーで合算・ISSUE-078）。
    #   固定グリッド中心 → 表示 bin 変換は下段の disp（centers_fine 経由）と同一定義を使う。
    sessions: dict[str, np.ndarray] = {}

    day = session_day_start(win_from)
    while day < win_to:
        day_end = next_session_day_start(day)
        lo_t = max(day, win_from)
        hi_t = min(day_end, win_to)
        if lo_t < hi_t:
            if lo_t == day and hi_t == day_end:
                roll = _day_rollup(symbol, day, table, now_val)          # 完全日=完了日のみキャッシュ。
            else:
                roll = _partial_rollup(symbol, lo_t, hi_t, table, now_val)  # 境界日=完了窓のみキャッシュ。
            if roll is not None:
                last_roll = roll  # 走査順＝時系列昇順のため、最後に非 None だったのが窓最終日ぶん。
                arr = roll[roll_key]  # metric に応じて dwell 秒 / 生ティック数 を集計。
                off = roll["kmin"] - kw0
                lo = max(0, off)
                hi = min(size, off + len(arr))
                if hi > lo:
                    fine[lo:hi] += arr[(lo - off):(hi - off)]
                if want_sessions:
                    # その日の roll[roll_key] を表示 bin へ再集計＝日別プロファイルの形（試作 mp_core と同）。
                    cd = (roll["kmin"] + np.arange(len(arr)) + 0.5) * GRID_W
                    dd = np.clip(((cd - price_min) / binw).astype(int), 0, n_bins - 1)
                    da = np.zeros(n_bins, dtype=float)
                    np.add.at(da, dd, arr)
                    ds = session_date_label(day)
                    prev = sessions.get(ds)  # 境界分割日（完全日と部分日が同一 ds）は合算。
                    sessions[ds] = da if prev is None else prev + da
        day = day_end

    # 固定グリッド(fine) → 表示 bin へ再集計。
    centers_fine = (kw0 + np.arange(size) + 0.5) * GRID_W
    disp = np.clip(((centers_fine - price_min) / binw).astype(int), 0, n_bins - 1)
    tpo = np.zeros(n_bins, dtype=float)
    np.add.at(tpo, disp, fine[:size])

    tmax = float(tpo.max()) if tpo.max() > 0 else 1.0
    poc = float(centers[int(tpo.argmax())])
    va_low, va_high = _value_area(tpo, centers, va_pct)

    bins = [
        {
            "price": round(float(centers[i]), 2),
            "tpo": int(round(float(tpo[i]))),
            "norm": round(float(tpo[i]) / tmax, 4),
        }
        for i in range(n_bins)
    ]
    out = {
        "bins": bins,
        "poc": round(poc, 2),
        "va_low": round(float(va_low), 2),
        "va_high": round(float(va_high), 2),
        "price_min": price_min,
        "price_max": price_max,
        "tpo_units": int(round(float(fine.sum()))),  # metric に応じ 総 dwell 秒 / 総ティック数（int 丸め）。
        "n_bins": n_bins,
    }
    if want_fine:
        # tick 逐次成長の忠実 binning 用に、表示 bin へ再集計する前の **GRID_W 固定グリッド**（base 累積）を
        #   露出する。クライアント側 DwellAccumulator は forming tick を同一 fine grid（kw0=fine_kmin 起点）へ
        #   累積し、combined fine → 表示 bin 再集計することで mp_core.compute_profile と厳密一致する
        #   （base=表示 bin 再集計・forming=表示 bin 直接 の二方式併存による POC/VA 乖離を消す）。
        #   既定 want_fine=False では本キーを付けない＝既存スキーマ不変。
        fine_len = size if size >= 1 else len(fine)
        out["fine"] = [float(v) for v in fine[:fine_len]]
        out["fine_kmin"] = int(kw0)
        out["grid_w"] = float(GRID_W)
    if want_today:
        # 窓の最終日ぶんを別集計して表示 bin へ再集計する（スナップショット当日強調・増分2 C）。
        #   移植元 prototype_260630-01/mp_core.py want_today（dwell/m1=最終日ロールアップの再ビン）。
        today = np.zeros(n_bins, dtype=float)
        if last_roll is not None:
            arr = last_roll[roll_key]
            off = last_roll["kmin"] - kw0
            ft = np.zeros(max(size, 1), dtype=float)
            lo = max(0, off)
            hi = min(size, off + len(arr))
            if hi > lo:
                ft[lo:hi] += arr[(lo - off):(hi - off)]
            np.add.at(today, disp, ft[:size])
        today_max = float(today.max()) if today.max() > 0 else 1.0
        out["today"] = [round(float(v), 3) for v in today]
        out["today_max"] = today_max
    if want_sessions:
        # 日付昇順で {date, tpo[], poc, va_low, va_high} を返す。VA は累積プロファイルと同一定義
        #   （_value_area）を各日 tpo に適用する＝当日 MP 読み取りと VA 線が一致する（DRY・単一定義）。
        out["sessions"] = [
            _session_entry(d, a, centers, va_pct) for d, a in sorted(sessions.items())
        ]
    return out


# --------------------------------------------------------------------------- #
# ウォーマー（事前ビルド）: 完了日ロールアップをディスクへ一括構築（冪等）
# --------------------------------------------------------------------------- #
def _day_start_from_tick_path(p: Any) -> int:
    """ティック parquet パス ``.../YYYY/MM/DD/<symbol>_ticks.parquet`` から day_start(UTC 秒) を得る。"""
    parts = _Path(p).parts
    y, m, d = int(parts[-4]), int(parts[-3]), int(parts[-2])
    return int(pd.Timestamp(f"{y:04d}-{m:02d}-{d:02d}", tz="UTC").timestamp())


def warm_dwell_cache(
    symbol: str, start: Any = None, end: Any = None, now: float | None = None
) -> dict:
    """全 or 指定期間の完了日ロールアップをディスクへ一括構築する（冪等・進捗 print）。

    :func:`marketdata.tick_m1.day_parquet_files` で実在日を列挙し、各完了日を :func:`_day_rollup` で
    構築・保存する。既にディスクにある完了日はスキップ（冪等）。当日（未確定日）は永続化しない。
    一度回せば以降の全期間 dwell はディスクから高速ロードできる。

    Args:
        symbol: 実ティック symbol（例 'JP225'）。
        start/end: 期間端（None は start=2000-01-01 / end=当日。存在日のみ処理）。
        now: 完了日判定の基準時刻（既定は現在時刻。テスト注入用）。

    Returns:
        ``{built, skipped, days}``（構築数・スキップ数・列挙された実在日数）。
    """
    now_val = _time.time() if now is None else float(now)
    lo = pd.Timestamp("2000-01-01") if start is None else pd.Timestamp(start)
    hi = pd.Timestamp(now_val, unit="s").normalize() if end is None else pd.Timestamp(end)
    files = day_parquet_files(lo, hi, symbol=symbol)
    win_to = int(hi.timestamp()) + 86400
    at_from = win_to - _ACTIVE_TABLE_DAYS * 86400
    table = _active_table(symbol, at_from, win_to)

    built = skipped = 0
    # ISSUE-078: 実在 parquet（UTC 日）から被覆セッション日集合を導出する（同一セッションは 2 UTC 日に
    #   跨るため set で重複排除）。セッション完了判定は next_session_day_start（DST 23h/25h 対応）。
    session_days = sorted({session_day_start(_day_start_from_tick_path(p)) for p in files}
                          | {session_day_start(_day_start_from_tick_path(p) + 86399) for p in files})
    for day_start in session_days:
        if next_session_day_start(day_start) > now_val:  # 未確定の当日セッションは永続化しない。
            continue
        if _cache_path(symbol, day_start).is_file():  # 冪等: 既存キャッシュはスキップ。
            skipped += 1
            continue
        _day_rollup(symbol, day_start, table, now_val)
        built += 1
        if built % 25 == 0:
            print(f"[warm] {symbol}: {built} built / {skipped} skipped ...")
    print(f"[warm] {symbol}: done — {built} built, {skipped} skipped, {len(files)} days enumerated")
    return {"built": built, "skipped": skipped, "days": len(files)}


if __name__ == "__main__":  # 小さな CLI エントリ（例: python -m adapter.compute.market_profile_dwell --warm jp225_tick）
    import argparse

    _parser = argparse.ArgumentParser(description="Market Profile dwell 日別ロールアップのディスクキャッシュ・ウォーマー")
    _parser.add_argument("--warm", metavar="REF_OR_SYMBOL", required=True,
                         help="datasetRef（例 jp225_tick）または実ティック symbol（例 JP225）")
    _parser.add_argument("--start", default=None, help="期間開始（例 2020-01-01・既定 全期間）")
    _parser.add_argument("--end", default=None, help="期間終了（例 2024-12-31・既定 当日）")
    _args = _parser.parse_args()
    _sym = resolve_symbol(_args.warm) or _args.warm  # ref なら symbol へ解決、それ以外は symbol とみなす。
    print(f"[warm] cache root = {_cache_root()}")
    warm_dwell_cache(_sym, start=_args.start, end=_args.end)
