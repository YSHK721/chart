"""rollup_store — 上位足ロールアップ CSV の解決・末尾読込・mtime キャッシュ（dataset と同方式）。

server が 1 分足を全ロードしないための読み取り側。上位足（5m..1M）はあらかじめ生成された
TF 別ロールアップ CSV（``DATA_DIR/rollups/<ref>_<tf>.csv``・loader 互換）を読む。
パスは marketdata.paths.DATA_DIR（単一基点・Sd §10.1 C-1）配下に集約する。

★メモリ・読込時間有界（D-2 と同方針）: ロールアップ全件（5m≈96 万行/64MB）を読まず、末尾
``_ROLLUP_TAIL_ROWS`` 行だけを ``tail_reader.read_tail`` で逆シーク読みする。表示・計算は
recentBars（1500 本）以内のため十分で、全件読み（1.1s/145MB）→末尾読み（~0.18s/16MB）へ短縮し
server の応答時間・常駐 RSS を抑える（1m の ``dataset._ATOMIC_TAIL_LOOKBACK_ROWS`` と同方式・同値）。

mtime キャッシュ（plain dict 上書き有界）と torn-read フォールバックを ``dataset._BASE_CACHE`` と
同方式で持つ（単一真実源・恒久 stale 化しない）。★dataset の P-1/P-2 と同型: (ref, tf) ごと最新
mtime の 1 エントリのみ保持（mtime ごと増殖しない）。ロールアップ書込は原子的（os.replace）だが、
読込失敗時は失敗をキャッシュへ焼かず直前の良好 df を返す（防御）。
"""

from __future__ import annotations

import logging
from pathlib import Path

import pandas as pd

logger = logging.getLogger(__name__)

from adapter.compute import tail_reader

# workspace ルート（このファイル: api/adapter/compute/ → parents[5] = /workspaces/app）。
_WORKSPACE_ROOT = Path(__file__).resolve().parents[5]
# 時系列データの単一基点（marketdata.paths.DATA_DIR・Sd §10.1 C-1）を import するため
# repo 根を sys.path へ（call_binding と同じロード境界の一括設定）。
import sys as _sys

if str(_WORKSPACE_ROOT) not in _sys.path:
    _sys.path.insert(0, str(_WORKSPACE_ROOT))
from marketdata.paths import DATA_DIR

_ROLLUPS_DIR = DATA_DIR / "rollups"

# 末尾読込の上限行数（全件を読まず末尾だけ逆シーク。recentBars=1500 に対し十分大。1m の
#   dataset._ATOMIC_TAIL_LOOKBACK_ROWS と同方式・同値）。遡及上限＝この行数（5m≈170 日・1h≈5.7 年）。
_ROLLUP_TAIL_ROWS = 50_000

# ロールアップ読込の mtime 検知キャッシュ（dataset._BASE_CACHE と同方式・有界）。
#   (ref, tf) → (mtime_ns, DataFrame)。(ref,tf) ごと最新 mtime の 1 エントリのみ保持する
#   （旧 mtime は上書きで消える＝plain dict 上書き有界）。
_ROLLUP_CACHE: dict[tuple[str, str], tuple[int | None, pd.DataFrame]] = {}


def path(ref: str, tf: str) -> Path:
    """ロールアップ CSV の解決パス（**当該 CSV ファイルの存在**でレイアウトを選ぶ）。

    ref 専用サブディレクトリ配置 ``DATA_DIR/rollups/<ref>/<ref>_<tf>.csv`` に**当該 tf の CSV が
    実在すれば**それを返す（``build_tick_rollup.py`` が ``rollup_state.json`` 衝突回避のため ref ごと
    隔離する配置・例 ``rollups/jp225_tick/jp225_tick_5m.csv``）。無ければ従来のフラット配置
    ``DATA_DIR/rollups/<ref>_<tf>.csv``（``jp225_m1`` 等の既存）を返す。

    判定基準を「サブdir の存在」ではなく「**ファイルの存在**」にするのは、空/作りかけの
    ``rollups/<ref>/`` がフラット CSV を無言で shadow して既存 ref を壊す事故（部分生成・誤生成）を
    避けるため。両配置に無ければフラットパスを返す（``read`` 側が不在を 1 箇所で扱う）。
    """
    subdir_csv = _ROLLUPS_DIR / ref / f"{ref}_{tf}.csv"
    if subdir_csv.is_file():
        return subdir_csv
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
    try:
        df = _read_tail_df(csv_path)
    except (OSError, ValueError, pd.errors.ParserError, pd.errors.EmptyDataError):
        # 読込失敗（torn-read 等）時は失敗をキャッシュへ焼かず、直前の良好 df があればそれを
        # 返す（不正データを配信しない）。無ければ送出する。
        if cached is not None:
            logger.warning("ロールアップ CSV 読込に失敗（torn-read 等）。直前のキャッシュを維持: %s/%s", ref, tf)
            return cached[1]
        raise
    _ROLLUP_CACHE[key] = (_csv_mtime(csv_path), df)
    return df


def _read_tail_df(csv_path: Path) -> pd.DataFrame:
    """ロールアップ CSV の末尾 ``_ROLLUP_TAIL_ROWS`` 行だけを逆シークで読む（全件読みしない）。

    ``tail_reader.read_tail`` は ``date`` を datetime index へ解決し loader 互換の
    ``open/high/low/close/volume`` 列を返す（全件 loader 読みと末尾域で index/値一致）。
    """
    return tail_reader.read_tail(csv_path, _ROLLUP_TAIL_ROWS)
