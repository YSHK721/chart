"""common.module_loader のテスト（ロック付き動的ロードの契約・ISSUE-176）。

検証対象:
    - ``load_package`` / ``load_module`` が ``sys.modules`` を唯一のキャッシュとすること
    - 並列同時ロードで **単一かつ完全初期化済み** のインスタンスのみが返ること
      （ロック不在の実装では、exec 前に ``sys.modules`` へ登録するため他スレッドが
       半構築モジュールを観測する＝ISSUE-176 影響 2 の実障害）
"""
from __future__ import annotations

import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from common import module_loader  # noqa: E402

# 本体 exec に時間を要するモジュール（ロック不在なら他スレッドが半構築を観測する）。
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
    name = "_issue176_slow_mod"
    yield name, path
    sys.modules.pop(name, None)


@pytest.fixture
def slow_package(tmp_path: Path):
    pkg = tmp_path / "slow_pkg"
    pkg.mkdir()
    (pkg / "leaf.py").write_text("LEAF = 42\n", encoding="utf-8")
    (pkg / "__init__.py").write_text(_SLOW_INIT, encoding="utf-8")
    name = "_issue176_slow_pkg"
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


def test_load_module_has_serializing_lock():
    # ISSUE-156 対策と同一機構（並列 compute の初回同時ロード直列化）を持つこと。
    assert module_loader._LOAD_LOCK is not None
    assert hasattr(module_loader._LOAD_LOCK, "acquire")


def test_nested_load_from_module_body_does_not_deadlock(tmp_path):
    # ロード中モジュールの本体が本ローダを再帰呼び出ししてもデッドロックしないこと
    #   （ロック導入で新たなハングを生まない＝挙動不変の担保）。
    (tmp_path / "inner.py").write_text("VALUE = 7\n", encoding="utf-8")
    (tmp_path / "outer.py").write_text(
        "import sys\n"
        f"sys.path.insert(0, {str(Path(__file__).resolve().parents[2])!r})\n"
        "from pathlib import Path\n"
        "from common import module_loader\n"
        f"INNER = module_loader.load_module('_issue176_inner', Path({str(tmp_path / 'inner.py')!r}))\n"
        "VALUE = INNER.VALUE\n",
        encoding="utf-8",
    )
    try:
        outer = module_loader.load_module("_issue176_outer", tmp_path / "outer.py")
        assert outer.VALUE == 7
    finally:
        sys.modules.pop("_issue176_outer", None)
        sys.modules.pop("_issue176_inner", None)


def test_load_module_caches_in_sys_modules(slow_module):
    name, path = slow_module
    first = module_loader.load_module(name, path)
    assert sys.modules[name] is first
    assert module_loader.load_module(name, path) is first  # 再 exec しない


def test_load_package_caches_in_sys_modules(slow_package):
    name, pkg = slow_package
    first = module_loader.load_package(name, pkg)
    assert sys.modules[name] is first
    assert first.public_fn() == 42  # 相対 import が解決している
    assert module_loader.load_package(name, pkg) is first


def test_load_module_concurrent_returns_single_initialized_instance(slow_module):
    name, path = slow_module
    results = _concurrent(module_loader.load_module, name, path)
    assert len({r[0] for r in results}) == 1, "重複ロード（複数インスタンス）が発生した"
    assert all(r[1] == "loaded" for r in results), "半構築モジュールを観測した"
    assert all(r[2] for r in results)


def test_load_package_concurrent_returns_single_initialized_instance(slow_package):
    name, pkg = slow_package
    results = _concurrent(module_loader.load_package, name, pkg)
    assert len({r[0] for r in results}) == 1, "重複ロード（複数インスタンス）が発生した"
    assert all(r[1] == "loaded" for r in results), "半構築モジュールを観測した"
    assert all(r[2] for r in results)

# --- ISSUE-219: exec 失敗時のキャッシュ汚染 -------------------------------------


def _write_module(tmp_path, body: str):
    path = tmp_path / "boom.py"
    path.write_text(body, encoding="utf-8")
    return path


def test_exec_failure_does_not_cache_half_built_module(tmp_path):
    """exec が失敗したモジュールを sys.modules へ残さない（ISSUE-219）。

    残すと ``_cached_ready`` が「exec 完了済み」と誤判定し、2 回目以降が**例外を出さずに
    途中まで定義された壊れたモジュールを配布する**。CPython 標準の import は失敗時に
    ``sys.modules`` から削除しており、本実装もそれに合わせる。
    """
    name = "_issue219_probe_a"
    path = _write_module(tmp_path, "VALUE = 1\nraise RuntimeError('exec 失敗')\nVALUE = 2\n")
    try:
        with pytest.raises(RuntimeError):
            module_loader.load_module(name, path)
        assert name not in sys.modules, "半構築モジュールが sys.modules に残っている"

        # 2 回目も同じ例外になること（黙って壊れた値を返さない）。
        with pytest.raises(RuntimeError):
            module_loader.load_module(name, path)
    finally:
        sys.modules.pop(name, None)


def test_exec_failure_leaves_loading_flag_cleared(tmp_path):
    """失敗しても _LOADING は残らない（後続の判定を汚さない）。"""
    name = "_issue219_probe_b"
    path = _write_module(tmp_path, "raise ValueError('boom')\n")
    try:
        with pytest.raises(ValueError):
            module_loader.load_module(name, path)
        assert name not in module_loader._LOADING
    finally:
        sys.modules.pop(name, None)


def test_successful_load_is_still_cached(tmp_path):
    """成功したモジュールは従来どおりキャッシュされ再 exec されない（非波及）。"""
    name = "_issue219_probe_c"
    path = _write_module(tmp_path, "COUNTER = []\nCOUNTER.append(1)\nVALUE = 42\n")
    try:
        first = module_loader.load_module(name, path)
        second = module_loader.load_module(name, path)
        assert first is second, "2 回目に再 exec されている"
        assert first.VALUE == 42
        assert len(first.COUNTER) == 1
    finally:
        sys.modules.pop(name, None)
