"""serving_cache — 供給時 mtime キャッシュ＋ロールアップ経路の単一定義（ISSUE-094 🟡-7）。

:mod:`marketdata.dataset` に凝集していた 3 アクター（品質＝クランプ / 台帳＝ref 解決＋JSON 整形 /
性能＝mtime キャッシュ＋ロールアップ経路分岐）のうち、**性能アクター**（mtime 検知キャッシュと
1m/上位足のロールアップ経路分岐）を本モジュールへ分離する。dataset はこれらの IO/キャッシュ
プリミティブを呼ぶオーケストレータへ縮退する。

不変条件（厳守・回帰の壁）:
    「キャッシュには生（未クランプ）を保存し、返却時にクランプする」。本モジュールが返すのは常に
    生の DataFrame（外れ値クランプは dataset 側 serving 戦略が最終返却前に一様適用する）。

キャッシュ設計（先行修正の非回帰）:
    - base（CSV 読込）: ref → (mtime_ns, df)。ref ごと最新 mtime の 1 エントリのみ（上書き有界）。
      torn-read（writer の非アトミック追記中の解析失敗）は直前良好 df へフォールバックし、失敗を
      焼かない（不正データを配信しない・🟡-1）。良好キャッシュが無ければ送出する。
    - resample: (ref, tf) → (mtime, df)。キー mtime は **base が実際に焼いた世代 mtime**
      （:func:`baked_mtime`）を単一真実源にする（P-1: _csv_mtime 独立呼びは torn-read 時に恒久
      stale 化する）。(ref,tf) ごと最新 1 エントリのみ（P-2: plain dict 上書き＝有界・LRU 不使用）。

依存注入（重要・利用側 monkeypatch 温存）:
    CSV ローダ生成（``loader_factory``）と resample 関数（``resample_fn``）は dataset から注入する。
    これにより ``monkeypatch.setattr(dataset, "_load_loader"/"resample_ohlc", ...)`` が本モジュール
    経由でも有効に働く（テストは呼び出し時の dataset 名前空間を差し替える）。

依存方向: pandas ＋ :mod:`marketdata.tail_reader` / :mod:`marketdata.rollup_store` のみに依存し、
dataset を逆 import しない（循環禁止）。tail_reader / rollup_store はモジュール属性として呼ぶため、
利用側の ``monkeypatch.setattr(dataset.tail_reader, "read_tail", ...)``（同一モジュールオブジェクト）
がそのまま反映される。
"""

from __future__ import annotations

import logging
import threading
from pathlib import Path
from typing import Any, Callable

import pandas as pd

from marketdata import rollup_store, tail_reader

logger = logging.getLogger(__name__)

# CSV mtime 検知キャッシュ（base の1段）。ref → (mtime_ns, DataFrame)。
_BASE_CACHE: "dict[str, tuple[int, pd.DataFrame]]" = {}

# resample 結果キャッシュ（load_dataframe の1段）。(ref, tf) → (mtime, resampled_df)。
_RESAMPLE_CACHE: "dict[tuple[str, str | None], tuple[int | None, pd.DataFrame]]" = {}

# ISSUE-156（A）: 供給キャッシュの直列化ロック（計算プール並列時の重複ビルド・torn-read 防止）。
#   粗粒度 RLock（本モジュールの公開 3 関数全体を包む）。キャッシュヒットはメモリ参照のみで
#   軽く、ミス時の重い CSV 読込/resample は従来どおり実質直列化される（多重ビルド防止）。
_CACHE_LOCK = threading.RLock()


def csv_mtime(path: Path) -> "int | None":
    """実 CSV の最終更新時刻（ns・整数）を返す（キャッシュキー）。取得不能は None。"""
    try:
        return path.stat().st_mtime_ns
    except OSError:
        return None


def baked_mtime(ref: str) -> "int | None":
    """base が実際に焼いた世代 mtime（``_BASE_CACHE[ref][0]``）を返す（無ければ None）。

    ★P-1: resample キャッシュのキー mtime の単一真実源。torn-read 時に base の据え置き世代と
    乖離させないため、base が保持する世代 mtime をそのまま使う（内部表現依存を局所化する）。
    """
    cached = _BASE_CACHE.get(ref)
    return cached[0] if cached is not None else None


