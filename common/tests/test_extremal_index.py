"""Ferro–Segers intervals 推定量の検証（必須ゲート 1 の道具立て）。

推定量そのものが正しいことを、**θ が解析的に既知の過程**で確かめる。実データの θ を測る前に
道具を検証しておかないと、「θ が小さい」のが現象なのか実装バグなのか区別できない。

検証に使う過程:
    ARMAX ``X_t = max(α X_{t−1}, (1−α) Z_t)``（Z は標準 Fréchet）は **θ = 1 − α**。
    α = 0 は iid で θ = 1。
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from common.extremal_index import (  # noqa: E402
    armax_series,
    extremal_index_of_series,
    intervals_estimator,
)


# ---------------------------------------------------------------------------
# 式そのもの（手計算可能な小入力）
# ---------------------------------------------------------------------------

def test_formula_branch_when_all_gaps_are_small():
    """max(S) <= 2 の枝: θ̂ = 2 (ΣS)² / ((N−1) ΣS²)。"""
    times = np.array([0, 1, 2, 4])          # S = [1, 1, 2] → max = 2
    s = np.array([1.0, 1.0, 2.0])
    expect = 2.0 * s.sum() ** 2 / (s.size * (s ** 2).sum())
    assert intervals_estimator(times) == pytest.approx(min(1.0, expect))


def test_formula_branch_when_a_gap_exceeds_two():
    """max(S) > 2 の枝: θ̂ = 2 (Σ(S−1))² / ((N−1) Σ(S−1)(S−2))。"""
    times = np.array([0, 1, 5, 12])         # S = [1, 4, 7] → max = 7
    s = np.array([1.0, 4.0, 7.0])
    expect = 2.0 * (s - 1).sum() ** 2 / (s.size * ((s - 1) * (s - 2)).sum())
    assert intervals_estimator(times) == pytest.approx(min(1.0, expect))


def test_theta_is_capped_at_one():
    """θ は 1 を超えない（超過が完全に散らばっても独立が上限）。"""
    times = np.arange(0, 1000, 50)
    assert intervals_estimator(times) <= 1.0


def test_insufficient_exceedances_return_nan():
    assert np.isnan(intervals_estimator(np.array([])))
    assert np.isnan(intervals_estimator(np.array([5])))
    assert np.isnan(intervals_estimator(np.array([5, 7])))     # S が 1 本＝推定不能


def test_denominator_of_the_large_gap_branch_is_always_positive():
    """`max(S) > 2` の枝は分母が必ず正になる（退化しない）ことを固定する。

    Σ(S−1)(S−2) の各項は S=1 → 0、S=2 → 0、S>=3 → 正。枝に入る条件が「ある S が 3 以上」で
    あるから、和は必ず正。したがって当該枝で 0 除算は起こらない（実装のガードは防御的措置）。
    """
    for times in (np.array([0, 1, 2, 3, 10]), np.array([0, 2, 4, 9]), np.array([0, 1, 100])):
        got = intervals_estimator(times)
        assert np.isfinite(got) and 0.0 < got <= 1.0, f"{times} → {got!r}"


def test_small_gap_branch_caps_at_one_rather_than_exceeding_it():
    """`max(S) <= 2` の枝は 1 を超える生値を出しうるため 1 で打ち切る。

    例: S = [1, 2] は 2·3²/(2·5) = 1.8。超過が 3 点しかない極小標本で θ が 1 を超えるのは
    推定量の性質であり、意味のある値ではない。1 で打ち切って「独立以上にはならない」を保つ。
    """
    assert intervals_estimator(np.array([0, 1, 3])) == 1.0


# ---------------------------------------------------------------------------
# 既知 θ の過程での回復（推定量の妥当性）
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("alpha,expected", [(0.0, 1.0), (0.3, 0.7), (0.5, 0.5), (0.8, 0.2)])
def test_recovers_known_theta_of_armax(alpha, expected):
    """ARMAX の既知 θ = 1 − α を、上位 5% 閾値で回復する。"""
    rng = np.random.default_rng(20260731 + int(alpha * 100))
    x = armax_series(200_000, alpha, rng=rng)
    u = float(np.quantile(x, 0.95))

    res = extremal_index_of_series(x, u, upper=True)

    assert res.theta == pytest.approx(expected, abs=0.05), (
        f"alpha={alpha}: θ̂={res.theta!r} vs 理論 {expected}")


def test_iid_data_gives_theta_near_one():
    """iid なら θ = 1（クラスタ化なし）。"""
    rng = np.random.default_rng(7)
    x = rng.standard_normal(200_000)
    res = extremal_index_of_series(x, float(np.quantile(x, 0.95)), upper=True)
    assert res.theta == pytest.approx(1.0, abs=0.05)


def test_effective_clusters_is_theta_times_exceedances():
    rng = np.random.default_rng(11)
    x = armax_series(50_000, 0.5, rng=rng)
    res = extremal_index_of_series(x, float(np.quantile(x, 0.95)), upper=True)
    assert res.effective_clusters == pytest.approx(res.theta * res.n_exceedances)
    assert res.n_exceedances > 0


def test_lower_tail_uses_less_than_threshold():
    """下側裾（リターンの負側）は `values < u` で超過を取る。"""
    x = np.array([0.0, -5.0, 0.0, -6.0, 0.0, -7.0, 0.0])
    res = extremal_index_of_series(x, -1.0, upper=False)
    assert res.n_exceedances == 3
    upper = extremal_index_of_series(x, -1.0, upper=True)
    assert upper.n_exceedances == 4


def test_nan_values_are_not_counted_as_exceedances():
    x = np.array([np.nan, 10.0, np.nan, 11.0, 12.0])
    res = extremal_index_of_series(x, 5.0, upper=True)
    assert res.n_exceedances == 3
