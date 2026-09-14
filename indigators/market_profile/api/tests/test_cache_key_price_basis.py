"""MP ディスクキャッシュの鍵に **価格基準** を含める（ISSUE-511 段階 1c）。

用語: 価格基準＝ティックの bid/ask のどちらを価格とするか（``"mid"`` / ``"bid"``・ref ごとの値は
marketdata/dataset_registry.py の ``price_basis``。MP は木の枝名で読むため、同ファイルの
「木の枝名 → 基準」の窓口から引く）。

なぜ必要か（ISSUE-511 段階 1 の前提・実測 2026-09-14）:
    MP の 4 系統のキャッシュ（dwell / zp の mgrid・znull / tf-period）の鍵は木の枝名・版数・格子・日で、
    価格基準を含まない。段階 1d で jp225_tick を mid → bid へ切り替えると、同じ枝名（JP225）の
    **mid で計算したキャッシュが bid の系列として配られる**。出力は形式上正しい（数値が揃う）ため
    状態検証では落ちない。鍵に基準を含め、基準が違えば別の置き場になることを固定する。

配置: 基準の segment は木の枝名の **上** に置く（``<root>/price-<basis>/<tree>/...``）。
    既存キャッシュの引き継ぎが「``<root>/<tree>`` → ``<root>/price-<basis>/<tree>``」の名前変更 1 回で
    済み、戻すのも名前変更 1 回である（tools/migrate_mp_cache_price_basis.py）。

構造: Arrange-Act-Assert（AAA）。
"""
from __future__ import annotations

from pathlib import Path

import pytest

from market_profile_api.gateway.dwell_rollup_store import DwellRollupStore
from market_profile_api.gateway.zp_store import ZpStore
from market_profile_api.gateway import tf_period_disk_cache as tf_disk

_DAY = 1339621200
_TREE = "JP225"


def _dwell(tmp_path, price_basis_of):
    return DwellRollupStore(
        root_provider=lambda: tmp_path, default_root_provider=lambda: tmp_path,
        grid_w=1.0, cache_version_provider=lambda: 4,
        day_parquet_files=lambda *a, **k: [], price_basis_of=price_basis_of,
    )


def _zp(tmp_path, price_basis_of):
    return ZpStore(
        root_provider=lambda: tmp_path, default_root_provider=lambda: tmp_path,
        grid_w=5.0, hist_days=250, m_reps=2000, cache_version_provider=lambda: 3,
        day_parquet_files=lambda *a, **k: [], price_basis_of=price_basis_of,
    )


def _paths(tmp_path, basis):
    """4 系統の書込パス（同じ木・同じ日）を、基準 ``basis`` の台帳で作る。"""
    of = {_TREE: basis}.__getitem__
    zp = _zp(tmp_path, of)
    return {
        "dwell": _dwell(tmp_path, of).cache_path(_TREE, _DAY),
        "mgrid": zp.mgrid_path(_TREE, _DAY),
        "znull": zp.null_path(_TREE, _DAY),
        "tf-period": tf_disk.day_disk_path(tmp_path, _TREE, "1h/s1/g1", _DAY, price_basis=basis),
    }


# --------------------------------------------------------------------------- #
# 1. 鍵に基準が入る（木の枝名の上）
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("system", ["dwell", "mgrid", "znull", "tf-period"])
def test_the_basis_segment_sits_right_above_the_tree(tmp_path, system):
    """4 系統とも ``price-<basis>`` の segment が木の枝名の直上にある。"""
    # Arrange / Act
    parts = _paths(tmp_path, "mid")[system].parts

    # Assert
    at = parts.index(_TREE)
    assert parts[at - 1] == "price-mid", f"{system}: {parts}"


@pytest.mark.parametrize("system", ["dwell", "mgrid", "znull", "tf-period"])
def test_the_same_tree_under_another_basis_lands_elsewhere(tmp_path, system):
    """同じ木・同じ日でも、基準が違えば置き場が違う（mid のキャッシュを bid として配らない）。"""
    # Arrange / Act
    mid, bid = _paths(tmp_path, "mid")[system], _paths(tmp_path, "bid")[system]

    # Assert
    assert mid != bid


# --------------------------------------------------------------------------- #
# 2. GC の不変条件（世代 dir の階層が実書込パスと一致する）
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("make, path_of", [
    (_dwell, lambda s: s.cache_path(_TREE, _DAY)),
    (_zp, lambda s: s.null_path(_TREE, _DAY)),
])
def test_the_gc_generation_segment_still_matches_the_write_path(tmp_path, make, path_of):
    """記述子の gen_depth が基準の segment を数に入れている（使用中の世代を孤児にしない）。"""
    # Arrange
    store = make(tmp_path, {_TREE: "mid"}.__getitem__)
    lay = store.layout()

    # Act
    parts = Path(path_of(store)).relative_to(Path(lay.root)).parts

    # Assert
    assert parts[lay.gen_depth - 1] in lay.current, (lay.gen_depth, parts, lay.current)


def test_the_layout_does_not_look_up_a_basis(tmp_path):
    """GC の記述子は基準を引かない（木を名指さずに世代名だけを出す）。"""
    # Arrange
    def refuse(tree):  # noqa: ANN001
        raise AssertionError(f"記述子の組み立てで基準を引いた: {tree!r}")

    # Act
    dwell, znull = _dwell(tmp_path, refuse).layout(), _zp(tmp_path, refuse).layout()

    # Assert（基準を引かずに世代名が出る）
    assert dwell.current == frozenset({"v4"})
    assert znull.current == frozenset({"b5"})


# --------------------------------------------------------------------------- #
# 3. 計算量（基準の解決数 − 組み立てたパス数 = 0・日数 2 点）
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("days", [1, 5])
def test_each_path_resolves_the_basis_exactly_once(tmp_path, days):
    """パス 1 本につき基準の解決は 1 回（余分に引かない・日数 1/5 の 2 点で固定）。"""
    # Arrange
    calls = []

    def counting(tree):  # noqa: ANN001
        calls.append(tree)
        return "mid"

    store = _dwell(tmp_path, counting)

    # Act
    for day in range(days):
        store.cache_path(_TREE, _DAY + day * 86400)

    # Assert
    assert len(calls) - days == 0, f"{days} 本のパスで基準を {len(calls)} 回解決した"
