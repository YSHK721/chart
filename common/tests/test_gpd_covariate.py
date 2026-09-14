"""共変量 GPD（研究ゲート専用）の分離と Nelder–Mead の抽出を固定する（ISSUE-479 Wave2 C-2）。

背景（実測）: 共変量 GPD の 5 名（負の対数尤度・当てはめ結果・当てはめ・尤度比検定・
検出力曲線）は本番参照 0・テスト 0 のまま `common.gpd` に同居していた。研究ゲート専用の
探索コードが、本番が常時 import する当てはめ核と同じモジュールにあると、片方の変更が
もう片方の読み手を巻き込む。最適化ルーチンも gpd 内の private 実装で、共有できなかった。

**移設前に既存の錨がゼロ**だったため、現行実装の出力を固定 seed で凍結してから移した
（凍結値は移設前の実装で採取したもの。以後この値からの逸脱は回帰である）。

本ファイルが固定するもの:
    1. 5 名が新モジュールにあり、gpd には定義が残っていないこと（AST）
    2. 抽出・公開昇格した名前が元の名前と同一オブジェクトであること（温存の証明）
    3. 移設前に凍結した出力との float bit 一致
    4. 振る舞い検定 3 件（既知パラメータの再現・H0 真での検定の大きさ・検出力の単調性）
    5. 当てはめ 1 回につき最適化の発行が 1 回であること（計算量）
"""
from __future__ import annotations

import ast
import importlib
import pathlib

import numpy as np
import pytest

gpd = importlib.import_module("common.gpd")

_GPD_SOURCE = pathlib.Path(gpd.__file__)

#: 研究ゲート専用として分離した 5 名（全数）。
_MOVED_NAMES = (
    "covariate_gpd_neg_loglik",
    "CovariateGpdFit",
    "covariate_gpd_fit",
    "lr_test_last_coefficient",
    "power_curve",
)


def _covariate():
    """分離先モジュールを遅延 import する（収集時エラーで他検定を巻き添えにしない）。"""
    return importlib.import_module("common.gpd_covariate")


def _fixture_design(n: int = 240, seed: int = 42):
    """凍結時と同一の計画行列と超過標本を組み立てる（seed 固定）。"""
    rng = np.random.default_rng(seed)
    x = rng.normal(0.0, 1.0, n)
    design = np.column_stack([np.ones(n), x])
    gamma_true = np.array([np.log(2.0), 0.35])
    xi_true = 0.15
    beta = np.exp(design @ gamma_true)
    u = rng.random(n)
    excess = beta * ((1.0 - u) ** (-xi_true) - 1.0) / xi_true
    return design, excess, gamma_true, xi_true


def test_the_covariate_module_exposes_the_five_names() -> None:
    """研究ゲート専用の 5 名が分離先モジュールから取得できる。"""
    covariate = _covariate()
    missing = [name for name in _MOVED_NAMES if not hasattr(covariate, name)]
    assert missing == [], f"分離先に不足: {missing}"


def _top_level_definitions(path: pathlib.Path) -> set[str]:
    """モジュール直下で定義される名前（関数・クラス・代入）の集合。"""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    defined: set[str] = set()
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.ClassDef)):
            defined.add(node.name)
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            defined.add(node.target.id)
        elif isinstance(node, ast.Assign):
            defined.update(t.id for t in node.targets if isinstance(t, ast.Name))
    return defined


def test_gpd_no_longer_defines_the_covariate_functions() -> None:
    """当てはめ核に研究ゲート専用の定義が残っていない。"""
    leaked = sorted(_top_level_definitions(_GPD_SOURCE) & set(_MOVED_NAMES))
    assert leaked == [], f"研究ゲート専用の定義が当てはめ核に残存: {leaked}"


def test_the_optimizer_is_shared_and_the_old_private_name_is_preserved() -> None:
    """最適化ルーチンは共有モジュールが唯一の実装で、旧 private 名は同一オブジェクト。"""
    nelder_mead = importlib.import_module("common.nelder_mead")
    assert gpd._nelder_mead is nelder_mead.nelder_mead


def test_the_promoted_constant_keeps_the_old_private_name() -> None:
    """公開昇格した閾値定数の旧 private 名は同一オブジェクト。"""
    assert gpd._XI_EPS is gpd.XI_EPS
    assert gpd.XI_EPS == 1e-8


def test_covariate_fit_reproduces_the_frozen_values() -> None:
    """移設前に凍結した当てはめ結果と float bit 一致。"""
    # Arrange
    covariate = _covariate()
    design, excess, _gamma_true, _xi_true = _fixture_design()

    # Act
    fit = covariate.covariate_gpd_fit(excess, design)

    # Assert
    assert list(map(float, fit.gamma)) == [0.6504621103635728, 0.3958482236959193]
    assert float(fit.xi) == 0.16752902790634538
    assert float(fit.neg_loglik) == 431.8544960631905
    assert fit.n == 240
    assert covariate.covariate_gpd_neg_loglik(
        np.array([0.7, 0.35, 0.15]), excess, design
    ) == 432.1556067988879


def test_likelihood_ratio_test_reproduces_the_frozen_values() -> None:
    """移設前に凍結した尤度比検定の統計量・p 値と float bit 一致。"""
    covariate = _covariate()
    design, excess, _gamma_true, _xi_true = _fixture_design()

    stat, p_value = covariate.lr_test_last_coefficient(excess, design)

    assert float(stat) == 22.522741913559003
    assert float(p_value) == 2.076704108158369e-06


