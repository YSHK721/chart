"""ISSUE-179（market_profile 項目 B）: tf-period キャッシュ協調の単一情報源ガード。

**実測（変更前 ``controller/tf_period_profile_controller.py:202-406``）**: 「完了なら メモリ →
ディスク → 計算（＋両層へ保存）／未完了なら都度計算」という per-entry 協調が 4 メソッド
（``_day_columns`` / ``_day_columns_zp`` / ``_bucket_columns`` / ``_bucket_columns_zp``）へ
逐語複製されていた。複製部は 20 行 ×4（メモリ LRU 参照・``move_to_end``・ディスク照合・
LRU トリム・保存）で、差分は 5 点（エントリキー・ディスク世代 subdir・ディスク側キー・
完了判定式・計算本体）のみ。

**是正**: 協調を :class:`TfPeriodDayCache` へ抽出し、差分はパラメータ＋計算戦略（0 引数
callable）で渡す。抽出は「状態も一緒に移す」形で行い、メモリ LRU（辞書・上限）は協働子が
所有する（host の private フィールドへ協働子が代入する分割不全を作らない）。ディスク側は
``DayCacheDiskPort``（協働子が所有する Output Boundary）へ依存し、``_TFP_CACHE_ROOT`` の
call-time 解決（テストの monkeypatch アンカー）は controller 側に残る。
"""
from __future__ import annotations

import ast
import inspect
from pathlib import Path

import pytest

import market_profile_api.controller.tf_period_profile_controller as ctl
from market_profile_api.controller.tf_period_cache import TfPeriodDayCache

_PKG = Path(__file__).resolve().parents[1] / "market_profile_api"
_CTL_SRC = (_PKG / "controller" / "tf_period_profile_controller.py").read_text(encoding="utf-8")
_CACHE_SRC = (_PKG / "controller" / "tf_period_cache.py").read_text(encoding="utf-8")
_CACHE_AST = ast.parse(_CACHE_SRC)


def _imported_modules(tree: ast.AST) -> "set[str]":
    """モジュールが import する対象名（docstring・コメントは対象外＝実依存のみ）。"""
    names: "set[str]" = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom):
            names.add(node.module or "")
    return names


def _referenced_symbols(tree: ast.AST) -> "set[str]":
    """コード上で参照される識別子（docstring・コメントは対象外）。"""
    return {
        node.id if isinstance(node, ast.Name) else node.attr
        for node in ast.walk(tree)
        if isinstance(node, (ast.Name, ast.Attribute))
    }


class _SpyDisk:
    """テスト用ディスク口（``DayCacheDiskPort`` 実装）。呼出履歴を記録する。"""

    def __init__(self, present: "dict | None" = None):
        self.present = dict(present or {})
        self.loads: list = []
        self.saves: list = []

    def load(self, symbol, disk_tf, disk_key):
        self.loads.append((symbol, disk_tf, disk_key))
        return self.present.get((symbol, disk_tf, disk_key))

    def save(self, symbol, disk_tf, disk_key, unit, columns):
        self.saves.append((symbol, disk_tf, disk_key, unit, columns))


def _counter(value):
    calls = []

    def _compute():
        calls.append(1)
        return value

    return _compute, calls


# --------------------------------------------------------------------------- #
# 1. 状態所有（「状態も一緒に移す」抽出であること）
# --------------------------------------------------------------------------- #
def test_cache_owns_its_memory_state():
    """メモリ LRU は協働子インスタンスが所有する（インスタンス間で共有されない）。"""
    a = TfPeriodDayCache(_SpyDisk(), max_entries=4)
    b = TfPeriodDayCache(_SpyDisk(), max_entries=4)
    fn, _ = _counter((1.0, ["x"]))
    a.resolve(key="k", symbol="S", disk_tf="tf", disk_key=1, completed=True, compute=fn)
    assert a.memory_size() == 1
    assert b.memory_size() == 0


def test_controller_does_not_own_the_cache_state_anymore():
    """host（controller）に生の LRU 辞書は残らない（分割不全＝状態が host に残る形の禁止）。"""
    assert not hasattr(ctl, "_DAY_MEM")
    assert not hasattr(ctl, "_DAY_MEM_MAX")
    assert isinstance(ctl._DAY_CACHE, TfPeriodDayCache)


def test_collaborator_does_not_reach_back_into_the_host():
    """協働子は host を import しない（依存は host → 協働子の一方向）。"""
    assert not any("tf_period_profile_controller" in m for m in _imported_modules(_CACHE_AST))
    assert not any("controller" in m for m in _imported_modules(_CACHE_AST))


# --------------------------------------------------------------------------- #
# 2. 協調規則（変更前 4 メソッドの逐語仕様）
# --------------------------------------------------------------------------- #
def test_completed_entry_is_computed_once_then_served_from_memory():
    disk = _SpyDisk()
    c = TfPeriodDayCache(disk, max_entries=8)
    fn, calls = _counter((10.0, ["c"]))
    args = dict(key="k", symbol="S", disk_tf="tf/s1/g10", disk_key=7, completed=True)
    first = c.resolve(**args, compute=fn)
    second = c.resolve(**args, compute=fn)
    assert first == second == (10.0, ["c"])
    assert len(calls) == 1                     # 2 回目はメモリヒット
    assert len(disk.loads) == 1                # メモリヒット時はディスクを引かない
    assert disk.saves == [("S", "tf/s1/g10", 7, 10.0, ["c"])]


