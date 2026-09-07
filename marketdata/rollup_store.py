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

依存方向（厳守）: pandas + 標準ライブラリ + marketdata（tail_reader / rollup_paths）のみに依存し、
indicator_ui / simulator / MP / indigators を一切 import しない（marketdata は最下層・逆依存ゼロ）。
"""

from __future__ import annotations

import logging
from pathlib import Path

import pandas as pd

logger = logging.getLogger(__name__)

from marketdata import rollup_paths, tail_reader

# workspace ルート（このファイル: marketdata/ → parents[1] = /workspaces/app）。
_WORKSPACE_ROOT = Path(__file__).resolve().parents[1]

# ロールアップ格納の基点。配置規則の唯一の所有者は marketdata.rollup_paths（ISSUE-502 D-16）で
#   あり、本モジュールは解決結果を保持するだけ（``"rollups"`` の綴りを持たない）。
#   モジュール属性として持つのは、利用側が基点だけを差し替えられるようにするため。
_ROLLUPS_DIR = rollup_paths.rollups_root()

# 末尾読込の上限行数（全件を読まず末尾だけ逆シーク。recentBars=1500 に対し十分大）。
#   値の唯一の定義は marketdata.tail_reader.SERVING_TAIL_ROWS（ISSUE-502 D-10）。かつては
#   1m 原子（dataset）と本所が同じ数値を各自に持ち、コメントで「同値」と人手同期していた。
#   遡及上限＝この行数（5m≈170 日・1h≈5.7 年）。
_ROLLUP_TAIL_ROWS = tail_reader.SERVING_TAIL_ROWS

# ロールアップ読込の mtime 検知キャッシュ（dataset._BASE_CACHE と同方式・有界）。
#   (ref, tf) → (mtime_ns, DataFrame)。(ref,tf) ごと最新 mtime の 1 エントリのみ保持する
#   （旧 mtime は上書きで消える＝plain dict 上書き有界）。
_ROLLUP_CACHE: dict[tuple[str, str], tuple[int | None, pd.DataFrame]] = {}


def path(ref: str, tf: str) -> Path:
    """ロールアップ CSV の解決パス（配置権威 :func:`marketdata.rollup_paths.resolve_csv` へ委譲）。

    レイアウト（2 配置とその選び方＝**当該 CSV ファイルの存在**で選ぶ）は
    :mod:`marketdata.rollup_paths` が唯一所有する。本関数は基点 :data:`_ROLLUPS_DIR` を渡して
    解決させるだけで、配置の綴りを持たない（ISSUE-502 D-16）。
    """
    return rollup_paths.resolve_csv(ref, tf, root=_ROLLUPS_DIR)


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
    if cached is not None and mtime is not None and mtime == cached[0]:
        # mtime 不変ならキャッシュヒット（再読込しない）。
        # ISSUE-278 #5: 取得不能（CSV 削除）を「不変」に含めない（含めると削除に気付かず
        #   古い断面を無期限配信する）。serving_cache と同一規律。
        return cached[1]
    try:
        df = _read_tail_df(csv_path)
    except FileNotFoundError:
        raise   # 素材消失は torn-read ではない＝古い断面を配信せず落とす（fail-fast）。
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

    ``tail_reader.read_tail`` は date 列を datetime index へ解決し loader 互換の
    ``open/high/low/close/volume`` 列を返す（全件 loader 読みと末尾域で index/値一致）。
    """
    return tail_reader.read_tail(csv_path, _ROLLUP_TAIL_ROWS)
