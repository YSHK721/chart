"""zp compute と検定パイプライン step5_null_b の数値パリティ（移植の正しさ＋定数ドリフト防止）。

分析層（indigators/market_profile/analysis/mp_stats）を sys.path へ追加して import し、
同一 S・同一 seed・同一 M・同一 CHUNK で Null B モーメントが完全一致することを固定する。
グリッド整列: 日レンジ [20000, 20400)・row_w = 400/40 = 10 = GRID_W・klo=2000/khi=2039 で
step5 の日相対 40 行と zp の絶対グリッド 40 セルが一致する。
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from market_profile_api.compute import market_profile_zp as zp

_ANALYSIS_DIR = Path(__file__).resolve().parents[2] / "analysis"
if str(_ANALYSIS_DIR) not in sys.path:
    sys.path.insert(0, str(_ANALYSIS_DIR))

from mp_stats import data_prep as dp  # noqa: E402
from mp_stats import step5_null_b as s5  # noqa: E402


def test_constants_parity_with_analysis():
    """定数複製のドリフト防止（analysis 側と等値であることを固定）。"""
    assert zp.SESSION_OPEN_MOD == dp.SESSION_OPEN_MOD
    assert zp.SESSION_CLOSE_MOD == dp.SESSION_CLOSE_MOD
    assert zp.BRACKET_BASE_MOD == dp.BRACKET_BASE_MOD
    assert zp.BRACKET_MIN == dp.BRACKET_MIN
    assert zp.K_BRACKETS == dp.K_BRACKETS
    assert zp.CHUNK == s5.CHUNK
    mods = np.arange(dp.SESSION_OPEN_MOD, dp.SESSION_CLOSE_MOD + 1)
    assert np.array_equal(zp._B_OF_MINUTE, dp.calendar_bracket_of_mod(mods))


def _analysis_session(D: int, seed: int):
    """分析層の合成 M1 → SessionData/DailyFeatures（20000 中心・毎日リセット）。"""
    rng = np.random.default_rng(seed)
    mods = np.arange(61, 1439)
    frames = []
    for d in range(D):
        steps = rng.normal(scale=2.0, size=mods.size)
        closes = 20000.0 + np.cumsum(steps)
        opens = np.concatenate([[20000.0], closes[:-1]])
        frames.append(pd.DataFrame({
            "epoch": 1704067200 + d * 86400 + mods * 60,
            "open": opens,
            "high": np.maximum(opens, closes),
            "low": np.minimum(opens, closes),
            "close": closes,
        }))
    df = pd.concat(frames, ignore_index=True)
    sd = dp.build_session_data(df)
    f = dp.build_daily_features(sd, variants=(dp.PRIMARY,))
    return sd, f


def test_null_b_moments_parity_with_step5():
    """同一 S・seed・M・CHUNK で step5.null_b_day と mean/sd が一致（グリッド整列条件下）。"""
    sd, f = _analysis_session(30, seed=21)
    S = s5.build_step_matrix(sd, f)
    b = dp.calendar_bracket_of_mod(
        np.arange(dp.SESSION_OPEN_MOD, dp.SESSION_CLOSE_MOD + 1, dtype=np.int32)
    )
    d = 10
    open_d = float(f.o[d])
    low, high = 20000.0, 20400.0  # row_w = 10 = GRID_W・境界整列
    m_reps = 600
    mean5, sd5 = s5.null_b_day(S, b, d, open_d, low, high, rng=np.random.default_rng(33), m_reps=m_reps)
    mean_z, var_z = zp.null_b_moments_abs(
        S, open_d, 2000, 2039, rng=np.random.default_rng(33), m_reps=m_reps
    )
    assert mean_z.shape == mean5.shape == (40,)
    assert np.allclose(mean_z, mean5, rtol=0, atol=1e-12)
    assert np.allclose(np.sqrt(var_z), sd5, rtol=0, atol=1e-12)


def test_obs_counts_parity_with_step5():
    """観測側も step5.observed_row_counts と一致（同一グリッド整列）。"""
    rng = np.random.default_rng(55)
    closes = 20000.0 + np.cumsum(rng.normal(scale=1.5, size=zp.G_MINUTES))
    closes = np.clip(closes, 20000.0, 20399.99)
    obs5 = s5.observed_row_counts(closes, 20000.0, 20400.0)
    obs_z = zp.obs_cell_counts(closes, 2000, 2039)
    assert np.array_equal(obs_z, obs5)
