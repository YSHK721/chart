"""adapter.compute.module_loader の並列契約テスト（ISSUE-185）。

背景（実測・ISSUE-185）:
    動的ロードは相対 import 解決のため ``sys.modules[name] = module`` を **exec 前**に
    登録する必要がある。旧実装は ``threading.Lock`` + 二重チェックを持っていたが、
    **高速経路がロックを取らずに ``sys.modules`` を素引きする**ため、未初期化の
    モジュールオブジェクトを掴んだ（3 スレッドで 2/3、16 スレッドで 15/16 が半構築観測）。
    掴んだ側は公開関数が未定義のため呼出時 ``AttributeError`` になり得る。

検証対象:
    - 並列同時ロードで **単一かつ完全初期化済み** のインスタンスのみが返ること
    - 実装が共有ローダ ``common.module_loader`` へ一本化されていること（重複解消）
    - ロード中モジュール本体からの入れ子ロードでデッドロックしないこと（RLock 性質）
    - キャッシュ挙動・exec 失敗時の残留挙動が従来どおりであること（挙動不変の担保）
"""
from __future__ import annotations

import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from adapter.compute import module_loader
from common import module_loader as common_module_loader

_API_ROOT = Path(__file__).resolve().parents[1]
_REPO_ROOT = Path(__file__).resolve().parents[4]

# 本体 exec に時間を要するモジュール（高速経路が半構築を観測しうる時間窓を作る）。
_SLOW_BODY = """
import time
time.sleep(0.2)
MARKER = "loaded"
def public_fn():
    return 42
"""

_SLOW_INIT = """
import time
time.sleep(0.2)
from .leaf import LEAF
MARKER = "loaded"
def public_fn():
    return LEAF
"""


@pytest.fixture
def slow_module(tmp_path: Path):
    path = tmp_path / "slow_mod.py"
    path.write_text(_SLOW_BODY, encoding="utf-8")
    name = "_issue185_slow_mod"
    yield name, path
    sys.modules.pop(name, None)


@pytest.fixture
def slow_package(tmp_path: Path):
    pkg = tmp_path / "slow_pkg"
    pkg.mkdir()
    (pkg / "leaf.py").write_text("LEAF = 42\n", encoding="utf-8")
    (pkg / "__init__.py").write_text(_SLOW_INIT, encoding="utf-8")
    name = "_issue185_slow_pkg"
    yield name, pkg
    for key in [k for k in sys.modules if k.startswith(name)]:
        sys.modules.pop(key, None)


def _concurrent(loader, name, path, n: int = 8):
    barrier = threading.Barrier(n)

    def task(_i):
        barrier.wait()
        mod = loader(name, path)
        # 半構築観測の検出は「返却時点」で行う（後で完成しても遅い）。
        return id(mod), getattr(mod, "MARKER", None), hasattr(mod, "public_fn")

    with ThreadPoolExecutor(max_workers=n) as ex:
        return list(ex.map(task, range(n)))


def test_delegates_to_common_shared_loader():
    # ISSUE-185: 同一関心の 2 実装を共有ローダへ一本化する（片方だけ直る状態を作らない）。
    assert module_loader.load_package is common_module_loader.load_package
    assert module_loader.load_module is common_module_loader.load_module


def test_load_package_concurrent_returns_single_initialized_instance(slow_package):
    results = _concurrent(module_loader.load_package, *slow_package)
    assert len({r[0] for r in results}) == 1, "重複ロード（複数インスタンス）が発生した"
    assert all(r[1] == "loaded" for r in results), "半構築モジュールを観測した"
    assert all(r[2] for r in results), "公開関数未定義のモジュールを観測した"


def test_load_module_concurrent_returns_single_initialized_instance(slow_module):
    results = _concurrent(module_loader.load_module, *slow_module)
    assert len({r[0] for r in results}) == 1, "重複ロード（複数インスタンス）が発生した"
    assert all(r[1] == "loaded" for r in results), "半構築モジュールを観測した"
    assert all(r[2] for r in results), "公開関数未定義のモジュールを観測した"


