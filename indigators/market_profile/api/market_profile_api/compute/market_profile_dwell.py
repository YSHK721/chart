"""market_profile_dwell — 実ティック滞在（真の time-at-price・セッション認識）プロファイル計算。

``src=candle``（足レンジ TPO・:mod:`market_profile`）に対し、本モジュールは ``src=dwell`` を担う。
原子＝「価格帯の実ティック滞在秒」で集計する。応答スキーマ（bins/poc/va_low/va_high/price_min/
price_max/tpo_units/n_bins）は candle 版と同一に保つ（tpo は dwell 秒＝int へ丸め）。

セッション認識（休場自動除外）:
    (曜日×時) のティック密度から活発/休場を判定し、隣接ティック間ギャップのうち「活発な時間帯に
    属する秒」だけを滞在に計上する。これにより週末・日次メンテの休場帯を除外しつつ、取引中の
    静かな滞在は満額残す（試作 prototype_260630-01/mp_core.py が実証したアルゴリズムを本体作法へ移植）。

ディスク永続キャッシュ（全期間高速化・:func:`market_profile_dwell_warmer.warm_dwell_cache`）:
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
      :func:`market_profile_dwell_warmer.warm_dwell_cache` を 1 回実行してキャッシュを構築して
      おくこと（運用手順）。
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
# セッション認識（活発/休場地図）の純カーネル（ISSUE-094 🔴-2）。集計側は下段の委譲ラッパー
#   （_build_active_table / _active_seconds_cross / _table_for_day）で monkeypatch 経路と byte を温存する。
from market_profile_api.compute import session_activity as _session_activity
# ディスクキャッシュ Repository（ISSUE-040(b) SRP 分離 / ISSUE-092 ④ gateway 移設 / ISSUE-137 DIP 逆転）。
# 集計数学は本モジュール、永続化 I/O は gateway 層の Store が担う（旧 compute パスは互換シムとして温存）。
# ISSUE-137: 永続化 Store への依存は compute 所有の Output Boundary（StorePort）へ逆転（tick I/O と同規律）。
#   具象 DwellRollupStore の結線は composition root（gateway/composition）が担い、compute は本ポート
#   （dwell_store()）にのみ依存する（module-level 直 new を撤去）。
from market_profile_api.compute.store_port import (  # noqa: F401  (set_dwell_store は注入 API として再エクスポート)
    DwellStorePort,
    dwell_cache_miss,
    dwell_store,
    set_dwell_store,
)

# ISSUE-087 🟡-3: repo 根/MP api の解決は venv の .pth（tools/install_dev_paths.py）が担う（実行時 sys.path 改変を撤去）。
# ISSUE-091 🔴-2: ティック物理格納（day parquet・DATA_DIR）への依存は compute 所有の
#   Output Boundary へ逆転。具象は gateway/marketdata_tick_store が実装。
# ISSUE-136（ISP）: dwell は tick 読取のみ（data_dir 不使用）ため狭い TickReaderPort に依存する。
from market_profile_api.compute.tick_store_port import tick_reader as _tick_reader
# セッション日境界（ISSUE-078・NY17:00 ET 基準）。日切り・完了判定・ラベルの唯一の規則源
#（marketdata の純業務規則＝I/O 非依存のため内側 import を許容）。
from marketdata.session_day import (  # noqa: E402
    next_session_day_start,
    session_date_label,
    session_day_start,
)

# ISSUE-133（SRP）: 統計コア（純数学＝セッション認識滞在秒積分・固定グリッドロールアップ）は
# market_profile_dwell_kernel へ分離した。本モジュール（キャッシュ協調）は公開シンボルを再エクスポート
# し、呼出面（``mpd._session_dwell`` / ``mpd._rollup_ticks`` / ``mpd.GRID_W``）と数値を完全に温存する。
from market_profile_api.compute.market_profile_dwell_kernel import (  # noqa: E402,F401
    GRID_W,
    _rollup_ticks,
    _session_dwell,
)
# ISSUE-178: 層間 DTO（不変）。日別ロールアップは生 dict でなく :class:`DayRollup` で受け渡す。
from market_profile_api.compute.rollup_dto import DayRollup  # noqa: E402,F401


def day_parquet_files(lo_day: int, hi_day: int, *, symbol: str) -> "list[_Path]":
    """正準ティック日別ファイルの列挙（TickReaderPort へ委譲・read-only）。

    ISSUE-183: 引数は UNIX 秒（int・UTC 日始端）。``pd.Timestamp`` 変換は gateway 実装が担う。
    既存テストの monkeypatch 単一注入点（``mpd.day_parquet_files``）を module 属性として温存する。
    """
    return _tick_reader().day_files(lo_day, hi_day, symbol=symbol)

# datasetRef → 実ティック symbol 解決（forming_bar.TICK_REFS と整合。'jp225_tick'→'JP225'）。
TICK_REF_SYMBOLS: dict[str, str] = {"jp225_tick": "JP225"}

# セッション認識 dwell のパラメータ（試作と一致）。規則は session_activity が唯一の規則源。
_ACTIVE_FRAC = _session_activity.ACTIVE_FRAC   # (曜日×時) のティック数が ピーク×この割合 未満なら「休場」。
# GRID_W（固定価格グリッド幅 pt）は market_profile_dwell_kernel から再エクスポート済み（ISSUE-133 SRP）。

# 全期間化（250日キャップ撤廃）。完了日はディスク/メモリキャッシュ経由で O(1) ロードされるため、
# 集計窓を直近日数に切り詰めず ``[t0, t1+bar_sec)`` の全日を集計する（初回ウォーム後は高速）。
# ``MAX_DWELL_DAYS`` は後方互換のため定数として残すが、compute_dwell_profile は窓クランプに使用しない。
MAX_DWELL_DAYS = 250      # （後方互換・非使用）かつて集計窓を直近ぶんに限定していた上限。
_MAX_DWELL_DAYS = MAX_DWELL_DAYS  # 後方互換の別名。
_ACTIVE_TABLE_DAYS = 120  # active table 構築に用いる直近日数（試作と同じ・一度だけ構築）。

# ディスク永続キャッシュ（日別ロールアップ）。既存の生データ/ticks/CSV は触らず、新規 cache
# ディレクトリのみに読み書きする。完了日（UTC 確定日）のみ永続化し、当日（未確定）は都度計算する。
# ISSUE-183 item5: 永続化設定（cache root / 形式版数＝偶有的性質）は本質層である本モジュールから
#   gateway 側 :mod:`market_profile_api.gateway.cache_settings`（``DWELL_CACHE_ROOT`` /
#   ``DWELL_CACHE_VERSION``）へ**移送**した。旧 module private（``_CACHE_ROOT`` / ``_CACHE_VERSION``）は
#   Composition Root に層外から読まれており、カプセル化の破れかつ偶有的性質の本質層居住だった。
#   移送であり複製ではない（二重情報源を作らない）。テストの tmp 隔離・版数 bump も gateway 側を差し替える。

# ディスクキャッシュ Repository（ISSUE-040(b) / ISSUE-137 DIP）。永続化 I/O（save/load/署名/無効化・
# parquet/tempfile）は DwellRollupStore に分離済み。ISSUE-137: 既定 Store の合成は composition root
# （gateway/composition.default_dwell_store）へ移設した。本体 module 変数（GRID_W / day_parquet_files）は
# composition の provider が call-time に読む（monkeypatch 経路を温存）。
# 永続化 I/O は dwell_store()（未注入時は composition の既定・注入時は set_dwell_store の実体）へ委譲する。
# ISSUE-177（LSP）: CACHE_MISS 番兵は **call-time** に :func:`dwell_cache_miss` で取得する（module 定数
#   への import 時束縛を撤去）。定数化すると既定具象 ``DwellRollupStore.CACHE_MISS`` が焼き込まれ、
#   ``DwellStorePort`` 準拠だが既定具象非派生の Store を :func:`set_dwell_store` で注入したとき、その
#   Store の番兵と identity 不一致になり「キャッシュミス番兵を実データとして受理」する（Port 準拠実装が
#   既定具象と置換不能＝LSP 破綻）。番兵の所有者は常に現在の Store。

# 生ティック parquet の必須列（marketdata.tick_m1._TICK_COLUMNS と同じ意味）。
_TICK_COLUMNS = ["timestamp", "bidPrice", "askPrice"]
_OUTLIER_FRAC = 0.30      # 窓内 mid 中央値 ±30% の外れ値除去（tick_window と同基準）。

# プロセス内キャッシュ（AB 兼用・perf）。走査した過去日ぶんが累積する（各エントリは小配列＝緩く有界）。
# 完了した過去日/窓のみ登録し、現在進行中の当日はキャッシュしない（Y2a・_day_rollup/_partial_rollup 参照）。
_DAY_CACHE: dict[tuple[str, int], "DayRollup | None"] = {}      # (symbol, day_start) → rollup or None
_PARTIAL_CACHE: dict[tuple[str, int, int], "DayRollup | None"] = {}  # (symbol, lo, hi) → rollup or None
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
    """``[start, end)`` の実ティックを ``(secs:int64, mids:float64)`` で返す（TickStorePort へ委譲）。

    ISSUE-178: Port（:meth:`TickReaderPort.load_window_ticks`）は不変 DTO :class:`TickWindow` を返す。
    本シムはその 2 配列（read-only）をタプルへ展開して既存呼出面を保つ。

    ISSUE-133 SRP: 日別 parquet の列挙・読取・concat・tz 除去・窓マスク・mid 算出・外れ値除去・安定
    ソート（＝ティック格納スキーマの復号＝偶有的性質）は gateway の :class:`TickReaderPort` 実装へ移設した。
    本関数はテストの単一注入点（``mpd._load_window_ticks`` の monkeypatch）を module 属性として温存する
    薄い委譲であり、ティック列（``_TICK_COLUMNS``）と外れ値しきい（``_OUTLIER_FRAC``）を注入する。
    """
    win = _tick_reader().load_window_ticks(
        symbol, start, end, columns=_TICK_COLUMNS, outlier_frac=_OUTLIER_FRAC
    )
    # ISSUE-178: 境界（Port）は不変 DTO :class:`TickWindow`。本シムは compute 内部の既存呼出面
    #   （``secs, mids = _load_window_ticks(...)`` の 2 値タプル・テストの monkeypatch 単一注入点）を
    #   温存するため展開して返す。配列自体は read-only のまま＝プロセス内共有でも in-place 汚染しない。
    return win.secs, win.mids


# --------------------------------------------------------------------------- #
# セッション認識 dwell（活動テーブル + 活発秒の積分）
# --------------------------------------------------------------------------- #
def _build_active_table(secs: np.ndarray) -> np.ndarray:
    """セッション認識カーネルへの委譲（:func:`session_activity.build_active_table`）。

    既存テストの monkeypatch 単一注入点（``mpd._build_active_table``）を module 属性として温存する。
    ``_ACTIVE_FRAC``（module 変数）を call-time に読むため、テストが ``_ACTIVE_FRAC`` を差し替える
    場合も反映される。
    """
    return _session_activity.build_active_table(secs, active_frac=_ACTIVE_FRAC)


def _active_seconds_cross(a: int, b: int, table: np.ndarray) -> int:
    """セッション認識カーネルへの委譲（:func:`session_activity.active_seconds_cross`）。

    kernel の ``_session_dwell`` は :mod:`session_activity` を直参照するが、本 module 名での跨ぎギャップ
    積分（既存テストの ``mpd._active_seconds_cross`` 単一注入点）を温存するため委譲シンボルを残す。
    """
    return _session_activity.active_seconds_cross(a, b, table)


# --------------------------------------------------------------------------- #
# 固定グリッド日別ロールアップ（メモリキャッシュ）
#   純数学の _session_dwell / _rollup_ticks は market_profile_dwell_kernel から再エクスポート済み
#   （ISSUE-133 SRP・冒頭 import 参照）。以下のキャッシュ協調関数は bare name で呼ぶ。
# --------------------------------------------------------------------------- #
def _active_table(symbol: str, at_from: int, win_to: int) -> np.ndarray:
    """symbol×窓の活動テーブルを構築してキャッシュする。

    ISSUE-089: メモキーは (symbol, 日量子化した at_from/win_to)。旧実装の symbol のみキー
    （先勝ち）は、プロセス内で最初に触った要求の窓のテーブルが以後の全要求へ流用され、
    境界日 partial・新規保存の日次 npz へプロセス履歴依存の値が焼き込まれる非決定性の温床
    だった（byte-parity golden が数時間で再赤化した真因）。日量子化はリプレイの sliding 窓で
    キーが分単位に増殖するのを防ぐ（テーブルは曜日×時の粗い地図＝日内の差は実質無い）。
    """
    key = (symbol, int(at_from) // 86400, int(win_to) // 86400)
    cached = _ACTIVE_TABLE.get(key)
    if cached is not None:
        return cached
    secs, _ = _load_window_ticks(symbol, at_from, win_to)
    table = _build_active_table(secs) if len(secs) else np.ones((7, 24), dtype=bool)
    _ACTIVE_TABLE[key] = table
    return table


def _table_for_day(symbol: str, day_start: int) -> np.ndarray:
    """セッション認識カーネルへの委譲（:func:`session_activity.table_for_day`・ISSUE-089 月初アンカー）。

    月初アンカーの因果規則は :mod:`session_activity` が唯一の規則源。活動テーブルの構築（120 日窓の
    ティック読込＋プロセス内キャッシュ）は本 module の :func:`_active_table` を ``active_table_fn`` として
    注入する（``_active_table`` は ``mpd._load_window_ticks`` を call-time 参照＝monkeypatch を尊重する）。
    既存テストの monkeypatch 単一注入点（``mpd._table_for_day``）を module 属性として温存する。
    """
    return _session_activity.table_for_day(
        symbol, int(day_start), active_table_days=_ACTIVE_TABLE_DAYS, active_table_fn=_active_table
    )


# --------------------------------------------------------------------------- #
# 日別ロールアップのディスク永続キャッシュ委譲（DwellRollupStore・新規 cache ディレクトリのみ・fail-safe）
# --------------------------------------------------------------------------- #
# 以下は :class:`DwellRollupStore`（永続化 Repository）への薄い委譲。公開/内部シンボル名は不変に保ち、
# 既存テストの monkeypatch 経路（`mpd._cache_path` / `_save_day_rollup` / `_load_day_rollup` /
# `_day_source_signature`）と byte 出力を温存する（ISSUE-040(b)）。永続化設定（cache root / 形式版数）の
# 差し替えは gateway 側 `cache_settings.DWELL_CACHE_ROOT` / `DWELL_CACHE_VERSION`（ISSUE-183 item5）。
def _cache_root() -> _Path:
    """ディスクキャッシュの基点 ``DATA_DIR/cache/market_profile_dwell`` を返す。

    差替は gateway 側 ``cache_settings.DWELL_CACHE_ROOT``（ISSUE-183 item5）。
    """
    return dwell_store().cache_root()


def _cache_path(symbol: str, day_start: int) -> _Path:
    """日別ロールアップの保存パス ``<root>/<symbol>/v<version>/g<GRID_W>/<day_start>.npz``。

    ISSUE-089 で版数 dir を挟む実配置へ移行済み（本 docstring は旧形のまま残置していた＝ISSUE-172）。
    構成の唯一の定義は :meth:`DwellRollupStore._relative_parts`（本関数は Store へ委譲するのみ）。
    """
    return dwell_store().cache_path(symbol, day_start)


def _day_source_signature(symbol: str, day_start: int) -> str:
    """完了日 ``day_start`` のソースティック署名（無効化用・Store へ委譲）。ファイル無しは空文字。"""
    return dwell_store().day_source_signature(symbol, day_start)


def _save_day_rollup(path: _Path, roll: "DayRollup | None", sig: str = "") -> None:
    """ロールアップ（None=実データ無し完了日を含む）を ``.npz`` へ原子的に保存する（Store へ委譲）。"""
    dwell_store().save_day_rollup(path, roll, sig)


def _load_day_rollup(path: _Path) -> "tuple[Any, str]":
    """ディスクから日別ロールアップと署名を読む（Store へ委譲）。未ヒット/破損/不整合は ``(CACHE_MISS, "")``。"""
    return dwell_store().load_day_rollup(path)


def _day_rollup(symbol: str, day_start: int, table: "np.ndarray | None", now: float) -> "DayRollup | None":
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
        if disk is not dwell_cache_miss() and cached_sig == cur_sig:
            if disk is not None:
                _DAY_CACHE[key] = disk  # 非空のみメモ化。
            return disk
    secs, mids = _load_window_ticks(symbol, day_start, day_end)  # 計算。
    # ISSUE-089: 表は「日の属する月初」アンカーで内部導出する（呼び出し側の table は使わない＝
    #   キャッシュへ焼き込む値をリクエスト窓/プロセス履歴から独立させる。引数は互換のため残置）。
    roll = _rollup_ticks(secs, mids, _table_for_day(symbol, int(day_start)))
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


def _partial_rollup(symbol: str, lo: int, hi: int, table: np.ndarray, now: float) -> "DayRollup | None":
    """境界日（サブ日足）用の部分集計 ``[lo, hi)`` を固定グリッドへ集約する。

    Y2a: 窓終端が完了した（``hi <= now``）場合のみキャッシュする。当日の部分足（``hi > now``）は
    新ティックで stale 化しうるため毎回再計算する。
    """
    key = (symbol, int(lo), int(hi))
    if key in _PARTIAL_CACHE:
        return _PARTIAL_CACHE[key]
    secs, mids = _load_window_ticks(symbol, lo, hi)
    roll = _rollup_ticks(secs, mids, _table_for_day(symbol, session_day_start(int(lo))))  # ISSUE-089: 日アンカー表。
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
    *,
    va_pct: float,  # ISSUE-260: 必須（既定は market_profile.VA_PCT_DEFAULT 唯一源が持つ）。
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
    境界日は :func:`_partial_rollup` で固定グリッド :class:`DayRollup` を得て、metric に対応する配列を
    ``fine[]`` に加算し、固定グリッド中心を表示 bin へ再集計して tpo[] を得る。POC/VA は
    :func:`market_profile._value_area` を再利用する（dwell/count で同一定義）。

    ``want_fine=True`` のとき、応答に ``fine[]``（表示 bin 再集計**前**の GRID_W 固定グリッド dwell/cnt・
    ``kw0=floor(price_min/GRID_W)`` 起点）／``fine_kmin``／``grid_w`` を付加する（tick 逐次成長のクライアント
    側忠実 binning 用・既定 False は不変＝キーを付けない）。

    ``want_sessions=True`` のとき、応答に ``sessions[]``（各カレンダー日の表示 bin プロファイル
    ``[{"date":"YYYY-MM-DD","tpo":[...]}]``・日付昇順）を付加する。走査中の日別ロールアップ
    ``roll`` の metric 対応配列（dwell/cnt）を表示 bin へ再集計し、境界分割日は同一 date で合算する
    （移植元 prototype_260630-01/mp_core.py want_sessions）。既定 False は不変（sessions キーを付けない）。

    全期間化: 250 日キャップは撤廃し ``[t0, t1+bar_sec)`` の全日を集計する。各完了日はディスク/メモリ
    キャッシュ経由で O(1) ロードされるため、一度ウォームすれば全期間でも高速（数秒）。
    perf 注意（初回コールド時のみ重い）: ディスク未ウォームの完了日は per-day parquet 逐次読込で
    日数比例のブロックとなる。事前に :func:`market_profile_dwell_warmer.warm_dwell_cache` で
    全期間の完了日を構築しておくこと。
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
                arr = getattr(roll, roll_key)  # metric に応じて dwell 秒 / 生ティック数 を集計。
                off = roll.kmin - kw0
                lo = max(0, off)
                hi = min(size, off + len(arr))
                if hi > lo:
                    fine[lo:hi] += arr[(lo - off):(hi - off)]
                if want_sessions:
                    # その日の metric 対応配列を表示 bin へ再集計＝日別プロファイルの形（試作 mp_core と同）。
                    cd = (roll.kmin + np.arange(len(arr)) + 0.5) * GRID_W
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
            arr = getattr(last_roll, roll_key)
            off = last_roll.kmin - kw0
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
# ウォーマー（運用バッチ）: 実体は market_profile_dwell_warmer（ISSUE-133 SRP）
# --------------------------------------------------------------------------- #
# 完了日ロールアップの一括ビルド（運用バッチ アクター）は :mod:`market_profile_dwell_warmer` にある。
# 消費者は当該 module を直接 import する（CLI は :mod:`tools.warm_market_profile_cache`）。
#
# ISSUE-305: 本 module に ``warm_dwell_cache`` の遅延委譲を置かない。運用バッチ（外側）→ 統計コア
# （内側）が正しい向きであり、逆向きの委譲は依存の循環を作る。関数内 import はその循環を
# module ロード時に露呈させないだけで、循環そのものは消えていない。
