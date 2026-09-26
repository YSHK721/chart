"""dataset_registry 単一源からの 4 台帳導出の検証（ISSUE-094 🟡-9）。

DATASET_WHITELIST・_OUTLIER_CLAMP_REFS_SET・_ROLLUP_REFS（dataset）・TICK_REFS（tf_meta）が
記述子レジストリから導出され、従来の値・型・可変性（monkeypatch 用）が温存されることを固定する。
循環依存が無いことも確認する。
"""

from __future__ import annotations

from pathlib import Path

from marketdata import dataset, dataset_registry, tf_meta
from marketdata.paths import DATA_DIR


# --- 従来値の byte 不変（回帰の壁） --------------------------------------- #
def test_whitelist_values_unchanged():
    wl = dataset.DATASET_WHITELIST
    # ISSUE-511 段階 3 の段階 7a で spread 列つきの新系列 jp225_mt5_spread が、ISSUE-533
    # 段階 3 の前提工事で Dukascopy 側の対 jp225_tick_spread が加わった。
    assert set(wl) == {
        "sample", "jp225", "jp225_m1", "jp225_tick", "jp225_tick_spread",
        "jp225_mt5", "jp225_mt5_spread",
    }
    assert wl["jp225"] == DATA_DIR / "jp225_daily.csv"
    assert wl["jp225_m1"] == DATA_DIR / "jp225_m1.csv"
    # ISSUE-511 段階 1d: bid で作り直した保存物（旧 mid の jp225_tick_m1.csv は残置）。
    assert wl["jp225_tick"] == DATA_DIR / "jp225_tick_bid_m1.csv"
    assert wl["sample"].name == "ohlcv.csv"


def test_clamp_refs_unchanged():
    assert dataset._OUTLIER_CLAMP_REFS_SET == {
        "jp225": True, "jp225_m1": True, "jp225_tick": True, "jp225_mt5": True,
        # ISSUE-511 段階 3 の段階 7a: 実市場 ref ゆえクランプ対象（jp225_mt5 と同格）。
        "jp225_mt5_spread": True,
        # ISSUE-533 段階 3 の前提工事: 同じ理由で jp225_tick と同格。
        "jp225_tick_spread": True,
    }


def test_rollup_refs_unchanged():
    # 挿入順（ISSUE-511 段階 3 の段階 7a で jp225_mt5_spread を末尾へ、ISSUE-533 段階 3 の
    # 前提工事で jp225_tick_spread を対の base の直後へ追加）。
    assert dataset._ROLLUP_REFS == (
        "jp225_m1", "jp225_tick", "jp225_tick_spread", "jp225_mt5", "jp225_mt5_spread",
    )


def test_tick_refs_unchanged():
    # ISSUE-512 段階 3（承認 2026-09-13）で jp225_mt5 が、ISSUE-511 段階 3 の段階 7a で
    # jp225_mt5_spread が、ISSUE-533 段階 3 の前提工事で jp225_tick_spread が加わった
    # （spread 列は tick=True の記述子だけが宣言できる）。
    assert tf_meta.TICK_REFS == frozenset(
        {"jp225_tick", "jp225_tick_spread", "jp225_mt5", "jp225_mt5_spread"}
    )


# --- 型・可変性（利用側 monkeypatch・membership が無変更で動く） ---------- #
def test_derived_containers_have_expected_types():
    assert isinstance(dataset.DATASET_WHITELIST, dict)
    assert isinstance(dataset._OUTLIER_CLAMP_REFS_SET, dict)
    assert isinstance(dataset._ROLLUP_REFS, tuple)
    assert isinstance(tf_meta.TICK_REFS, frozenset)


def test_whitelist_and_clamp_are_independent_mutable_dicts():
    # registry を共有せず新規 dict（一方への setitem が他方/registry を汚さない）。
    assert dataset.DATASET_WHITELIST is not dataset._OUTLIER_CLAMP_REFS_SET
    dataset.DATASET_WHITELIST["_probe"] = Path("/tmp/x")
    try:
        assert "_probe" not in dataset._OUTLIER_CLAMP_REFS_SET
        assert "_probe" not in dataset_registry.REGISTRY
    finally:
        del dataset.DATASET_WHITELIST["_probe"]


# --- 単一源からの導出であること ------------------------------------------ #
def test_all_four_derive_from_registry():
    assert dataset.DATASET_WHITELIST == dataset_registry.whitelist()
    assert dataset._OUTLIER_CLAMP_REFS_SET == dataset_registry.clamp_refs()
    assert dataset._ROLLUP_REFS == dataset_registry.rollup_refs()
    assert tf_meta.TICK_REFS == dataset_registry.tick_refs()


# --- 循環禁止: registry は dataset / tf_meta を import しない（AST で実 import 文を検査） --- #
def test_registry_has_no_reverse_dependency():
    import ast
    import inspect

    tree = ast.parse(inspect.getsource(dataset_registry))
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                imported.add(node.module)
                imported.update(f"{node.module}.{a.name}" for a in node.names)
    assert not any("dataset" in m and m != "marketdata.dataset_registry" for m in imported), imported
    assert not any("tf_meta" in m for m in imported), imported
