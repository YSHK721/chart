"""tf_period_profile_controller — GET /tf_period_profile の純ロジック（HTTP 殻非依存）。

「時間足毎のprofile列」機能の配信点。ローリング窓 ``[from, to)`` の実 tick を読み、選択 tf 周期で
分割して**最小価格単位**でビニングした sparse プロファイル列を返す（列数は窓で有界＝応答肥大を防ぐ）。

``handle_tf_period_profile(ref, timeframe, frm, to) -> (status, body)``:
  - 非 tick ref / 非対応 tf（1W/1M・floor 不可）→ 400 nested error（``_error_body`` 再利用）。
  - 不正窓（from>=to / 欠落）→ 400。
  - 正常: ``{ok, tf, unit, from, to, columns:[{time, levels, poc, va_low, va_high, ...}]}``。

依存方向: framework → 本 controller → compute（tf_period_profile / market_profile_dwell）＋ forming_bar
（ref/tf 判定）。tick 読込は :func:`market_profile_dwell._load_window_ticks`（単一注入点・read-only）を再利用。
"""
from __future__ import annotations

import time as _time
from pathlib import Path as _Path
from typing import Any

import numpy as np

from marketdata import tf_meta as _forming_bar  # ISSUE-087 🔴-1: 裸 adapter 依存を排し単一情報源を参照
from market_profile_api.compute import market_profile_dwell as _mpd
from market_profile_api.compute import market_profile_zp as _zp
from market_profile_api.compute import tf_period_columns as _tfc  # ISSUE-094 🔴-2: 集計エンジンの compute 移送先
# ISSUE-092 ④: 日次ディスク JSON の物理 I/O は gateway 層へ抽出（ISSUE-091 #6 レイヤ責務違反の是正）。
from market_profile_api.gateway import tf_period_disk_cache as _tf_disk_cache
# ISSUE-183 item5: 永続化設定（zp 形式版数）の単一情報源は gateway 側 cache_settings。
from market_profile_api.gateway import cache_settings as _cache_settings
# 既定 DATA_DIR の解決は DataRootPort 経由（ISSUE-136 ISP: data_dir のみ使用の狭いポートへ依存）。
from market_profile_api.compute.tick_store_port import data_root as _data_root
from market_profile_api.compute.tf_period_profile import (
    _TFP_CACHE_VERSION,
    tf_period_profiles,
)
from market_profile_api.controller.market_profile_controller import _error_body
# ISSUE-172: 配置記述子（GC 契約 DTO）。tf-period の世代 subdir を所有する当事者は本 controller。
from market_profile_api.cache_layout import CacheLayout as _CacheLayout
# ISSUE-179 項目 B: per-entry キャッシュ協調（メモリ LRU ＋ ディスクの 2 層）の単一実装。
from market_profile_api.controller.tf_period_cache import TfPeriodDayCache as _TfPeriodDayCache

# セッション日境界（ISSUE-078・NY17:00 ET 基準）。日切り・完了判定の唯一の規則源。
from marketdata.session_day import (  # noqa: E402
    next_period_label,
    next_session_day_start,
    period_session_labels,
    session_bar_time,
    session_day_start,
    session_label_to_start,
    session_period_label,
)

_DAY = 86400  # 1 カレンダー日（秒）。per-day キャッシュ／窓分割の単位。

# 対応 tf（固定周期＝floor 可能）→ 周期秒。1W/1M はカレンダーバケット（下記 _BUCKET_TFS）。
_TF_SECONDS = {"1m": 60, "5m": 300, "15m": 900, "30m": 1800, "1h": 3600, "4h": 14400, "1D": _DAY}

# ISSUE-086（全時間足パラメータ統一）: 1W/1M はセッション日次のロールアップ（W-FRI/ME バケット・
#   規則源 marketdata.session_day.session_period_label＝resample と同一規約）で列を構成する。
_BUCKET_TFS = ("1W", "1M")

