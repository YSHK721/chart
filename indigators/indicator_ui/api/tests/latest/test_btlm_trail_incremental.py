"""S2/S3/S4 検証 — btlm_trail の増分計算が full と bit 一致することを固定する（ISSUE-233）。

通過条件（内部設計_latest増分計算.md §6.1 / B-5）: 全系列 max_dev = 0（`evq` 相当の
`btlm_trail_off_hi/lo`・`q5`/`q95`・`beta`/`sigma`/`band_hit_rate` を含む）。

固定する不変条件:
  1. 実測構成（band_method=empirical・maxbars=115・empirical_n=495・n_cov=495）を含む
     パラメータ行列で latest（増分）== full の末尾 K 点。
  2. 足内更新の非破壊性（同一確定状態から形成中バー 10 通り）。
  3. バー確定の前進（窓を 1 本ずつ伸長）と窓の縮小/再伸長。
  4. 増分器が扱えない入力は従来経路へ落ち、挙動が変わらないこと。
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from adapter.compute import IndicatorComputeAdapter
from adapter.compute import incremental_state
from adapter.compute.latest_dispatch import full_compute, latest_compute
from adapter.compute.latest_meta import latest_meta


@pytest.fixture(autouse=True)
def _clean_state():
    incremental_state.reset()
    yield
    incremental_state.reset()


def _ohlcv(n: int = 400, seed: int = 0) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    close = 39000.0 + np.cumsum(rng.normal(0.0, 12.0, n))
    open_ = np.concatenate([[close[0]], close[:-1]])
    high = np.maximum(open_, close) + np.abs(rng.normal(0.0, 4.0, n))
    low = np.minimum(open_, close) - np.abs(rng.normal(0.0, 4.0, n))
    return pd.DataFrame(
        {"open": open_, "high": high, "low": low, "close": close,
         "volume": np.full(n, 1000.0)},
        index=pd.date_range("2024-01-01", periods=n, freq="h", name="time"),
    )


def _params(**overrides) -> dict:
    p = {
        "source": "close", "maxbars": 100, "q_low": 0.05, "q_high": 0.95,
        "band_method": "ols", "empirical_n": 500, "q_out": None,
        "show_metrics": True, "n_cov": 250,
        "color": "rgba(123, 104, 238, 1)",
    }
    p.update(overrides)
    return p


def _assert_tail_matches_full(adapter, df, params, k=1, *, min_tail=None, incremental=True):
    full = full_compute(adapter, "btlm_trail", "default", df, dict(params))
    latest = latest_compute(
        adapter, "btlm_trail", "default", df, dict(params), min_tail=min_tail
    )
    if incremental:
        assert incremental_state.stats()["states"] >= 1, "増分経路が使われていること"
    assert [s["name"] for s in latest] == [s["name"] for s in full]
    for got, want in zip(latest, full):
        assert {kk: vv for kk, vv in got.items() if kk != "data"} == {
            kk: vv for kk, vv in want.items() if kk != "data"
        }, f"系列 metadata が full と一致すること（{got['name']}）"
        assert got["data"] == want["data"][-k:], (
            f"末尾 {k} 点が full と完全一致すること（系列 {got['name']}）"
        )
    return latest


def test_declared_as_incremental():
    meta = latest_meta("btlm_trail", "default", _params())
    assert meta.archetype == "incremental"
    assert meta.incremental == "btlm_trail"
    assert meta.min_window is None
    assert meta.trailing_k == 1


# =========================================================================== #
# 1. 全系列 max_dev = 0
# =========================================================================== #
@pytest.mark.parametrize("band_method", ["ols", "empirical"])
@pytest.mark.parametrize("maxbars", [50, 100, 115])
def test_latest_equals_full_exactly(band_method, maxbars):
    adapter = IndicatorComputeAdapter()
    _assert_tail_matches_full(
        adapter, _ohlcv(400),
        _params(band_method=band_method, maxbars=maxbars, empirical_n=200, n_cov=150),
    )


def test_latest_equals_full_for_measured_configuration():
    # ISSUE-233 の実測構成（empirical・maxbars=115・empirical_n=495・n_cov=495）。
    adapter = IndicatorComputeAdapter()
    _assert_tail_matches_full(
        adapter, _ohlcv(1000),
        _params(band_method="empirical", maxbars=115, empirical_n=495, n_cov=495),
    )


@pytest.mark.parametrize("band_method", ["ols", "empirical"])
@pytest.mark.parametrize("q_out", [None, 0.5, 0.99, 1.5])
def test_latest_equals_full_with_q_out(band_method, q_out):
    # q_out は q_high < q_out < 1 のときだけ有効（それ以外は黙って無効化＝補助線なし）。
    adapter = IndicatorComputeAdapter()
    _assert_tail_matches_full(
        adapter, _ohlcv(400),
        _params(band_method=band_method, q_out=q_out, empirical_n=200, n_cov=150),
    )


@pytest.mark.parametrize("source", ["close", "open", "high", "low", "hl2", "hlc3", "ohlc4", "hlcc4"])
def test_latest_equals_full_with_source(source):
    adapter = IndicatorComputeAdapter()
    _assert_tail_matches_full(
        adapter, _ohlcv(400),
        _params(band_method="empirical", source=source, empirical_n=200, n_cov=150),
    )


@pytest.mark.parametrize("band_method", ["ols", "empirical"])
def test_latest_equals_full_without_metrics(band_method):
    adapter = IndicatorComputeAdapter()
    _assert_tail_matches_full(
        adapter, _ohlcv(400),
        _params(band_method=band_method, show_metrics=False, empirical_n=200, n_cov=150),
    )


@pytest.mark.parametrize("min_tail", [2, 5, 30])
def test_latest_equals_full_with_min_tail(min_tail):
    adapter = IndicatorComputeAdapter()
    _assert_tail_matches_full(
        adapter, _ohlcv(400),
        _params(band_method="empirical", empirical_n=200, n_cov=150),
        k=min_tail, min_tail=min_tail,
    )


# =========================================================================== #
# 2. 足内更新の非破壊性
# =========================================================================== #
@pytest.mark.parametrize("band_method", ["ols", "empirical"])
def test_intrabar_steps_are_non_destructive(band_method):
    adapter = IndicatorComputeAdapter()
    base = _ohlcv(400)
    params = _params(band_method=band_method, q_out=0.99, empirical_n=200, n_cov=150)
    _assert_tail_matches_full(adapter, base, params)
    for i in range(10):
        df = base.copy()
        delta = (i - 5) * 9.0
        for col in ("open", "high", "low", "close"):
            df.iloc[-1, df.columns.get_loc(col)] = base.iloc[-1][col] + delta
        _assert_tail_matches_full(adapter, df, params)


# =========================================================================== #
# 3. バー確定の前進・窓の縮小/再伸長
# =========================================================================== #
@pytest.mark.parametrize("band_method", ["ols", "empirical"])
def test_bar_advance_keeps_exact_match(band_method):
    adapter = IndicatorComputeAdapter()
    base = _ohlcv(400)
    params = _params(band_method=band_method, empirical_n=200, n_cov=150)
    for n in range(300, 312):
        _assert_tail_matches_full(adapter, base.iloc[:n], params)
    assert incremental_state.stats()["states"] == 1


@pytest.mark.parametrize("band_method", ["ols", "empirical"])
def test_window_shrink_and_regrow_keeps_exact_match(band_method):
    adapter = IndicatorComputeAdapter()
    base = _ohlcv(400)
    params = _params(band_method=band_method, empirical_n=200, n_cov=150)
    _assert_tail_matches_full(adapter, base, params)
    for n in (250, 251, 400):
        _assert_tail_matches_full(adapter, base.iloc[:n], params)


def test_left_edge_shift_rebuilds_and_stays_exact():
    adapter = IndicatorComputeAdapter()
    base = _ohlcv(400)
    params = _params(band_method="empirical", empirical_n=200, n_cov=150)
    for start in range(0, 3):
        _assert_tail_matches_full(adapter, base.iloc[start:start + 300], params)


# =========================================================================== #
# 4. 対象外の入力は従来経路（挙動不変）
# =========================================================================== #
def test_short_window_falls_back_to_full_path_and_matches():
    adapter = IndicatorComputeAdapter()
    params = _params(maxbars=100)
    _assert_tail_matches_full(adapter, _ohlcv(101), params, incremental=False)
    assert incremental_state.stats()["states"] == 0


def test_invalid_quantile_pair_raises_same_error_as_full_path():
    from adapter.compute.indicator_compute_adapter import ComputeError

    adapter = IndicatorComputeAdapter()
    df = _ohlcv(400)
    params = _params(q_low=0.9, q_high=0.1)
    with pytest.raises(ComputeError) as full_exc:
        full_compute(adapter, "btlm_trail", "default", df, dict(params))
    with pytest.raises(ComputeError) as latest_exc:
        latest_compute(adapter, "btlm_trail", "default", df, dict(params))
    assert latest_exc.value.error_type == full_exc.value.error_type
