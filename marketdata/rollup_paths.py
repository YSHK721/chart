"""marketdata.rollup_paths — ロールアップ保存配置の**単一権威**（ISSUE-502 D-16）。

配置の形は 2 つある。どちらも本モジュールだけが組み立てる:

  - フラット配置   ``<DATA_DIR>/rollups/<ref>_<tf>.csv``
    （jp225_m1 の既存配置。進捗 state ファイルも rollups 直下に置く）
  - ref 専用配置   ``<DATA_DIR>/rollups/<ref>/<ref>_<tf>.csv``
    （jp225_tick / jp225_mt5。進捗 state のファイル名が ref 非依存の固定名であるため、
    CSV だけでなく state まで物理分離しないと既存 ref の state を上書き破壊する）

読み手（:func:`resolve_csv`）は **当該 CSV ファイルの実在**でレイアウトを選ぶ。判定基準を
「サブ dir の存在」ではなく「ファイルの存在」にするのは、空・作りかけの ``rollups/<ref>/`` が
フラット CSV を無言で shadow して既存 ref を壊す事故を避けるため（判定規則も本所が持つ）。

なぜ分けたか（SRP）:
    「派生物をどこへ置くか」を変える理由（ref ごとの隔離・保存先の再編）と、「1 分足をどう畳むか」
    を変える理由（resample 規則・増分の刻み）は一致しない。分離前は同じ配置規則を
    marketdata 3 箇所・tools 2 箇所・indigators 3 箇所の計 8 箇所が各自組み立てており、権威が
    どこにも無かった（ISSUE-502 台帳 D-16）。

この「単一権威」は宣言ではなく検定が強制する:
``marketdata/tests/test_rollup_layout_authority.py`` がリポジトリを走査し、本モジュール以外が
配置リテラル（``"rollups"`` / ``f"..._{tf}.csv"``）を組んでいたら落ちる。
:mod:`marketdata.tick_tree`（tick 木レイアウトの権威）と同じ様式である。

依存方向（厳守）: 標準ライブラリと :mod:`marketdata.paths`（物理基点の唯一源）のみに依存する。
ロールアップの生成・読取（:mod:`marketdata.rollup` / :mod:`marketdata.rollup_store`）へは依存
しない＝権威が利用者へ逆流しない。この宣言は
``marketdata/tests/test_module_dependency_declarations.py`` が AST 走査で強制する。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from marketdata.paths import DATA_DIR

#: ロールアップ格納ディレクトリ名（配置の語彙。この綴りを持ってよいのは本モジュールだけ）。
ROLLUPS_DIRNAME = "rollups"


def rollups_root(data_dir: Any = DATA_DIR) -> Path:
    """ロールアップ格納の基点（``<data_dir>/rollups``）。"""
    return Path(data_dir) / ROLLUPS_DIRNAME


def ref_dir(ref: str, *, data_dir: Any = DATA_DIR) -> Path:
    """``ref`` 専用のロールアップ出力ディレクトリ（``<data_dir>/rollups/<ref>``）。

    進捗 state のファイル名は ref 非依存の固定名であるため、ref ごとに dir を切って state まで
    物理分離する（同一 dir へ書くと既存 ref の state を上書き破壊する）。
    """
    return rollups_root(data_dir) / ref


def csv_name(ref_prefix: str, tf: str) -> str:
    """ロールアップ CSV のファイル名（``<ref_prefix>_<tf>.csv``）。"""
    return f"{ref_prefix}_{tf}.csv"


def csv_path(out_dir: Any, ref_prefix: str, tf: str) -> Path:
    """``out_dir`` 配下のロールアップ CSV パス（書き手が出力先 dir を決めたあとの解決点）。"""
    return Path(out_dir) / csv_name(ref_prefix, tf)


def resolve_csv(ref: str, tf: str, *, root: Any = None, data_dir: Any = DATA_DIR) -> Path:
    """読み手向けの解決（**当該 CSV の実在**で 2 レイアウトを選ぶ）。

    ref 専用配置 ``<root>/<ref>/<ref>_<tf>.csv`` に当該 tf の CSV が実在すればそれを返し、
    無ければフラット配置 ``<root>/<ref>_<tf>.csv`` を返す。両配置に無ければフラットパスを
    返す（不在の扱いは呼び出し側 1 箇所に閉じる）。

    ``root`` は基点を明示注入したいとき（テストの差し替え等）に使う。既定は
    :func:`rollups_root` ``(data_dir)``。
    """
    root_path = rollups_root(data_dir) if root is None else Path(root)
    subdir_csv = root_path / ref / csv_name(ref, tf)
    if subdir_csv.is_file():
        return subdir_csv
    return root_path / csv_name(ref, tf)


__all__ = [
    "ROLLUPS_DIRNAME",
    "rollups_root",
    "ref_dir",
    "csv_name",
    "csv_path",
    "resolve_csv",
]