# アクター: 表示解像度（描画行高＝count 列のビニング粒度）。集計パラメータ（GRID_W/W_LOG 等の
#   統計格子）とは別アクターであり、compute の集計仕様から独立してここ（配信・表示 controller）に置く。
#   ISSUE-094 🔴-2: compute 移送後も本定数は「表示解像度」として controller に残す（count 列の _day_columns で参照）。
# ISSUE-073: 時間足別ビニング解像度（依頼者承認 2026-07-13「1分足は細かく分析したい」・
#   同日 0.5pt→0.0255pt へ細分化指示）。1m は 1 周期の値幅が数〜数十 pt で GRID_W(10pt) では
#   1〜数レベルに潰れるため、データの最小価格刻み 0.0255（mid 量子化＝実 tick の真の解像度・
#   ISSUE-068 以前の min-unit 稼働実績と同値）で量子化する（sparse 応答は占有レベル数に比例＝
#   1m 窓では実用範囲。ISSUE-068 の肥大は 1h×長窓の話）。
#   未指定 tf（5m..1D）は ISSUE-068 の GRID_W(=10pt) を維持（応答肥大・メインスレッドブロック対策を保つ）。
#   ディスクキャッシュは g{unit:g} サブディレクトリで解像度別に自動分離される（混在しない）。
_UNIT_BY_TF: dict[str, float] = {"1m": 0.0255}

# src=zp の対応 tf。周期内分数が少なすぎると z が退化するため 15m 以上に限定する
#   （1m=1 分・5m=5 分では帰無/観測とも計数が退化し z の意味が立たない）。
_ZP_TF_ALLOWED = ("15m", "30m", "1h", "4h", "1D", "1W", "1M")  # ISSUE-086: 1W/1M＝日次 zp の畳み込み。

# ISSUE-055（B: per-day キャッシュ）: 窓 ``[from, to)`` を**カレンダー日単位**に分割し、各日を
#   ``(symbol, tf, day_start)`` でキャッシュする。過去日ティックは不変（実測:
#   .doc/TICK_IMMUTABILITY_VERIFICATION.md）＝完了日（``day_start + _DAY <= now``）の列は永続再利用でき、
#   無効化不要。窓は「完了日はキャッシュ／当日のみ再計算」で組み立てるため、直近窓も初回表示（ウォーム後）も
#   高速化する。窓端がずれても日粒度でヒットする（窓単位キャッシュより頑健）。副次効果として、同一日を
#   常に同じ日内 unit で量子化する＝ローリングで窓ごとに unit が揺れて同一日の描画が変わる現行の不整合も解消。
#   メモリはホット層（有界 LRU）、ディスクは JSON で跨プロセス永続（dwell の日別ディスクキャッシュと同方針）。
# ISSUE-179 項目 B: 協調手順と**その状態（LRU 辞書・上限）**は :class:`TfPeriodDayCache` が所有する
#   （本 module に生の辞書を残すと「協働子が host の private を触る」分割不全になる）。
_DAY_MEM_ENTRY_MAX = 1024  # ISSUE-088 🔵-4: 1M バケット（当月~22日次）×年単位スクロールでの LRU スラッシュ回避（旧 256）。  # メモリ LRU 上限（日エントリ数）。5m の 1 日は数百列で数百 KB になり得るため有界化。

# ディスクキャッシュ根。None=既定（DATA_DIR/cache/tf_period）。False=ディスク無効（テスト隔離用）。
#   Path/str=差替（テストは tmp を注入）。完了日のみ JSON 保存し、当日（未確定）は保存しない。
_TFP_CACHE_ROOT: "Any" = None


class _DayDiskCache:
    """:class:`TfPeriodDayCache` へ渡すディスク層（``DayCacheDiskPort`` 実装）。

    ``_TFP_CACHE_ROOT`` の解決は本 controller の責務のまま（ISSUE-092 ④ の分担）。協働子は
    root も世代規約も知らず、``disk_tf`` を素通しで受ける。module 関数を **call-time に名前解決**
    するため、既存テストの ``_TFP_CACHE_ROOT`` monkeypatch は従来どおり効く。
    """

    def load(self, symbol: Any, disk_tf: Any, disk_key: Any) -> "tuple[float, list] | None":
        return _load_day_disk(symbol, disk_tf, disk_key)

    def save(self, symbol: Any, disk_tf: Any, disk_key: Any, unit: float, columns: list) -> None:
        _save_day_disk(symbol, disk_tf, disk_key, unit, columns)


