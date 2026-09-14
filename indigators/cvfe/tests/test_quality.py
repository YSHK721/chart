"""気配品質診断とサンプリング間隔決定の検証（仕様 §4.1・§4.2）。"""

import sys
from pathlib import Path

import numpy as np
import pytest

_PKG_DIR = Path(__file__).resolve().parents[1]
if str(_PKG_DIR) not in sys.path:
    sys.path.insert(0, str(_PKG_DIR))
if str(Path(__file__).resolve().parent) not in sys.path:
    sys.path.insert(0, str(Path(__file__).resolve().parent))

from cvfe_synthetic import make_dataset  # noqa: E402
from src.quality import (  # noqa: E402
    REFERENCE_DELTA_SEC,
    SAMPLING_GRID,
    _freeze_ratio,
    diagnose_quality,
    select_delta_star,
)
from src.sampling import validate_edges, validate_ticks  # noqa: E402


def test_sampling_grid_matches_specification():
    """D = {5, 10, 15, 30, 60, 120, 300, 600, 900, 1800}（仕様 §4.1-2）。"""
    assert SAMPLING_GRID == (5, 10, 15, 30, 60, 120, 300, 600, 900, 1800)
    assert REFERENCE_DELTA_SEC == 300


def test_select_delta_star_picks_smallest_stable_delta():
    """以降すべての Δ' が基準と 5% 未満で一致する最小の Δ を選ぶ（仕様 §4.2）。"""
    ref = 1.0
    # Δ = 5, 10 は基準から 10% ずれ、Δ >= 15 は一致する → Δ* = 15。
    rv = {5: 1.10, 10: 1.10, 15: 1.02, 30: 1.01, 60: 1.0,
          120: 1.0, 300: ref, 600: 1.01, 900: 1.02, 1800: 1.03}
    assert select_delta_star(rv) == 15


def test_select_delta_star_requires_all_coarser_deltas_to_agree():
    """途中の Δ' が外れる場合はそれより細かい Δ は採用しない（『すべての Δ' >= Δ』）。"""
    rv = {5: 1.0, 10: 1.0, 15: 1.0, 30: 1.0, 60: 1.0,
          120: 1.0, 300: 1.0, 600: 1.0, 900: 1.20, 1800: 1.0}
    # 900 が外れるため Δ <= 900 はすべて不可。1800 のみが条件を満たす。
    assert select_delta_star(rv) == 1800


def test_select_delta_star_falls_back_to_300():
    """条件を満たす Δ が存在しない場合は 300 を返す（仕様 §4.2）。"""
    rv = {d: 1.0 for d in SAMPLING_GRID}
    rv[1800] = 2.0                       # 最も粗い Δ が外れる → 該当なし
    assert select_delta_star(rv) == REFERENCE_DELTA_SEC


def test_freeze_ratio_counts_only_runs_of_at_least_60_seconds():
    """60 秒以上連続して不変であった時間のみを数える（仕様 §4.1-4）。"""
    # 0..100 秒は不変（101 秒間 → 100 秒の継続）、105..135 は別値で不変（30 秒 → 対象外）。
    times = np.concatenate([np.arange(0.0, 101.0, 5.0), np.arange(105.0, 136.0, 5.0)])
    logp = np.concatenate([np.full(21, 1.0), np.full(7, 2.0)])
    edges = np.array([0.0, 200.0])
    got = _freeze_ratio(times, logp, edges, 1)
    assert got == pytest.approx(100.0 / 200.0)


def test_omega2_uses_five_second_total_rv_over_twice_sample_count():
    """ω̂² = RV_total(5 秒) / (2 · n_min)（仕様 §4.1-3）。"""
    ticks, edges = make_dataset(520, bar_sec=3600, tick_sec=5, seed=5,
                                noise_omega_ratio=1.0)
    times, logp = validate_ticks(ticks)
    e = validate_edges(edges)
    rep = diagnose_quality(times, logp, e, 500, 0.05)
    # 診断期間は先頭 500 本。各バーの Δ=5 格子点は 3600/5 + 1 = 721 点。
    n_min = 500 * 721
    assert rep.omega2_hat == pytest.approx(rep.rv_mean[5] * 500 / (2 * n_min), rel=1e-12)
    assert rep.omega2_hat > 0.0


def test_clean_data_passes_and_selects_rv():
    """ノイズ・凍結のない系列は PASS かつ measure_id="RV"（仕様 §4.1-6）。"""
    ticks, edges = make_dataset(520, bar_sec=3600, tick_sec=5, seed=9)
    times, logp = validate_ticks(ticks)
    rep = diagnose_quality(times, logp, validate_edges(edges), 500, 0.05)
    assert rep.quality_gate == "PASS"
    assert rep.measure_id == "RV"
    assert rep.signature_slope <= 0.10
    assert rep.delta_star_sec in SAMPLING_GRID
    assert rep.freeze_ratio == 0.0


def test_freeze_gate_precedes_signature_slope():
    """凍結率超過は勾配より先に評価され FAIL / PARK になる（仕様 §4.1-6 の評価順）。"""
    ticks, edges = make_dataset(520, bar_sec=3600, tick_sec=5, seed=9,
                                freeze_fraction=0.5, noise_omega_ratio=1.0)
    times, logp = validate_ticks(ticks)
    rep = diagnose_quality(times, logp, validate_edges(edges), 500, 0.05)
    assert rep.quality_gate == "FAIL" and rep.measure_id == "PARK"
    assert rep.delta_star_sec == 0
