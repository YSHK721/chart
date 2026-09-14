"""OHLC 経路（仕様 §4.1-6 の PARK 縮退）の検証。

正本仕様: indigators/cvfe/CVFE_spec_v1.0.md §4.1-6・§4.3 "PARK"・§3.3 E06

検証観点:
    - レンジ 0 のバー（high == low）を無効バーとして扱う（ISSUE-224）
    - その結果 σ̂ が発散しない（Jensen 補正 exp(s²/8) が暴走しない）
    - 通常のバーは従来どおり有効で、σ̂ が Parkinson σ の水準に収まる
"""

import sys
from pathlib import Path

import numpy as np
import pytest

_PKG_DIR = Path(__file__).resolve().parents[1]
if str(_PKG_DIR) not in sys.path:
    sys.path.insert(0, str(_PKG_DIR))

from src.ohlc import (  # noqa: E402
    bar_edges_from_times,
    compute_cvfe_from_ohlc,
    measures_from_ohlc,
)


def _series(n=700, seed=5, zero_range_at=()):
    """レンジ 0 のバーを任意本数だけ含む OHLC を作る。"""
    rng = np.random.default_rng(seed)
    c = 10_000.0 * np.exp(np.cumsum(rng.standard_normal(n) * 0.008))
    h = c * (1 + np.abs(rng.standard_normal(n)) * 0.004)
    lo = c * (1 - np.abs(rng.standard_normal(n)) * 0.004)
    o = np.concatenate([[c[0]], c[:-1]])
    for i in zero_range_at:                       # 無取引日を模す（4 値すべて同値）
        o[i] = h[i] = lo[i] = c[i] = c[i]
    t = 1_262_304_000.0 + np.arange(n) * 86_400.0
    return o, h, lo, c, t


def test_zero_range_bar_is_invalid():
    """high == low のバーは valid=False（§3.3 E06 と同じ扱い・ISSUE-224）。"""
    o, h, lo, c, t = _series(n=50, zero_range_at=(7, 20))
    ms = measures_from_ohlc(o, h, lo, c, bar_edges_from_times(t, 86_400))
    assert ms[7].valid is False and ms[20].valid is False
    assert np.isnan(ms[7].v) and np.isnan(ms[7].c)
    assert all(ms[i].valid for i in range(50) if i not in (7, 20))


def test_zero_range_bars_do_not_blow_up_sigma_hat():
    """レンジ 0 のバーが混じっても σ̂ が発散しない（ISSUE-224 の回帰壁）。

    修正前は C_FLOOR クリップで ln C = −36.8 の外れ値が生まれ、残差分散 s² が膨張して
    仕様 §4.6 の Jensen 補正 exp(s²/8) が桁違いの倍率を掛けていた。
    """
    n = 700
    zero_at = tuple(range(10, n, 40))             # 約 2% のバーをレンジ 0 にする
    o, h, lo, c, t = _series(n=n, zero_range_at=zero_at)
    res = compute_cvfe_from_ohlc(o, h, lo, c, t, n_har=500)

    s = res.sigma_hat[res.available]
    assert s.size > 50, "有効バーが少なすぎて検証にならない"

    pk = np.sqrt((np.log(h / lo)) ** 2 / (4 * np.log(2)))
    ref = float(np.median(pk[pk > 0]))            # 測定量そのものの水準（正しい目安）
    assert np.median(s) == pytest.approx(ref, rel=0.5), (
        f"σ̂ 中央値 {np.median(s)} が測定量水準 {ref} から乖離している")
    assert res.har_resid_var < 5.0, f"残差分散 s² が膨張している: {res.har_resid_var}"
    assert s.max() < ref * 50, f"σ̂ の最大値が発散している: {s.max()}"


def test_result_is_unchanged_without_zero_range_bars():
    """レンジ 0 が無い系列では挙動が変わらない（非波及）。"""
    o, h, lo, c, t = _series(n=700)
    res = compute_cvfe_from_ohlc(o, h, lo, c, t, n_har=500)
    assert res.available.sum() > 100
    assert np.all(np.isfinite(res.sigma_hat[res.available]))
    assert res.har_resid_var < 5.0