#: 4 経路（日次 count / 日次 zp / バケット count / バケット zp）が共有する唯一の協調子。
_DAY_CACHE = _TfPeriodDayCache(_DayDiskCache(), max_entries=_DAY_MEM_ENTRY_MAX)


def _reset_tf_period_cache() -> None:
    """per-day メモリキャッシュを全消去する（テスト隔離用・ディスクは触らない）。"""
    _DAY_CACHE.clear()


# --------------------------------------------------------------------------- #
# ディスク配置（世代 subdir）の単一情報源（ISSUE-172）
# --------------------------------------------------------------------------- #
# 完了日 JSON の実配置は ``<root>/<symbol>/<disk_tf>/<key>.json``（``disk_tf`` は下記ビルダが
# 組み立てる ``<tf>/<gen>/<sub>`` の 2 段 subdir）。世代 dir は disk_tf の 2 segment 目で、
# 書込経路は count 日次 / count バケット / zp の 3 系統ある。GC 記述子（:func:`layout`）は
# **この 3 ビルダの出力そのもの**から現行世代名を導出する（世代タグの二重定義を排除する）。
_DISK_TF_GEN_INDEX = 1  # disk_tf("<tf>/<gen>/<sub>") 内の世代 segment 位置（0 起点）。
_TFP_GEN_DEPTH = 1 + _DISK_TF_GEN_INDEX + 1  # root からの階層数（<sym> の 1 段 ＋ disk_tf 内位置）。
_TFP_BUCKET_GEN = "s1"  # ISSUE-086: 1W/1M バケット count 列の世代（日次 count 世代とは独立に据置）。
_TFP_ZP_GEN = "s3"  # ISSUE-085: VA 修正世代（zp 列・count 世代とは独立）。


def _disk_tf_count(tf: Any, unit: float) -> str:
    """日次 count 列の disk_tf。世代は生成本体の ``_TFP_CACHE_VERSION`` に連動（ISSUE-091 A3）。"""
    return f"{tf}/s{_TFP_CACHE_VERSION}/g{unit:g}"


def _disk_tf_bucket(tf: Any, unit: float) -> str:
    """1W/1M バケット count 列の disk_tf（ISSUE-086）。"""
    return f"{tf}/{_TFP_BUCKET_GEN}/g{unit:g}"


def _disk_tf_zp(tf: Any) -> str:
    """zp 列（日次・バケット共通）の disk_tf。ISSUE-088 🔵-3: zp 内部世代へ連動。

    ISSUE-183 item5: zp の形式版数は gateway 側 ``cache_settings.ZP_CACHE_VERSION``（単一情報源）
    から call-time に読む（旧: compute の module private ``_zp._ZP_CACHE_VERSION``）。
    """
    return f"{tf}/{_TFP_ZP_GEN}/zp-v{_cache_settings.ZP_CACHE_VERSION}"


def _disk_tf_variants(tf: Any, unit: float) -> "tuple[str, ...]":
    """本 controller がディスクへ書く全 disk_tf を列挙する（GC 記述子の導出元・ISSUE-172）。"""
    return (_disk_tf_count(tf, unit), _disk_tf_bucket(tf, unit), _disk_tf_zp(tf))


def layout() -> _CacheLayout:
    """GC 向けの現行世代記述子（:class:`CacheLayout`）を返す（ISSUE-172）。

    ``current`` は :func:`_disk_tf_variants` の各出力から世代 segment を抜き出して構成するため、
    書込経路が使う世代タグと定義上一致する（片方だけ bump して使用中 dir が孤児化する事故を防ぐ）。
    ``root`` はディスク無効時（``_TFP_CACHE_ROOT is False``）に ``None``＝走査対象外。
    """
    root = _tfp_disk_root()
    gens = frozenset(
        tf.split("/")[_DISK_TF_GEN_INDEX] for tf in _disk_tf_variants("_", float(_mpd.GRID_W))
    )
    return _CacheLayout(
        name="tf-period",
        root=_Path(root) if root else None,
        gen_depth=_TFP_GEN_DEPTH,  # <sym>/<tf>/<gen>
        current=gens,
        reason=f"tf-period 旧世代（現行 {' / '.join(sorted(gens))}）",
    )


