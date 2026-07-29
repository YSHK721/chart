"""比較対象モデル M0〜M3 の検証（仕様 §5.3）。"""

import sys
from pathlib import Path

import numpy as np
import pytest

_PKG_DIR = Path(__file__).resolve().parents[1]
if str(_PKG_DIR) not in sys.path:
    sys.path.insert(0, str(_PKG_DIR))

from src.benchmarks import (  # noqa: E402
    EWMA_LAMBDA,
    MA_WINDOW,
    _fit_garch11,
    forecast_ewma,
    forecast_garch11,
    forecast_har_plain,
    forecast_moving_average,
)
from src.dto import HAR_LAG_MONTH  # noqa: E402


def test_specification_constants():
    """M0 は直近 20 本、M1 は λ = 0.94（仕様 §5.3）。"""
    assert MA_WINDOW == 20
    assert EWMA_LAMBDA == 0.94


def test_moving_average_uses_only_past_bars():
    """M0：σ̂_t = sqrt(mean(V_{t−20..t−1}))。当該バーの V_t を含まない（因果性）。"""
    v = np.arange(1.0, 101.0)
    t0 = 30
    out = forecast_moving_average(v, t0)
    assert np.all(np.isnan(out[:t0]))
    assert out[50] == pytest.approx(np.sqrt(v[30:50].mean()))


def test_moving_average_ignores_the_current_bar():
    """直近バーの値を変えても当該バーの予測は変わらない（当該バーを見ていない）。"""
    v = np.full(100, 4.0)
    a = forecast_moving_average(v, 30)
    v2 = v.copy()
    v2[50] = 1e6
    b = forecast_moving_average(v2, 30)
    assert a[50] == b[50]
    assert a[51] != b[51]          # 次バーには反映される


def test_ewma_recursion_matches_definition():
    """M1：h_t = λ h_{t−1} + (1 − λ) V_{t−1}。"""
    v = np.full(60, 9.0)
    out = forecast_ewma(v, 30)
    # 定常値は V に一致する（全要素が等しいため）。
    assert out[59] == pytest.approx(3.0, rel=1e-9)
    assert np.all(np.isnan(out[:30]))


def test_har_plain_is_causal_and_finite():
    """M3：t0 以降のみ有限で、当該バーの C_t を用いない。"""
    rng = np.random.default_rng(3)
    n, n_har = 700, 500
    c = np.exp(rng.standard_normal(n) * 0.3) * 1e-4
    p_close = np.cumsum(rng.standard_normal(n) * 0.001)
    t0 = n_har + HAR_LAG_MONTH
    out = forecast_har_plain(c, p_close, t0, n_har)
    assert np.all(np.isnan(out[:t0]))
    assert np.all(np.isfinite(out[t0:]))
    assert np.all(out[t0:] > 0.0)

    c2 = c.copy()
    c2[t0 + 5] *= 100.0            # 当該バー自身の C を変える
    out2 = forecast_har_plain(c2, p_close, t0, n_har)
    assert out[t0 + 5] == out2[t0 + 5]      # 当該バーの予測は不変
    assert out[t0 + 6] != out2[t0 + 6]      # 次バーには反映される


def test_garch11_recovers_known_parameters():
    """GARCH(1,1) の最尤推定が既知パラメータの近傍へ収束する（scipy 非依存）。"""
    omega_t, alpha_t, beta_t = 2.0e-6, 0.08, 0.90
    rng = np.random.default_rng(4)
    n = 4_000
    r = np.empty(n)
    h = omega_t / (1.0 - alpha_t - beta_t)
    for i in range(n):
        r[i] = np.sqrt(h) * rng.standard_normal()
        h = omega_t + alpha_t * r[i] ** 2 + beta_t * h

    omega, alpha, beta = _fit_garch11(r)
    assert alpha + beta < 1.0
    # 持続性 α+β は最も安定に推定される量。±0.06 以内で一致することを固定する。
    assert abs((alpha + beta) - (alpha_t + beta_t)) < 0.06, (omega, alpha, beta)


def test_garch11_forecast_is_causal():
    """M2：t0 以降のみ有限。当該バーの収益を用いない。"""
    rng = np.random.default_rng(5)
    n, n_har = 700, 500
    r = rng.standard_normal(n) * 0.01
    t0 = n_har + HAR_LAG_MONTH
    out = forecast_garch11(r, t0, n_har)
    assert np.all(np.isnan(out[:t0]))
    assert np.all(np.isfinite(out[t0:])) and np.all(out[t0:] > 0.0)

    r2 = r.copy()
    r2[t0 + 5] *= 50.0
    out2 = forecast_garch11(r2, t0, n_har)
    assert out[t0 + 5] == out2[t0 + 5]
    assert out[t0 + 6] != out2[t0 + 6]


def test_garch11_is_deterministic():
    """同一入力で 2 回推定し同一値（§6 数値再現性）。"""
    rng = np.random.default_rng(6)
    r = rng.standard_normal(1_000) * 0.01
    assert _fit_garch11(r) == _fit_garch11(r)
