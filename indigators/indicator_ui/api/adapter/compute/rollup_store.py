"""rollup_store — 上位足ロールアップ CSV の解決・読込・mtime キャッシュ（dataset と同方式）。

server が 1 分足を全ロードしないための読み取り側。上位足（5m..1M）はあらかじめ生成された
TF 別ロールアップ CSV（``<workspace>/marketdata/data/rollups/<ref>_<tf>.csv``・loader 互換）を
読む。既存 loader を再利用し、mtime キャッシュ（plain dict 上書き有界）と torn-read フォールバックを
``dataset._BASE_CACHE`` と同方式で持つ（単一真実源・恒久 stale 化しない）。

★dataset の P-1/P-2 と同型: (ref, tf) ごと最新 mtime の 1 エントリのみ保持（mtime ごと増殖しない）。
torn-read（writer の非アトミック書込途中）時は失敗をキャッシュへ焼かず直前の良好 df を返す。
"""

from __future__ import annotations

import logging
from functools import lru_cache
from pathlib import Path

import pandas as pd

logger = logging.getLogger(__name__)

from adapter.compute.module_loader import load_module

# workspace ルート（このファイル: api/adapter/compute/ → parents[5] = /workspaces/app）。
_WORKSPACE_ROOT = Path(__file__).resolve().parents[5]
_ROLLUPS_DIR = _WORKSPACE_ROOT / "marketdata" / "data" / "rollups"

# ロールアップ CSV の時刻列（loader が index へ解決する起点）。
_TIME_COLUMN = "date"

# ロールアップ読込の mtime 検知キャッシュ（dataset._BASE_CACHE と同方式・有界）。
#   (ref, tf) → (mtime_ns, DataFrame)。(ref,tf) ごと最新 mtime の 1 エントリのみ保持する
#   （旧 mtime は上書きで消える＝plain dict 上書き有界）。
_ROLLUP_CACHE: dict[tuple[str, str], tuple[int | None, pd.DataFrame]] = {}


def path(ref: str, tf: str) -> Path:
    """ロールアップ CSV の解決パス（``<workspace>/marketdata/data/rollups/<ref>_<tf>.csv``）。"""
    return _ROLLUPS_DIR / f"{ref}_{tf}.csv"


def _csv_mtime(csv_path: Path) -> int | None:
    """ロールアップ CSV の最終更新時刻（ns・整数）。存在しなければ None。"""
    try:
        return csv_path.stat().st_mtime_ns
    except OSError:
        return None


def read(ref: str, tf: str) -> pd.DataFrame:
    """上位足ロールアップ CSV を DataFrame 化して返す（mtime キャッシュ + torn-read フォールバック）。

    既存 loader を再利用し date を index へ解決する。CSV の mtime が前回と同一ならキャッシュを返す。
    mtime 変化（CSV 上書き）時は再読込して当該 (ref,tf) の 1 エントリを置換する（有界）。
    torn-read（解析失敗）時は失敗をキャッシュへ焼かず直前の良好 df を返す（無ければ送出）。
    """
    csv_path = path(ref, tf)
    mtime = _csv_mtime(csv_path)
    key = (ref, tf)
    cached = _ROLLUP_CACHE.get(key)
    if cached is not None and (mtime is None or mtime == cached[0]):
        # mtime 不変、または取得不能（CSV 削除）ならキャッシュヒット（再読込しない）。
        return cached[1]
    loader = _load_loader()
    try:
        df = loader.load_ohlc_csv(str(csv_path), time_column=_TIME_COLUMN)
    except (OSError, ValueError, pd.errors.ParserError, pd.errors.EmptyDataError):
        # writer が非アトミックに書込中だと torn-read で解析失敗しうる。失敗をキャッシュへ
        # 焼かず、直前の良好 df があればそれを返す（不正データを配信しない）。無ければ送出する。
        if cached is not None:
            logger.warning("ロールアップ CSV 読込に失敗（torn-read 等）。直前のキャッシュを維持: %s/%s", ref, tf)
            return cached[1]
        raise
    _ROLLUP_CACHE[key] = (_csv_mtime(csv_path), df)
    return df


@lru_cache(maxsize=None)
def _load_loader():
    """指標 src の loader モジュールを一意名で読み込む（read-only・dataset と同じ loader）。"""
    pkg_dir = _WORKSPACE_ROOT / "indigators" / "profit_band" / "src"
    return load_module("_rollup_store_loader_src", pkg_dir / "loader.py")