def _tfp_disk_root() -> "_Path | None":
    """ディスクキャッシュ基点を返す（False=無効なら None・既定は DATA_DIR/cache/tf_period）。

    既定の DATA_DIR 解決は DataRootPort 経由（ISSUE-091 🔴-2 で dwell/zp と同規律・ISSUE-136 ISP 分割後）。
    旧 `_mpd._paths` 参照は port 化で撤去済み属性への参照となり実行時 AttributeError を
    起こしていた（ISSUE-092 統合検証の実 HTTP で検出・テストは root 注入経路のため未検出）。
    """
    if _TFP_CACHE_ROOT is False:
        return None
    if _TFP_CACHE_ROOT is not None:
        return _Path(_TFP_CACHE_ROOT)
    return _data_root().data_dir() / "cache" / "tf_period"


def _load_day_disk(symbol: Any, tf: Any, day_start: int) -> "tuple[float, list] | None":
    """完了日の (unit, columns) をディスクから読む。無効/未ヒット/破損は None（＝再計算へ）。

    ISSUE-092 ④: キャッシュ根の有効/無効（``_TFP_CACHE_ROOT`` の monkeypatch）は本 controller に残し、
    call-time 解決した root を gateway の純 I/O（:func:`tf_period_disk_cache.load_day_disk`）へ委譲する。
    """
    root = _tfp_disk_root()
    if root is None:
        return None
    return _tf_disk_cache.load_day_disk(root, symbol, tf, day_start)


def _save_day_disk(symbol: Any, tf: Any, day_start: int, unit: float, columns: list) -> None:
    """完了日の (unit, columns) を JSON へ原子的に保存する（無効/失敗は握りつぶす＝次回再計算）。

    ISSUE-092 ④: root 解決は本 controller（``_TFP_CACHE_ROOT`` の call-time 参照）、原子的書込は
    gateway の純 I/O（:func:`tf_period_disk_cache.save_day_disk`）へ委譲する。挙動・パスは不変。
    """
    root = _tfp_disk_root()
    if root is None:
        return
    _tf_disk_cache.save_day_disk(root, symbol, tf, day_start, unit, columns)


def _merge_live_tail(
    secs: np.ndarray, mids: np.ndarray, live_ticks: "list | None", lo: int, hi: int
) -> "tuple[np.ndarray, np.ndarray]":
    """live buffer 末尾合成の compute 委譲（:func:`tf_period_columns.merge_live_tail`・ISSUE-094）。

    controller 内の呼び出し元（_day_columns 等）が本 module 名で参照する委譲シンボルを温存する。
    """
    return _tfc.merge_live_tail(secs, mids, live_ticks, lo, hi)


