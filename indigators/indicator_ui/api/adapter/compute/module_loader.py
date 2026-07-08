"""module_loader — 指標 src（top-level 名 ``src`` 同名衝突）を一意名で読み込む共通機構。

3 指標はいずれも top-level パッケージ名 ``src`` を使うため ``import src`` では 1 つしか
読めない。本モジュールは「ファイルパスから一意なモジュール名で exec する」共通手順を
1 か所へ集約する（call_binding の ``_load_src_package`` と controller の ``_load_loader`` が
個別に持っていた importlib 機構の重複を解消する・振る舞い不変）。

- ``load_package(name, pkg_dir)``  : ``__init__.py`` を持つパッケージを一意名で読み込む
  （相対 import ``from .bands import`` が解決するよう submodule_search_locations を設定）。
- ``load_module(name, file_path)`` : 単一の .py ファイルをモジュールとして読み込む。

いずれも ``sys.modules`` を唯一のキャッシュとし、同名の 2 回目以降は再 exec しない。
既存指標 src は read-only（改変しない）。描画ライブラリは import しない。
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType


def load_package(name: str, pkg_dir: Path) -> ModuleType:
    """``pkg_dir/__init__.py`` を一意名 ``name`` で読み込む（相対 import を解決可能にする）。

    キャッシュ: ``sys.modules[name]`` が存在すれば再 exec せず返す。
    """
    cached = sys.modules.get(name)
    if cached is not None:
        return cached

    spec = importlib.util.spec_from_file_location(
        name,
        pkg_dir / "__init__.py",
        submodule_search_locations=[str(pkg_dir)],
    )
    if spec is None or spec.loader is None:  # pragma: no cover - 環境異常（spec 解決不能）
        raise ImportError(f"パッケージを読み込めません: {name} ({pkg_dir})")
    module = importlib.util.module_from_spec(spec)
    # exec 前に登録する（src 内の相対 import が自モジュールを参照できるように）。
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def load_module(name: str, file_path: Path) -> ModuleType:
    """単一の .py ファイルを一意名 ``name`` のモジュールとして読み込む。

    キャッシュ: ``sys.modules[name]`` が存在すれば再 exec せず返す。
    """
    cached = sys.modules.get(name)
    if cached is not None:
        return cached

    spec = importlib.util.spec_from_file_location(name, file_path)
    if spec is None or spec.loader is None:  # pragma: no cover - 環境異常（spec 解決不能）
        raise ImportError(f"モジュールを読み込めません: {name} ({file_path})")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module
