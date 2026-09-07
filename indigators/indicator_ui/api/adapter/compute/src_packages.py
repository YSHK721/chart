"""SRC_PACKAGES — 指標 src パッケージのロード境界（call_binding から分離・ISSUE-502 段階 4B）。

責務は 1 つだけである: **指標名 → 指標 src パッケージ（および その公開 callable）** の解決。
3 指標はいずれも top-level パッケージ名 ``src`` を使うため ``import src`` では同名衝突して
1 つしか読めない。本モジュールは各指標 src を **ファイルパスから一意なパッケージ名で
読み込む**（既存 src は read-only・改変しない）。描画ライブラリは import しない。

分離の理由（SRP）: 従来は本機構が指標記述子表と同じファイル（call_binding.py）に同居しており、
「表」と「モジュールロード境界」という変更軸の異なる 2 つが 1 ファイルに載っていた。ロード規約
（sys.path の解決点・一意名の付け方）が変わる動機と、指標が 1 件増える動機は独立である。

ISSUE-087 🟡-3: repo 根 / MP api の解決は venv の .pth（tools/install_dev_paths.py）が担う。
ISSUE-174: 兄弟パッケージ層（indigators 直下の moving_averages / mql_builtins / profit_system）
  の解決点は本ロード境界（``_ensure_indigators_on_path``）に一本化されている。各 src の
  sys.path.insert は撤去済み。
"""

from __future__ import annotations

import importlib
import sys
from pathlib import Path
from types import ModuleType
from typing import Callable

from adapter.compute.module_loader import load_package

#: indigators/ ルート（このファイル: api/adapter/compute/ → parents[4] = indigators/）。
INDIGATORS_ROOT = Path(__file__).resolve().parents[4]

# 一意パッケージ名の接頭辞／接尾辞。3 指標が共通 top-level 名 ``src`` を使うため、各指標を
# ``_<indicator>_src`` という衝突しない名前で sys.modules へ登録する（同名 src 回避）。
_SRC_MODULE_PREFIX = "_"
_SRC_MODULE_SUFFIX = "_src"


def _src_module_name(indicator: str) -> str:
    """指標名から一意なパッケージ名（``_<indicator>_src``）を組み立てる。"""
    return f"{_SRC_MODULE_PREFIX}{indicator}{_SRC_MODULE_SUFFIX}"


def _ensure_indigators_on_path() -> None:
    """``indigators/`` を sys.path へ 1 回だけ登録する（ISSUE-174・冪等）。

    指標 src は兄弟パッケージ（indigators 直下の moving_averages / mql_builtins / profit_system）
    を top-level 名で import する。その解決点を **ロード境界であるここ 1 か所**に置き、各 src の
    最内層に散っていた ``sys.path.insert``（13 本）を撤去する。既に登録済みなら何もしない。
    """
    path = str(INDIGATORS_ROOT)
    if path not in sys.path:
        sys.path.insert(0, path)


def load_src_package(indicator: str) -> ModuleType:
    """指標 src パッケージを一意なパッケージ名で読み込む（同名 ``src`` 衝突を回避）。

    importlib 機構は ``module_loader.load_package`` に集約（重複解消・振る舞い不変）。
    一意名 ``_<indicator>_src`` を与え、相対 import（``from .bands import``）と
    sys.modules キャッシュは load_package が担保する。

    exec 前に ``indigators/`` を sys.path へ載せる（src 内の兄弟パッケージ絶対 import の解決点）。
    """
    _ensure_indigators_on_path()
    return load_package(_src_module_name(indicator), INDIGATORS_ROOT / indicator / "src")


def load_callable(indicator: str, attr: str) -> Callable:
    """指標 src の lwc_chart から add_* を取り出す（read-only）。"""
    src = load_src_package(indicator)
    lwc = importlib.import_module(src.__name__ + ".lwc_chart")
    return getattr(lwc, attr)


def indicator_src(indicator: str) -> ModuleType:
    """指標 src パッケージを一意名で読み込んで返す（read-only・無改変参照）。

    増分器（``adapter.compute.incremental``）が指標 src の **公開関数**（``*_on_buffer`` や
    窓末尾 OLS など）を呼ぶための唯一の入口。ロード機構（同名 ``src`` 衝突の
    回避・sys.path の解決点）を本モジュールへ閉じ込め、増分器側へ importlib を散らさない。
    """
    return load_src_package(indicator)