def test_disk_hit_populates_memory_and_skips_compute():
    disk = _SpyDisk({("S", "tf", 7): (2.0, ["d"])})
    c = TfPeriodDayCache(disk, max_entries=8)
    fn, calls = _counter((99.0, ["never"]))
    got = c.resolve(key="k", symbol="S", disk_tf="tf", disk_key=7, completed=True, compute=fn)
    assert got == (2.0, ["d"])
    assert calls == []                         # 計算しない
    assert disk.saves == []                    # 読んだものを書き戻さない
    assert c.memory_size() == 1                # メモリへ載る
    assert c.resolve(key="k", symbol="S", disk_tf="tf", disk_key=7, completed=True,
                     compute=fn) == (2.0, ["d"])
    assert len(disk.loads) == 1                # 2 回目はメモリヒット


def test_incomplete_entry_is_never_cached():
    """未完了エントリはメモリ・ディスクとも読まず・書かず、毎回計算する。"""
    disk = _SpyDisk({("S", "tf", 7): (2.0, ["stale"])})
    c = TfPeriodDayCache(disk, max_entries=8)
    fn, calls = _counter((3.0, ["live"]))
    args = dict(key="k", symbol="S", disk_tf="tf", disk_key=7, completed=False)
    assert c.resolve(**args, compute=fn) == (3.0, ["live"])
    assert c.resolve(**args, compute=fn) == (3.0, ["live"])
    assert len(calls) == 2
    assert disk.loads == [] and disk.saves == []
    assert c.memory_size() == 0


def test_memory_is_bounded_and_evicts_least_recently_used():
    disk = _SpyDisk()
    c = TfPeriodDayCache(disk, max_entries=2)
    for k in ("a", "b"):
        fn, _ = _counter((1.0, [k]))
        c.resolve(key=k, symbol="S", disk_tf="tf", disk_key=k, completed=True, compute=fn)
    fn_a, calls_a = _counter((1.0, ["a"]))
    c.resolve(key="a", symbol="S", disk_tf="tf", disk_key="a", completed=True, compute=fn_a)
    assert calls_a == []                       # a はヒット（＝最近使用へ移動）
    fn_c, _ = _counter((1.0, ["c"]))
    c.resolve(key="c", symbol="S", disk_tf="tf", disk_key="c", completed=True, compute=fn_c)
    assert c.memory_size() == 2
    assert c.memory_keys() == ("a", "c")       # 追い出されるのは最も古い b


def test_memory_store_precedes_disk_save():
    """保存順序は「メモリ → ディスク」（変更前 4 メソッドと同順）。"""
    order: list = []

    class _OrderDisk(_SpyDisk):
        def save(self, *a):
            order.append("disk")
            super().save(*a)

    c = TfPeriodDayCache(_OrderDisk(), max_entries=8)

    def _compute():
        order.append("compute")
        return (1.0, ["x"])

    c.resolve(key="k", symbol="S", disk_tf="tf", disk_key=1, completed=True, compute=_compute)
    assert order == ["compute", "disk"]
    assert c.memory_size() == 1


def test_clear_empties_memory_only():
    disk = _SpyDisk()
    c = TfPeriodDayCache(disk, max_entries=8)
    fn, _ = _counter((1.0, ["x"]))
    c.resolve(key="k", symbol="S", disk_tf="tf", disk_key=1, completed=True, compute=fn)
    c.clear()
    assert c.memory_size() == 0
    assert len(disk.saves) == 1                # ディスクは触らない


# --------------------------------------------------------------------------- #
# 3. host 側の単一化（4 メソッドの逐語複製が消えていること）
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("name", [
    "_day_columns", "_day_columns_zp", "_bucket_columns", "_bucket_columns_zp",
])
def test_each_method_delegates_to_the_single_coordinator(name):
    src = inspect.getsource(getattr(ctl, name))
    assert "_DAY_CACHE.resolve(" in src
    for banned in ("move_to_end", "popitem", "_load_day_disk(", "_save_day_disk("):
        assert banned not in src, f"{name} に協調の複製が残っている: {banned}"


def test_coordination_primitives_appear_only_in_the_collaborator():
    """LRU 操作は協働子のみが行う（controller 本体には残らない）。"""
    assert "move_to_end" not in _CTL_SRC
    assert "popitem" not in _CTL_SRC
    assert "OrderedDict" not in _CTL_SRC
    assert _CACHE_SRC.count("move_to_end") >= 1


def test_disk_root_stays_a_call_time_controller_concern(monkeypatch, tmp_path):
    """``_TFP_CACHE_ROOT`` の monkeypatch アンカーは controller 側に残る（協働子は root を知らない）。"""
    assert "_TFP_CACHE_ROOT" not in _referenced_symbols(_CACHE_AST)
    monkeypatch.setattr(ctl, "_TFP_CACHE_ROOT", False)
    assert ctl._DAY_CACHE._disk.load("S", "tf", 1) is None  # 無効時は None（例外にしない）
    monkeypatch.setattr(ctl, "_TFP_CACHE_ROOT", str(tmp_path))
    ctl._DAY_CACHE._disk.save("S", "tf", 1, 10.0, [{"time": 1, "levels": []}])
    assert ctl._DAY_CACHE._disk.load("S", "tf", 1) == (10.0, [{"time": 1, "levels": []}])
