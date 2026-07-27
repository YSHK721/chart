"""common.normal_dist — 標準正規逆累積分布（Acklam 有理近似）の契約を固定する回帰テスト。

ISSUE-179 項目 3 で ``tgp_btlm/src/core.py`` と ``btlm_trail/src/core.py`` に完全一致で
複製されていた Acklam 係数 20 個・分岐しきい値 0.02425 を共有プリミティブへ 1 本化した。
本テストは移設元（``tgp_btlm`` のベクトル版）の挙動をそのまま固定する。
"""

from __future__ import annotations

import numpy as np
import pytest


def test_norm_ppf_matches_known_quantiles() -> None:
    # Arrange
    from common.normal_dist import norm_ppf

    # Act / Assert: Acklam 近似の相対誤差は約 1.15e-9。
    assert norm_ppf(0.5) == pytest.approx(0.0, abs=1e-9)
    assert norm_ppf(0.975) == pytest.approx(1.959963985, abs=1e-6)
    assert norm_ppf(0.95) == pytest.approx(1.644853627, abs=1e-6)
    assert norm_ppf(0.025) == pytest.approx(-1.959963985, abs=1e-6)


def test_norm_ppf_scalar_input_returns_float() -> None:
    # Arrange
    from common.normal_dist import norm_ppf

    # Act
    out = norm_ppf(0.9)

    # Assert
    assert isinstance(out, float)


def test_norm_ppf_array_input_returns_array_elementwise() -> None:
    # Arrange
    from common.normal_dist import norm_ppf

    ps = np.array([0.01, 0.5, 0.99])  # 下側裾 / 中央 / 上側裾の 3 分岐を跨ぐ

    # Act
    out = norm_ppf(ps)

    # Assert
    assert isinstance(out, np.ndarray)
    assert out.shape == (3,)
    for i, p in enumerate(ps):
        assert out[i] == norm_ppf(float(p))


def test_norm_ppf_is_antisymmetric_around_half() -> None:
    # Arrange
    from common.normal_dist import norm_ppf

    # Act / Assert
    for p in (0.001, 0.02, 0.02425, 0.3, 0.49):
        assert norm_ppf(p) == pytest.approx(-norm_ppf(1.0 - p), rel=1e-12)


def test_norm_ppf_branch_threshold_is_continuous() -> None:
    # Arrange: 下側裾/中央の分岐しきい値 0.02425 の境界（有理近似の継ぎ目）。
    from common.normal_dist import norm_ppf

    # Act
    below = norm_ppf(0.02425 - 1e-9)
    at = norm_ppf(0.02425)

    # Assert: 継ぎ目での跳びが近似誤差（~1e-9）の範囲に収まる。
    assert abs(at - below) < 1e-7


@pytest.mark.parametrize("bad", [0.0, 1.0, -0.1, 1.5])
def test_norm_ppf_rejects_out_of_range(bad: float) -> None:
    # Arrange
    from common.normal_dist import norm_ppf

    # Act / Assert
    with pytest.raises(ValueError, match="0 < p < 1"):
        norm_ppf(bad)


def test_norm_ppf_rejects_out_of_range_inside_array() -> None:
    # Arrange
    from common.normal_dist import norm_ppf

    # Act / Assert: 配列に 1 要素でも範囲外があれば拒否する。
    with pytest.raises(ValueError, match="0 < p < 1"):
        norm_ppf(np.array([0.5, 1.0]))
