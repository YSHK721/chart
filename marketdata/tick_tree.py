"""marketdata.tick_tree — 日別ティック parquet の木レイアウトの**単一権威**（ISSUE-479 M-2）。

木の形は ``<DATA_DIR>/ticks/YYYY/MM/DD/<symbol>_ticks.parquet`` であり、取得側（tools の常駐
watch・アーカイブ取込）と読取側（M1 素材化・市場プロファイル・リプレイ）が同じ 1 箇所から
解決する。レイアウトを変えるときに触るべき箇所が 1 つであることを、宣言ではなく検定
（``marketdata/tests/test_tick_tree_layout_authority.py``）が repo 走査で強制する。

なぜ :mod:`marketdata.tick_m1` から分けたか:
    木の形を変える理由（保存先の再編・銘柄トークンの命名）と、ticks→M1 の集計規則を変える理由
    （価格基準・清掃・重複畳み）は一致しない。同居していた間、木の権威を参照したいだけの
    44 ファイル 164 箇所が、素材化の実装まで抱えたモジュールに依存していた。分離後も
    :mod:`marketdata.tick_m1` が本モジュールの関数を**同一オブジェクトのまま再輸出**するため、
    既存の参照は 1 箇所も変わらない（``tick_m1.day_parquet_path`` の monkeypatch も効き続ける）。

依存方向: 本モジュールは pandas と :mod:`marketdata.paths`（物理基点の唯一源）のみに依存し、
:mod:`marketdata.tick_m1` を逆 import しない（権威が素材化へ依存しない）。この宣言は
``marketdata/tests/test_module_dependency_declarations.py`` が AST 走査で強制する。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, List

import pandas as pd

from marketdata.paths import DATA_DIR

# 既定の銘柄トークン（tools/build_tick_rollup.py と一致）。木のファイル名の語彙であり本所が持つ。
_DEFAULT_SYMBOL = "JP225"


def tick_root(data_dir: Any = DATA_DIR) -> Path:
    """ティック parquet の基点（``<DATA_DIR>/ticks``）。"""
    return Path(data_dir) / "ticks"


def day_parquet_path(day: Any, *, symbol: str = _DEFAULT_SYMBOL, data_dir: Any = DATA_DIR) -> Path:
    """``day`` の日別ティック parquet の正準パスを返す（実在チェックはしない）。

    tick tree レイアウト ``<DATA_DIR>/ticks/YYYY/MM/DD/<symbol>_ticks.parquet`` の単一権威
    （reader: :func:`day_parquet_files` / writer: tools.live_tick_watch が共用し、レイアウト
    変更を本所 1 箇所に閉じる）。

    この「単一権威」は ``marketdata/tests/test_tick_tree_layout_authority.py`` が
    **リポジトリ走査で強制**する（ISSUE-262）。かつて宣言だけがあり、実際は tools 3 本と
    replay adapter がレイアウトを自前で組んでいた。
    """
    d = pd.Timestamp(day)
    return (
        tick_root(data_dir)
        / f"{d.year:04d}" / f"{d.month:02d}" / f"{d.day:02d}"
        / f"{symbol}_ticks.parquet"
    )


def day_empty_marker_path(day: Any, *, symbol: str = _DEFAULT_SYMBOL,
                          data_dir: Any = DATA_DIR) -> Path:
    """``day`` の「取得したがティック 0 件」マーカー（``<symbol>_ticks.empty``）の正準パス。

    parquet と同じ tick tree に属するため、レイアウト権威は本モジュールに閉じる（ISSUE-262）。
    かつて ``.empty`` の名前は 4 箇所（tools 2・simulator 1・with_suffix 導出 1）に散っていた。
    """
    return day_parquet_path(day, symbol=symbol, data_dir=data_dir).with_suffix(".empty")


def day_parquet_name(symbol: str = _DEFAULT_SYMBOL) -> str:
    """日別ティック parquet のファイル名（tick tree レイアウトの一部・単一権威）。"""
    return f"{symbol}_ticks.parquet"


def day_parquet_files(
    start: Any, end: Any, *, symbol: str = _DEFAULT_SYMBOL, data_dir: Any = DATA_DIR
) -> List[Path]:
    """``[start, end]``（両端含む・日次）の実在する日別ティック parquet を昇順で列挙する。

    パスは :func:`day_parquet_path`（レイアウト単一権威）で解決し、実在するものだけ
    返す（欠損日はスキップ・休場日対応）。
    """
    s, e = pd.Timestamp(start), pd.Timestamp(end)
    out: List[Path] = []
    d = s
    while d <= e:
        p = day_parquet_path(d, symbol=symbol, data_dir=data_dir)
        if p.is_file():
            out.append(p)
        d += pd.Timedelta(days=1)
    return out