def test_nested_load_from_module_body_does_not_deadlock(tmp_path):
    # 入れ子ロード（ロード中モジュール本体が本ローダを再帰呼び出し）でハングしないこと。
    #   非再入ロック実装では自己デッドロックするため、別スレッド + join(timeout) で判定する。
    (tmp_path / "inner.py").write_text("VALUE = 7\n", encoding="utf-8")
    (tmp_path / "outer.py").write_text(
        "import sys\n"
        f"sys.path.insert(0, {str(_REPO_ROOT)!r})\n"
        f"sys.path.insert(0, {str(_API_ROOT)!r})\n"
        "from pathlib import Path\n"
        "from adapter.compute import module_loader\n"
        f"INNER = module_loader.load_module('_issue185_inner', Path({str(tmp_path / 'inner.py')!r}))\n"
        "VALUE = INNER.VALUE\n",
        encoding="utf-8",
    )
    box: dict[str, object] = {}

    def run():
        box["outer"] = module_loader.load_module("_issue185_outer", tmp_path / "outer.py")

    worker = threading.Thread(target=run, daemon=True)
    try:
        worker.start()
        worker.join(timeout=10.0)
        assert not worker.is_alive(), "入れ子ロードでデッドロックした"
        assert box["outer"].VALUE == 7  # type: ignore[union-attr]
    finally:
        sys.modules.pop("_issue185_outer", None)
        sys.modules.pop("_issue185_inner", None)


def test_load_module_caches_in_sys_modules(slow_module):
    # 挙動不変の担保: sys.modules を唯一のキャッシュとし、2 回目は再 exec しない。
    name, path = slow_module
    first = module_loader.load_module(name, path)
    assert sys.modules[name] is first
    assert module_loader.load_module(name, path) is first


def test_load_package_caches_in_sys_modules(slow_package):
    # 挙動不変の担保: 相対 import 解決 + sys.modules キャッシュ。
    name, pkg = slow_package
    first = module_loader.load_package(name, pkg)
    assert sys.modules[name] is first
    assert first.public_fn() == 42
    assert module_loader.load_package(name, pkg) is first


def test_failed_exec_removes_module_from_sys_modules(tmp_path):
    """exec 失敗時は sys.modules から除去し、次回も同じ例外を出す（ISSUE-219）。

    **本テストは意図的な挙動変更である。** ISSUE-185（ローダ一本化）の時点では
    「exec 失敗時も除去しない」という当時の既存挙動を不変として固定していたが、
    その挙動自体が欠陥であることが実測で判明した:

        1 回目: RuntimeError  →  2 回目: 例外なしで VALUE=1（途中まで定義された壊れた
        モジュール）を配布。CPython 標準の import は失敗時に sys.modules から削除する。

    指標 src の動的ロードで src に実行時例外があると、初回だけエラーになり以降は
    一部だけ定義されたモジュールで計算が進む＝**沈黙した誤計算**になりうるため是正した。
    ISSUE-185 が担保した他の不変条件（相対 import 解決・成功時キャッシュ・並行安全）は
    本ファイルの他テストで維持されている。
    """
    path = tmp_path / "boom.py"
    path.write_text("VALUE = 1\nraise RuntimeError('boom')\nVALUE = 2\n", encoding="utf-8")
    name = "_issue219_boom"
    try:
        with pytest.raises(RuntimeError, match="boom"):
            module_loader.load_module(name, path)
        assert name not in sys.modules, "半構築モジュールが残っている"
        # 2 回目も同じ例外（壊れたモジュールを黙って返さない）。
        with pytest.raises(RuntimeError, match="boom"):
            module_loader.load_module(name, path)
    finally:
        sys.modules.pop(name, None)


def test_adapter_loader_delegates_to_common(tmp_path):
    """adapter 側は common への再エクスポートであり、修正が両経路へ届く（ISSUE-185 の一本化）。"""
    assert module_loader.load_module is common_module_loader.load_module
    assert module_loader.load_package is common_module_loader.load_package