def test_power_curve_reproduces_the_frozen_values() -> None:
    """移設前に凍結した検出力曲線と一致（seed 固定）。"""
    covariate = _covariate()
    design, _excess, _gamma_true, _xi_true = _fixture_design()

    curve = covariate.power_curve(
        design, 0.15, np.array([np.log(2.0)]), [0.0, 0.4],
        n_sim=25, alpha=0.05, rng=np.random.default_rng(9),
    )

    assert curve == [(0.0, 0.04), (0.4, 1.0)]


def test_the_fit_recovers_the_generating_parameters() -> None:
    """振る舞い 1: 既知パラメータで生成した標本から、その値を許容誤差内で復元する。"""
    # Arrange
    covariate = _covariate()
    design, excess, gamma_true, xi_true = _fixture_design()

    # Act
    fit = covariate.covariate_gpd_fit(excess, design)

    # Assert: n=240 の有限標本誤差を見込んだ粗い許容（点推定の一致ではなく整合を見る）。
    assert fit.gamma[0] == pytest.approx(gamma_true[0], abs=0.25)
    assert fit.gamma[1] == pytest.approx(gamma_true[1], abs=0.20)
    assert fit.xi == pytest.approx(xi_true, abs=0.20)


def test_the_test_size_is_near_alpha_when_the_null_is_true() -> None:
    """振る舞い 2: H0（最後の係数 = 0）が真のとき、棄却率が α の近傍に収まる。

    p 値が粗く一様であることの操作的な表明（厳密一様性ではなく、検定の大きさが
    名目水準から大きく外れていないことを固定する）。
    """
    covariate = _covariate()
    design, _excess, _gamma_true, _xi_true = _fixture_design()

    curve = covariate.power_curve(
        design, 0.15, np.array([np.log(2.0)]), [0.0],
        n_sim=100, alpha=0.05, rng=np.random.default_rng(2026),
    )

    size = curve[0][1]
    assert 0.0 <= size <= 0.20, f"H0 真での棄却率が名目 5% から大きく外れている: {size}"


def test_power_increases_with_the_effect_size() -> None:
    """振る舞い 3: 効果量を大きくすると検出力が単調に増える。"""
    covariate = _covariate()
    design, _excess, _gamma_true, _xi_true = _fixture_design()

    curve = covariate.power_curve(
        design, 0.15, np.array([np.log(2.0)]), [0.0, 0.1, 0.3],
        n_sim=100, alpha=0.05, rng=np.random.default_rng(2026),
    )

    powers = [power for _effect, power in curve]
    assert powers == sorted(powers), f"検出力が効果量に対して単調でない: {curve}"
    assert powers[-1] > powers[0]


class _OptimizerSpy:
    """Test Spy: 最適化ルーチンの発行回数を数える（実体へは委譲する）。"""

    def __init__(self, original) -> None:
        self._original = original
        self.issued = 0

    def __call__(self, *args, **kwargs):
        self.issued += 1
        return self._original(*args, **kwargs)


@pytest.mark.parametrize("n_rows", [100, 400])
def test_one_covariate_fit_issues_one_optimization(
    monkeypatch: pytest.MonkeyPatch, n_rows: int
) -> None:
    """計算量テスト: 当てはめ 1 回あたりの最適化発行 − 1 = 0。

    捨てる当てはめ（多点スタートの結果を 1 つしか使わない等）が入れば発行が上回り赤になる。
    行数 100/400 の 2 点で当てはめあたりの発行が変わらないこと（発行が標本数に比例しない
    ＝オーダーの表明）も固定する。
    """
    # Arrange
    covariate = _covariate()
    spy = _OptimizerSpy(covariate.nelder_mead)
    monkeypatch.setattr(covariate, "nelder_mead", spy)
    design, excess, _gamma_true, _xi_true = _fixture_design(n=n_rows)

    # Act
    covariate.covariate_gpd_fit(excess, design)

    # Assert
    assert spy.issued - 1 == 0


def test_the_likelihood_ratio_test_issues_two_optimizations(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """計算量テスト: 尤度比検定の最適化発行 − 使った当てはめ数（full と null の 2）= 0。"""
    covariate = _covariate()
    spy = _OptimizerSpy(covariate.nelder_mead)
    monkeypatch.setattr(covariate, "nelder_mead", spy)
    design, excess, _gamma_true, _xi_true = _fixture_design()

    covariate.lr_test_last_coefficient(excess, design)

    fits_used = 2      # 帰無・対立の 2 モデル（どちらの対数尤度も統計量に使われる）
    assert spy.issued - fits_used == 0


@pytest.mark.parametrize(("n_sim", "n_effects"), [(3, 1), (3, 2), (6, 1)])
def test_power_curve_issues_two_optimizations_per_simulated_test(
    monkeypatch: pytest.MonkeyPatch, n_sim: int, n_effects: int
) -> None:
    """計算量テスト: 検出力曲線の最適化発行 − 効果量数 x 反復数 x 2 = 0。

    反復数・効果量数それぞれを変えた 2 点で比例することを固定する（オーダーの表明）。
    """
    covariate = _covariate()
    spy = _OptimizerSpy(covariate.nelder_mead)
    monkeypatch.setattr(covariate, "nelder_mead", spy)
    design, _excess, _gamma_true, _xi_true = _fixture_design(n=100)
    effect_sizes = [0.0, 0.2][:n_effects]

    covariate.power_curve(
        design, 0.15, np.array([np.log(2.0)]), effect_sizes,
        n_sim=n_sim, alpha=0.05, rng=np.random.default_rng(3),
    )

    fits_used = n_effects * n_sim * 2
    assert spy.issued - fits_used == 0
