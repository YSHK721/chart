"""common.ols_fit — 直線 OLS 当てはめ共有プリミティブの契約を固定する回帰テスト。

ISSUE-179 項目 3 で ``tgp_btlm/src/reference.py``（全行）と ``btlm_trail/src/core.py``
（窓末尾）に複製されていた「Φ=[1,x] の OLS + 予測分散 s²·(1+leverage)」を共有化した。

**leverage の 2 形は統合しない**（実測: 端点ベクトル形 ``φ₀ᵀ(ΦᵀΦ)⁻¹φ₀`` と einsum 全行形の
末尾要素は 3000 試行中 232 件で最終ビットが不一致）。よって両形を別関数として保持し、
各呼び出し側は従来と同一の形を使い続ける。本テストはその非同一性も含めて固定する。
"""

from __future__ import annotations

import numpy as np


def _sample() -> tuple[np.ndarray, np.ndarray]:
    x = np.arange(1.0, 9.0)
    z = np.array([1.0, 2.5, 2.0, 4.5, 5.0, 5.5, 7.5, 8.0])
    return x, z


def test_ols_fit_recovers_exact_line_without_residual() -> None:
    # Arrange: 完全な直線 z = 3 + 2x（残差 0）。
    from common.ols_fit import ols_fit

    x = np.arange(1.0, 6.0)
    z = 3.0 + 2.0 * x

    # Act
    fit = ols_fit(x, z)

    # Assert
    assert fit.beta[0] == np.float64(3.0).item() or abs(fit.beta[0] - 3.0) < 1e-9
    assert abs(fit.beta[1] - 2.0) < 1e-9
    assert abs(fit.s2) < 1e-18
    assert np.allclose(fit.fitted, z)


def test_ols_fit_exposes_design_and_inverse_gram() -> None:
    # Arrange
    from common.ols_fit import ols_fit

    x, z = _sample()

    # Act
    fit = ols_fit(x, z)

    # Assert: Φ=[1, x] と (ΦᵀΦ)⁻¹ を呼び出し側へ渡す（leverage 算出に必要）。
    assert fit.phi.shape == (8, 2)
    assert np.allclose(fit.phi[:, 0], 1.0)
    assert np.allclose(fit.phi[:, 1], x)
    assert fit.xtx_inv.shape == (2, 2)
    assert np.allclose(fit.phi.T @ fit.phi @ fit.xtx_inv, np.eye(2), atol=1e-9)


def test_ols_fit_residual_variance_uses_n_minus_two_dof() -> None:
    # Arrange
    from common.ols_fit import ols_fit

    x, z = _sample()

    # Act
    fit = ols_fit(x, z)

    # Assert
    residual = z - fit.fitted
    assert fit.s2 == float(residual @ residual) / (x.size - 2)


def test_pred_sd_rows_matches_manual_einsum_leverage() -> None:
    # Arrange
    from common.ols_fit import ols_fit, pred_sd_rows

    x, z = _sample()
    fit = ols_fit(x, z)

    # Act
    got = pred_sd_rows(fit.phi, fit.xtx_inv, fit.s2)

    # Assert: 参照実装 OlsBtlmFitter と同一の線形代数（全行 leverage）。
    leverage = np.einsum("ij,jk,ik->i", fit.phi, fit.xtx_inv, fit.phi)
    expected = np.sqrt(fit.s2 * (1.0 + leverage))
    assert got.tobytes() == expected.tobytes()


def test_pred_sd_at_matches_manual_endpoint_leverage() -> None:
    # Arrange
    from common.ols_fit import ols_fit, pred_sd_at

    x, z = _sample()
    fit = ols_fit(x, z)
    row = np.array([1.0, float(x.size)])

    # Act
    got = pred_sd_at(row, fit.xtx_inv, fit.s2)

    # Assert: btlm_trail._window_end_scalar と同一の線形代数（端点 leverage）。
    leverage = float(row @ fit.xtx_inv @ row)
    assert np.float64(got).tobytes() == np.float64(
        float(np.sqrt(fit.s2 * (1.0 + leverage)))
    ).tobytes()


def test_two_leverage_forms_are_not_merged() -> None:
    """2 形は最終ビットが一致しない場合があるため別関数として保持する（実測根拠）。"""
    # Arrange
    from common.ols_fit import ols_fit, pred_sd_at, pred_sd_rows

    rng = np.random.default_rng(0)
    mismatches = 0

    # Act
    for _ in range(500):
        w = int(rng.integers(3, 60))
        x = np.arange(1.0, w + 1.0)
        z = np.cumsum(rng.normal(size=w)) * rng.uniform(0.1, 1000)
        fit = ols_fit(x, z)
        a = pred_sd_at(np.array([1.0, float(w)]), fit.xtx_inv, fit.s2)
        b = float(pred_sd_rows(fit.phi, fit.xtx_inv, fit.s2)[-1])
        if np.float64(a).tobytes() != np.float64(b).tobytes():
            mismatches += 1

    # Assert: 数値としては同値だがビット列は一致しない事例が実在する。
    assert mismatches > 0
    assert abs(a - b) < 1e-6 * max(1.0, abs(a))