def _day_columns(
    symbol: Any, tf: Any, tf_sec: int, day_start: int, now_val: float,
    live_ticks: "list | None" = None,
) -> "tuple[float, list]":
    """1 カレンダー日 ``[day_start, day_start+_DAY)`` の tf-period 列 ``(unit, columns)`` を返す。

    完了日（``day_start + _DAY <= now``）は **メモリ → ディスク → 計算（＋両層へ保存）** の順で解決し、
    再利用する（不変ゆえ無効化不要）。当日（未確定）はキャッシュせず毎回計算する（ティック成長のため）。
    """
    day_start = int(day_start)
    day_end = next_session_day_start(day_start)  # ISSUE-078: セッション日窓（DST 切替日は 23h/25h）。
    completed = day_end <= now_val
    # ISSUE-068: 列のビニング解像度を最小価格単位→GRID_W(=10pt) へ粗くする（依頼者承認・2026-07-12）。
    #   最小単位（≈0.0255）は 1 期間に数百レベルを生み可視1年で 37MB → parse/描画でメインスレッドが
    #   5.4s ブロックしていた。列幅~10px では最小単位は視認不能ゆえ表示損失なし。GRID_W 化で 37MB→~1-2MB。
    #   旧 min-unit ディスクキャッシュ（<root>/<sym>/<tf>/<day>.json）と混ざらないよう新 subdir へ隔離する。
    # ISSUE-073: tf 別解像度（_UNIT_BY_TF）。1m のみ最小刻み 0.0255・その他は GRID_W 維持（依頼者承認 2026-07-13）。
    unit = float(_UNIT_BY_TF.get(str(tf), _mpd.GRID_W))
    # ISSUE-078: セッション日キー世代 subdir（旧 UTC 日と不混在）。ISSUE-091 A3: 世代は生成本体の
    #   _TFP_CACHE_VERSION に連動（手書きリテラル排除・zp/dwell と同規律。v1 = 従来 's1' と同一パス）。
    # ISSUE-172: パス構成は単一情報源のビルダへ（GC 記述子 layout() と同一式）。

    def _compute() -> "tuple[float, list]":
        # ISSUE-078: 周期は「始端が本セッション日に属する」もので構成する。周期グリッドは従来どおり
        #   UTC floor（チャートのバー時刻と一致）。tf<=1h は境界（毎時 21/22:00 UTC）に整列するため従来と
        #   同形。4h はセッション境界を跨ぐ最終周期が生じるが始端所属で一意に割当（重複列を作らない）。
        #   1D はセッション日そのもの＝1 周期（time=セッション始端）。
        if tf_sec >= _DAY:
            secs, mids = _mpd._load_window_ticks(symbol, day_start, day_end)
            if not completed:
                # ISSUE-083 追補: 当日のみ live buffer 末尾を合成（最新ティックの即時反映・完了日は不変）。
                secs, mids = _merge_live_tail(secs, mids, live_ticks, day_start, day_end)
            # 単一周期化: 秒を始端相対へシフトし全ティックを period 0 に畳む → time をセッション始端へ戻す。
            shifted = np.asarray(secs, dtype=np.int64) - day_start if len(secs) else secs
            cols = tf_period_profiles(shifted, mids, int(day_end - day_start), unit, 0, int(day_end - day_start))
            for c in cols:
                # 1D 列の time は 1D バー時刻規約（セッション日ラベルの UTC 深夜＝rollup/forming と同一）。
                c["time"] = int(session_bar_time(day_start))
        else:
            p_first = ((day_start + tf_sec - 1) // tf_sec) * tf_sec       # 始端が本セッションに属す最初の周期。
            p_last = ((day_end - 1) // tf_sec) * tf_sec                    # 始端が day_end 未満の最後の周期。
            secs, mids = _mpd._load_window_ticks(symbol, p_first, p_last + tf_sec)
            if not completed:
                # ISSUE-083 追補: 当日のみ live buffer 末尾を合成（最新ティックの即時反映・完了日は不変）。
                secs, mids = _merge_live_tail(secs, mids, live_ticks, p_first, p_last + tf_sec)
            cols = tf_period_profiles(secs, mids, tf_sec, unit, p_first, day_end)
        return unit, cols

    return _DAY_CACHE.resolve(  # ISSUE-179 項目 B: 協調は単一実装（ISSUE-068: GRID_W subdir）。
        key=(symbol, tf, day_start), symbol=symbol, disk_tf=_disk_tf_count(tf, unit),
        disk_key=day_start, completed=completed, compute=_compute,
    )


def _day_columns_zp(
    symbol: Any, tf: Any, tf_sec: int, day_start: int, now_val: float,
    live_ticks: "list | None" = None,
) -> "tuple[float, list]":
    """src=zp の 1 カレンダー日 tf-period 列 ``(unit=GRID_W, columns)``。

    周期解像度は GRID_W セル（最小価格単位では帰無計数が退化するため）。levels の値は
    z（超過占有スコア）、levels は z>0 のセル＋POC セルのみ（sparse 維持）。帰無は
    :func:`market_profile_zp.null_b_period_moments`（1 回のサロゲート生成を周期カラム範囲で
    分割集計）。完了日はメモリ→ディスク（``<root>/<symbol>/<tf>/zp/<day>.json``）→計算、
    当日はキャッシュせず経過分までで都度計算する（既存 _day_columns と同規約）。
    """
    day_start = int(day_start)
    day_end = next_session_day_start(day_start)  # ISSUE-078。
    completed = day_end <= now_val

    def _compute() -> "tuple[float, list]":
        # ISSUE-094 🔴-2: 集計エンジン（z 統計直計算・周期分割・ライブ合成）は compute 層へ移送。
        return _tfc.day_columns_zp_compute(
            symbol, tf_sec, day_start, day_end, completed, now_val, live_ticks
        )

    return _DAY_CACHE.resolve(  # ISSUE-179 項目 B: 協調は単一実装。
        key=(symbol, tf, day_start, "zp"), symbol=symbol,
        disk_tf=_disk_tf_zp(tf),  # ISSUE-085/088 🔵-3 の世代規約はビルダが所有（ISSUE-172）。
        disk_key=day_start, completed=completed, compute=_compute,
    )


def _label_midnight(label: str) -> int:
    """バケットラベル 'YYYY-MM-DD' → バー time 規約値（ラベル日の UTC 深夜 epoch・1D と同規約）。"""
    y, m, d = (int(x) for x in str(label).split("-"))
    import datetime as _dtm

    return int(_dtm.datetime(y, m, d, tzinfo=_dtm.timezone.utc).timestamp())


def _bucket_completed(tf: str, label: str, now_val: float) -> bool:
    """バケット完了判定: 次バケット先頭セッションの始端が now 以前なら完了（不変＝キャッシュ可）。"""
    nxt_first = period_session_labels(tf, next_period_label(tf, label))[0]
    return session_label_to_start(nxt_first) <= now_val


def _bucket_columns(
    symbol: Any, tf: Any, label: str, now_val: float, live_ticks: "list | None" = None
) -> "tuple[float, list]":
    """1W/1M バケットの count 列（ISSUE-086）。

    セッション日次の 1D 列（:func:`_day_columns`＝既存の完了日キャッシュ経路を再利用）を
    バケットの全セッション日で加算合成する。levels は同一 GRID_W 格子ゆえ価格キーで加算でき、
    poc/va は合成カウントから再計算（:func:`_value_area_sparse`＝count 列と同一 VA 規約）。
    time はバケットラベル（W-FRI 金曜/ME 月末）の UTC 深夜＝rollup 1W/1M バーと同一規約。
    完了バケットは メモリ→ディスク→計算（＋保存）。当日を含むバケットは都度計算（ライブ育成）。
    """
    unit = float(_mpd.GRID_W)
    bar_time = _label_midnight(label)
    completed = _bucket_completed(tf, label, now_val)

    def _compute() -> "tuple[float, list]":
        # ISSUE-094 🔴-2: バケット合成エンジンは compute 層へ移送（1D 列取得は完了日キャッシュ経路を DIP 注入）。
        return _tfc.bucket_columns_compute(
            symbol, tf, label, bar_time, now_val, live_ticks, day_columns_fn=_day_columns
        )

    return _DAY_CACHE.resolve(  # ISSUE-179 項目 B: 協調は単一実装。
        key=(symbol, tf, bar_time), symbol=symbol,
        disk_tf=_disk_tf_bucket(tf, unit),  # ISSUE-172: 世代 s1 はビルダが所有（GC 記述子と同一式）。
        disk_key=bar_time, completed=completed, compute=_compute,
    )


def _bucket_columns_zp(
    symbol: Any, tf: Any, label: str, now_val: float, live_ticks: "list | None" = None
) -> "tuple[float, list]":
    """1W/1M バケットの zp 列（ISSUE-086）。

    z は加算不可のため、セッション日次の :class:`ZpRollup`（``obs``/``mean``/``var``・ISSUE-178 の
    不変 DTO。:func:`_zp_day_rollup`＝znull キャッシュ再利用・独立日ゆえモーメント加算可）を
    k 空間（絶対 log 格子＝日間で整列）で合成し、
    z = (Σobs − Σmean)/√Σvar を再計算する（compute_zp_profile の窓合成と同一規約）。
    levels/poc*/va/price 範囲は _day_columns_zp と同じ導出（levels=z>0＋POC セル・va=_value_area）。
    price_min/max は占有セル（Σobs>0）の格子境界（実 close との差は 1bp セル内）。
    完了バケットは メモリ→ディスク→計算（＋保存）。当日を含むバケットは都度計算。
    """
    bar_time = _label_midnight(label)
    completed = _bucket_completed(tf, label, now_val)

    def _compute() -> "tuple[float, list]":
        # ISSUE-094 🔴-2: バケット zp 合成エンジン（モーメント k 空間合成・z 再計算）は compute 層へ移送。
        return _tfc.bucket_columns_zp_compute(symbol, tf, label, bar_time, now_val, live_ticks)

    return _DAY_CACHE.resolve(  # ISSUE-179 項目 B: 協調は単一実装。
        key=(symbol, tf, bar_time, "zp"), symbol=symbol,
        disk_tf=_disk_tf_zp(tf),  # ISSUE-088 🔵-3 の世代規約はビルダが所有（ISSUE-172）。
        disk_key=bar_time, completed=completed, compute=_compute,
    )


def _parse_int(v: Any) -> "int | None":
    if isinstance(v, bool):
        return None
    if isinstance(v, int):
        return v
    if isinstance(v, str) and v.strip().lstrip("-").isdigit():
        return int(v.strip())
    return None


# src → 列生成関数・tf ゲート・unit フォールバックの記述子表（ISSUE-097 🔴-2）。
#   従来は src の分岐が handle_tf_period_profile 内 6 箇所（src 検証・zp の tf ゲート・bucket_fn・
#   bucket unit 既定・day_fn・day unit 既定）に散在し、3 つ目のソース追加時に全箇所の同期編集を
#   要した。本表を単一情報源とし、新ソース＝1 エントリ追加で閉じる（応答 byte 不変）。
#   キー None＝省略（最小価格単位カウント列）／"zp"＝超過占有 z(p)。allowed_tfs=None は tf ゲート無し。
#   unit フォールバック（空列時の既定 unit）は呼出時評価に合わせ callable で保持する。
_SRC_DESCRIPTORS: "dict[Any, dict]" = {
    None: {
        "day_fn": _day_columns,
        "bucket_fn": _bucket_columns,
        "allowed_tfs": None,
        "day_unit_fallback": lambda: 1.0,
        "bucket_unit_fallback": lambda: float(_mpd.GRID_W),
    },
    "zp": {
        "day_fn": _day_columns_zp,
        "bucket_fn": _bucket_columns_zp,
        "allowed_tfs": _ZP_TF_ALLOWED,
        "day_unit_fallback": lambda: float(_zp.GRID_W),
        "bucket_unit_fallback": lambda: round(60000.0 * (np.exp(_zp.W_LOG) - 1.0), 6),
    },
}
_SRC_MISSING = object()  # 未登録 src 判定用の番兵（None は正当なキーのため .get 既定に使えない）。


def handle_tf_period_profile(
    ref: Any, timeframe: Any, frm: Any, to: Any, now: "float | None" = None,
    src: Any = None, live_ticks: "list | None" = None,
) -> "tuple[int, dict]":
    """ローリング窓 ``[frm, to)`` の tf-period プロファイル列を返す（読取のみ）。

    ``now`` は完了窓判定（``to <= now`` のみキャッシュ)の基準時刻（既定は現在時刻・テスト注入用）。
    ``src``: None（既定）＝従来の最小価格単位カウント列（応答 byte 不変）。``"zp"``＝超過占有
    スコア z(p) 列（GRID_W セル解像度・levels 値は z・対応 tf は :data:`_ZP_TF_ALLOWED` のみ）。
    その他の値は 400。
    ``live_ticks``（ISSUE-083 追補）: served の in-memory LiveTickBuffer 末尾 ``[(unix_ms, mid)...]``。
    当日（未完了セッション）の計算にのみ parquet 優先 dedup で合成し、parquet フロンティア遅延
    （~1分）を待たず最新ティックを列へ反映する。完了日は無視（不変列のキャッシュを汚さない）。
    None/空は従来どおり（byte 不変）。
    """
    desc = _SRC_DESCRIPTORS.get(src, _SRC_MISSING)
    if desc is _SRC_MISSING:
        valid = "|".join("省略" if k is None else str(k) for k in _SRC_DESCRIPTORS)
        return _error_body("validation", f"未知の src です: {src!r}（{valid}）")
    if not _forming_bar.is_tick_ref(ref):
        return _error_body("validation", f"tick 由来 datasetRef ではありません: {ref!r}")
    # ISSUE-086: 1W/1M はセッション日次のロールアップ（バケット列）として対応する（旧: 400）。
    if not _forming_bar.is_supported_timeframe(timeframe) and timeframe not in _BUCKET_TFS:
        return _error_body("validation", f"非対応の timeframe です: {timeframe!r}")
    allowed_tfs = desc["allowed_tfs"]
    if allowed_tfs is not None and timeframe not in allowed_tfs:
        return _error_body(
            "validation",
            f"src={src} は tf {'|'.join(allowed_tfs)} のみ対応です: {timeframe!r}",
        )
    tf_sec = _TF_SECONDS.get(timeframe)
    if tf_sec is None and timeframe not in _BUCKET_TFS:
        return _error_body("validation", f"周期秒を解決できない timeframe です: {timeframe!r}")
    from_i, to_i = _parse_int(frm), _parse_int(to)
    if from_i is None or to_i is None or from_i >= to_i:
        return _error_body("validation", f"不正なローリング窓です [from,to)=({frm!r},{to!r})")

    symbol = _mpd.resolve_symbol(ref)
    now_val = _time.time() if now is None else float(now)

    # ISSUE-086: 1W/1M バケット走査（ラベル単位）。列 time はラベルの UTC 深夜＝rollup バーと同一。
    if timeframe in _BUCKET_TFS:
        bucket_fn = desc["bucket_fn"]
        columns_b: list = []
        units_b: list = []
        label = session_period_label(timeframe, from_i)
        while _label_midnight(label) < to_i:
            first_start = session_label_to_start(period_session_labels(timeframe, label)[0])
            if first_start >= now_val:
                break  # 未来バケット（未開始）。
            unit_d, cols_d = bucket_fn(symbol, timeframe, label, now_val, live_ticks=live_ticks)
            picked = [c for c in cols_d if from_i <= c["time"] < to_i]
            if picked:
                columns_b.extend(picked)
                units_b.append(unit_d)
            label = next_period_label(timeframe, label)
        unit_b = min(units_b) if units_b else desc["bucket_unit_fallback"]()
        return 200, {
            "ok": True,
            "tf": timeframe,
            "unit": round(unit_b, 6),
            "from": from_i,
            "to": to_i,
            "columns": columns_b,
        }

    # per-day 組み立て: 窓が跨るカレンダー日を走査し、各日の列（完了日=キャッシュ／当日=都度計算）を
    #   集めて窓 ``[from_i, to_i)`` に入る周期のみ採用する。列は日昇順・各日内も時刻昇順ゆえ結合で整列済み。
    #   応答 unit は寄与日の unit の最小（最も細かい解像度）を採る（描画のレベル行高の基準）。
    day_fn = desc["day_fn"]
    columns: list = []
    units: list = []
    day = session_day_start(from_i)  # ISSUE-078: セッション日ウォーク。
    while day < to_i:
        unit_d, cols_d = day_fn(symbol, timeframe, tf_sec, day, now_val, live_ticks=live_ticks)
        if cols_d:
            picked = [c for c in cols_d if from_i <= c["time"] < to_i]
            if picked:
                columns.extend(picked)
                units.append(unit_d)
        day = next_session_day_start(day)
    unit = min(units) if units else desc["day_unit_fallback"]()
    return 200, {
        "ok": True,
        "tf": timeframe,
        "unit": round(unit, 6),
        "from": from_i,
        "to": to_i,
        "columns": columns,
    }
