"""統計核の 3 分割（ブート核 / VaR 被覆検定 / SPA）を固定する（ISSUE-479 Wave2 C-3）。

背景: `common.stats_boot` が「定常ブートストラップ核」「VaR 被覆検定（Kupiec・
Christoffersen）」「Hansen SPA」の 3 責務を 1 モジュールに抱えていた。3 者は変更を要求する
アクターが異なり、被覆検定はブートストラップを一切使わない（χ² と対数尤度だけ）。

本ファイルが固定するもの:
    1. 各クラスが期待するモジュールに 1 件だけ定義されること（common 配下の全走査）
    2. 分割元に後方互換の再エクスポートを置かないこと（第 2 の入口を作らない）
    3. 分割前の出力と float の bit 一致（固定 seed）
    4. SPA のリサンプル発行が B に比例し、系列長 n では増えないこと（計算量）
"""
from __future__ import annotations

import ast
import importlib
import pathlib

import numpy as np
import pytest

stats_boot = importlib.import_module("common.stats_boot")

_PKG = pathlib.Path(stats_boot.__file__).parent

#: 分割後にクラスが属するべきモジュールファイル名。
_EXPECTED_HOME = {
    "VarBacktests": "var_backtests.py",
    "HansenSpa": "hansen_spa.py",
}

#: 分割元に残してはならない名前（後方互換の再エクスポート禁止）。
_MUST_NOT_REMAIN = ("VarBacktests", "HansenSpa", "chi2_sf_df1", "_xlogx_term")

#: 分割元に残るブート核（7 関数）。
_BOOTSTRAP_CORE = (
    "norm_cdf", "bootstrap_std", "flat_top_weight", "autocorr",
    "pw_block_len_one", "pw_block_len", "stationary_bootstrap_indices",
)


def _top_level_classes(path: pathlib.Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    return {node.name for node in tree.body if isinstance(node, ast.ClassDef)}


@pytest.mark.parametrize(("class_name", "home"), sorted(_EXPECTED_HOME.items()))
def test_each_class_is_defined_exactly_once_in_its_own_module(class_name: str, home: str) -> None:
    """クラス定義が common 配下で 1 件、かつ期待モジュールにあること。"""
    # Arrange / Act
    homes = sorted(
        path.name for path in sorted(_PKG.glob("*.py"))
        if class_name in _top_level_classes(path)
    )

    # Assert
    assert homes == [home], f"{class_name} の定義位置: {homes}（期待 {[home]}）"


@pytest.mark.parametrize("name", _MUST_NOT_REMAIN)
def test_the_origin_module_does_not_reexport_the_moved_names(name: str) -> None:
    """分割元に後方互換の再エクスポートを置かない（入口を 2 つ作らない）。"""
    assert not hasattr(stats_boot, name), f"分割元に {name} が残っている（再エクスポート禁止）"


@pytest.mark.parametrize("name", _BOOTSTRAP_CORE)
def test_the_bootstrap_core_stays_in_the_origin_module(name: str) -> None:
    """ブート核 7 関数は分割元に残る（過剰な移設をしていないこと）。"""
    assert hasattr(stats_boot, name)


def test_var_backtests_reproduces_the_frozen_values() -> None:
    """VaR 被覆検定が分割前と float bit 一致（固定 seed）。"""
    # Arrange
    var_backtests = importlib.import_module("common.var_backtests")
    rng = np.random.default_rng(2026)
    hits = [int(x) for x in (rng.random(300) < 0.07)]
    backtests = var_backtests.VarBacktests()

    # Act / Assert
    assert backtests.kupiec(hits, 0.05) == 1.0
    assert backtests.kupiec(hits, 0.01) == 6.443936787576083e-07
    assert backtests.christoffersen_independence(hits) == 0.2080068710842753
    assert backtests.kupiec([], 0.05) == 1.0
    assert backtests.christoffersen_independence([]) == 1.0
    assert var_backtests.chi2_sf_df1(3.84) == 0.05004352124870509
    assert var_backtests._xlogx_term(5, 0.5) == -3.4657359027997265


def test_hansen_spa_reproduces_the_frozen_values() -> None:
    """SPA の p 値が分割前と float bit 一致（固定 seed・2 seed x 2 B）。"""
    # Arrange
    hansen_spa = importlib.import_module("common.hansen_spa")
    f_matrix = np.random.default_rng(3).normal(0.0, 1.0, (60, 4)).tolist()
    spa = hansen_spa.HansenSpa()

    # Act / Assert
    assert spa.spa_pvalue(f_matrix, seed=0, B=200) == 0.435
    assert spa.spa_pvalue(f_matrix, seed=0, B=500) == 0.446
    assert spa.spa_pvalue(f_matrix, seed=7, B=200) == 0.47
    assert spa.spa_pvalue(f_matrix, seed=7, B=500) == 0.468


@pytest.mark.parametrize(("n", "B"), [(200, 100), (200, 400), (800, 100)])
def test_spa_issues_one_resample_per_replicate(
    monkeypatch: pytest.MonkeyPatch, n: int, B: int
) -> None:
    """計算量テスト: spa_pvalue が発行したリサンプル − 出力に使った反復数 = 0。

    「使った反復」は B（1 反復が 1 つの Vb を生み、その 1 つが超過判定に使われる）。
    捨てるリサンプルを作れば発行が B を上回り赤になる。回数を焼き込まず**無駄の不在**を
    固定する。B 100/400 で比例し、系列長 n 200/800 では増えないこと（発行が B だけで
    決まる＝オーダーの表明）も 2 点ずつで固定する。
    """
    # Arrange
    hansen_spa = importlib.import_module("common.hansen_spa")
    issued: list[int] = []
    original = hansen_spa.stationary_bootstrap_indices

    def _spy(size, block, rng):
        issued.append(size)
        return original(size, block, rng)

    monkeypatch.setattr(hansen_spa, "stationary_bootstrap_indices", _spy)
    f_matrix = np.random.default_rng(5).normal(0.0, 1.0, (n, 3)).tolist()

    # Act
    hansen_spa.HansenSpa().spa_pvalue(f_matrix, seed=0, B=B)

    # Assert
    assert len(issued) - B == 0