def _load_base_dataframe_unlocked(
    ref: str,
    *,
    path: Path,
    loader_factory: Callable[[], Any],
    time_column: str,
) -> pd.DataFrame:
    """原子 CSV を DataFrame 化する（resample 前・mtime キャッシュ＋torn-read フォールバック）。

    CSV の mtime が前回と同一ならキャッシュ DataFrame を返す。mtime 変化（CSV 上書き）時は
    ``loader_factory()`` が返すローダで再読込し、当該 ref の 1 エントリを置換する（旧 mtime は
    保持しない＝有界）。解析失敗（torn-read 等）は直前良好 df へフォールバックし、失敗を焼かない。
    """
    mtime = csv_mtime(path)
    cached = _BASE_CACHE.get(ref)
    if cached is not None and mtime is not None and mtime == cached[0]:
        # mtime 不変ならキャッシュヒット（再読込しない）。
        # ISSUE-278 #5: 取得不能（CSV 削除・マウント断）を「不変」に含めてはならない。含めると
        #   配信プロセスが削除に気付かず、削除時点の断面を無期限に配信し続ける（再起動でしか
        #   復旧しない・ログも出ない）。取得不能は下の再読込へ落とし、FileNotFoundError で落とす。
        return cached[1]
    loader = loader_factory()
    try:
        df = loader.load_ohlc_csv(str(path), time_column=time_column)
    except FileNotFoundError:
        # 素材そのものが消えている＝torn-read（追記中の一過性）ではない。古い断面を配信せず落とす
        #   （marketdata/paths.py と同じ fail-fast 方針＝誤った既定への暗黙退行を防ぐ）。
        raise
    except (OSError, ValueError, pd.errors.ParserError, pd.errors.EmptyDataError):
        # ライブ更新の writer が CSV を非アトミックに追記中だと末尾行が torn-read になり
        # pandas が解析失敗しうる（🟡-1）。失敗をキャッシュへ焼かず、直前の良好 df を返す。
        # 良好キャッシュが無ければ送出する（隠蔽しない）。
        if cached is not None:
            logger.warning("CSV 読込に失敗（torn-read 等）。直前のキャッシュを維持: %s", ref)
            return cached[1]
        raise
    _BASE_CACHE[ref] = (csv_mtime(path), df)
    return df


def _resolve_rollup_dataframe_unlocked(
    ref: str,
    timeframe: "str | None",
    *,
    path: Path,
    atomic_tail_rows: int,
) -> pd.DataFrame:
    """ロールアップ経路 ref の生 DataFrame を返す（メモリ有界化・D-2）。

    - 1m（None/'1m'）: 末尾安全上限ぶんを ``tail_reader.read_tail`` で逆シーク読み（全件 tail で
      OOM を復活させない有限値）。
    - 上位足（5m..1M）: 事前生成のロールアップ CSV を ``rollup_store.read`` で読む
      （mtime キャッシュ＋torn-read フォールバックは rollup_store 側）。
    """
    if timeframe in (None, "1m"):
        return tail_reader.read_tail(path, atomic_tail_rows)
    return rollup_store.read(ref, timeframe)


def _resample_cached_unlocked(
    ref: str,
    timeframe: "str | None",
    base: pd.DataFrame,
    *,
    resample_fn: Callable[[pd.DataFrame, "str | None"], pd.DataFrame],
    rule: "str | None",
) -> pd.DataFrame:
    """base を指定 rule で resample し、(ref, tf)・base 世代 mtime をキーにキャッシュする（生を保存）。

    ★P-1: キー mtime は :func:`baked_mtime`（base が焼いた世代）。★P-2: (ref,tf) ごと最新 1
    エントリのみ（plain dict 上書き＝有界）。ヒット時は生の resample 結果を返す（クランプは呼出側）。
    """
    mtime = baked_mtime(ref)
    key = (ref, timeframe)
    cached = _RESAMPLE_CACHE.get(key)
    if cached is not None and (mtime is None or mtime == cached[0]):
        # mtime 不変、または取得不能（CSV 削除）なら直前の resample 結果を返す（再 resample しない）。
        return cached[1]
    resampled = resample_fn(base, rule)
    _RESAMPLE_CACHE[key] = (mtime, resampled)
    return resampled




def load_base_dataframe(*args, **kwargs):
    """ロック付き公開ラッパ（ISSUE-156・挙動不変）。実体は ``_load_base_dataframe_unlocked``。"""
    with _CACHE_LOCK:
        return _load_base_dataframe_unlocked(*args, **kwargs)


def resolve_rollup_dataframe(*args, **kwargs):
    """ロック付き公開ラッパ（ISSUE-156・挙動不変）。実体は ``_resolve_rollup_dataframe_unlocked``。"""
    with _CACHE_LOCK:
        return _resolve_rollup_dataframe_unlocked(*args, **kwargs)


def resample_cached(*args, **kwargs):
    """ロック付き公開ラッパ（ISSUE-156・挙動不変）。実体は ``_resample_cached_unlocked``。"""
    with _CACHE_LOCK:
        return _resample_cached_unlocked(*args, **kwargs)

__all__ = [
    "csv_mtime",
    "baked_mtime",
    "load_base_dataframe",
    "resolve_rollup_dataframe",
    "resample_cached",
]
