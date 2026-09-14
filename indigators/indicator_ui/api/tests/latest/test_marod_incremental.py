"""S5 検証 — ma_marod / btlm_trail_marod の増分計算が full と bit 一致することを固定する。

通過条件（内部設計_latest増分計算.md §6.1 / B-5）: 全系列 max_dev = 0。対象は本体（乖離率）・
0% 基準線（horizontal_line）・分位バンド 2 本・イベント分位水準線 4 本（evq med/ext × hi/lo）。

固定する不変条件:
  1. パラメータ行列（基準線種別 × window_n × q_out × k_events × event_agg）で latest == full。
  2. 足内更新の非破壊性（同一確定状態から形成中バー 10 通り）。
  3. バー確定の前進（窓を 1 本ずつ伸長）— イベント列（エピソード declustering）の状態が
     参照実装と同一に進むことを、水準線の一致で確認する。
  4. 対象外の入力は従来経路へ落ち、挙動が変わらないこと。
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from adapter.compute import IndicatorComputeAdapter
from adapter.compute import incremental_state
from adapter.compute.latest_dispatch import full_compute, latest_compute
from adapter.compute.latest_meta import latest_meta

_IDS = ["ma_marod", "btlm_trail_marod"]


@pytest.fixture(autouse=True)
def _clean_state():
    incremental_state.reset()
    yield
    incremental_state.reset()


def _ohlcv(n: int = 400, seed: int = 3) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    close = 39000.0 + np.cumsum(rng.normal(0.0, 25.0, n))
    open_ = np.concatenate([[close[0]], close[:-1]])
    high = np.maximum(open_, close) + np.abs(rng.normal(0.0, 8.0, n))
    low = np.minimum(open_, close) - np.abs(rng.normal(0.0, 8.0, n))
    return pd.DataFrame(
        {"open": open_, "high": high, "low": low, "close": close,
         "volume": np.full(n, 1000.0)},
        index=pd.date_range("2024-01-01", periods=n, freq="h", name="time"),
    )


def _params(compute_id: str, **overrides) -> dict:
    common = {
        "source": "close", "q_low": 0.05, "q_high": 0.95, "q_out": 0.99,
        "k_events": 50, "event_agg": "episode", "window_n": 200,
    }
    if compute_id == "ma_marod":
        common.update({"ma_type": "ema", "length": 50, "color": "rgba(255, 152, 0, 1)"})
    else:
        common.update({"maxbars": 100, "color": "rgba(123, 104, 238, 1)"})
    common.update(overrides)
    return common


def _assert_tail_matches_full(adapter, compute_id, df, params, k=1, *, min_tail=None,
                              incremental=True):
    full = full_compute(adapter, compute_id, "default", df, dict(params))
    latest = latest_compute(
        adapter, compute_id, "default", df, dict(params), min_tail=min_tail
    )
    if incremental:
        assert incremental_state.stats()["states"] >= 1, "増分経路が使われていること"
    assert [s["name"] for s in latest] == [s["name"] for s in full]
    for got, want in zip(latest, full):
        if "data" not in want:  # 0% 基準線（horizontal_line）は素通し。
            assert got == want
            continue
        assert {kk: vv for kk, vv in got.items() if kk != "data"} == {
            kk: vv for kk, vv in want.items() if kk != "data"
        }, f"系列 metadata が full と一致すること（{got['name']}）"
        assert got["data"] == want["data"][-k:], (
            f"末尾 {k} 点が full と完全一致すること（系列 {got['name']}）"
        )
    return latest


@pytest.mark.parametrize("compute_id", _IDS)
def test_declared_as_incremental(compute_id):
    meta = latest_meta(compute_id, "default", _params(compute_id))
    assert meta.archetype == "incremental"
    assert meta.incremental == compute_id
    assert meta.min_window is None
    assert meta.trailing_k == 1


# =========================================================================== #
# 1. 全系列 max_dev = 0
# =========================================================================== #
@pytest.mark.parametrize("compute_id", _IDS)
@pytest.mark.parametrize("window_n", [50, 200])
def test_latest_equals_full_exactly(compute_id, window_n):
    adapter = IndicatorComputeAdapter()
    _assert_tail_matches_full(
        adapter, compute_id, _ohlcv(400), _params(compute_id, window_n=window_n)
    )


@pytest.mark.parametrize("ma_type", ["sma", "ema", "smma", "lwma"])
def test_ma_marod_latest_equals_full_for_all_ma_types(ma_type):
    adapter = IndicatorComputeAdapter()
    _assert_tail_matches_full(
        adapter, "ma_marod", _ohlcv(400), _params("ma_marod", ma_type=ma_type, length=30)
    )


@pytest.mark.parametrize("compute_id", _IDS)
@pytest.mark.parametrize("event_agg", ["episode", "bar"])
def test_latest_equals_full_with_event_agg(compute_id, event_agg):
    adapter = IndicatorComputeAdapter()
    _assert_tail_matches_full(
        adapter, compute_id, _ohlcv(400),
        _params(compute_id, event_agg=event_agg, k_events=10),
    )


@pytest.mark.parametrize("compute_id", _IDS)
@pytest.mark.parametrize("q_out", [None, 0.5, 0.99, 1.5])
def test_latest_equals_full_with_q_out(compute_id, q_out):
    adapter = IndicatorComputeAdapter()
    _assert_tail_matches_full(
        adapter, compute_id, _ohlcv(400), _params(compute_id, q_out=q_out)
    )


@pytest.mark.parametrize("compute_id", _IDS)
@pytest.mark.parametrize("source", ["close", "open", "hl2", "hlcc4"])
def test_latest_equals_full_with_source(compute_id, source):
    adapter = IndicatorComputeAdapter()
    _assert_tail_matches_full(
        adapter, compute_id, _ohlcv(400), _params(compute_id, source=source)
    )


@pytest.mark.parametrize("compute_id", _IDS)
@pytest.mark.parametrize("min_tail", [2, 5, 30])
def test_latest_equals_full_with_min_tail(compute_id, min_tail):
    adapter = IndicatorComputeAdapter()
    _assert_tail_matches_full(
        adapter, compute_id, _ohlcv(400), _params(compute_id),
        k=min_tail, min_tail=min_tail,
    )


# =========================================================================== #
# 2. 足内更新の非破壊性
# =========================================================================== #
@pytest.mark.parametrize("compute_id", _IDS)
def test_intrabar_steps_are_non_destructive(compute_id):
    adapter = IndicatorComputeAdapter()
    base = _ohlcv(400)
    params = _params(compute_id)
    _assert_tail_matches_full(adapter, compute_id, base, params)
    for i in range(10):
        df = base.copy()
        delta = (i - 5) * 30.0  # バンド超（イベント）を跨ぐ振れ幅を含める。
        for col in ("open", "high", "low", "close"):
            df.iloc[-1, df.columns.get_loc(col)] = base.iloc[-1][col] + delta
        _assert_tail_matches_full(adapter, compute_id, df, params)


# =========================================================================== #
# 3. バー確定の前進（イベント列の状態が参照実装と同一に進む）
# =========================================================================== #
@pytest.mark.parametrize("compute_id", _IDS)
def test_bar_advance_keeps_exact_match(compute_id):
    adapter = IndicatorComputeAdapter()
    base = _ohlcv(400)
    params = _params(compute_id, k_events=10)
    for n in range(320, 336):
        _assert_tail_matches_full(adapter, compute_id, base.iloc[:n], params)
    assert incremental_state.stats()["states"] == 1


@pytest.mark.parametrize("compute_id", _IDS)
def test_window_shrink_and_regrow_keeps_exact_match(compute_id):
    adapter = IndicatorComputeAdapter()
    base = _ohlcv(400)
    params = _params(compute_id, k_events=10)
    _assert_tail_matches_full(adapter, compute_id, base, params)
    for n in (300, 301, 400):
        _assert_tail_matches_full(adapter, compute_id, base.iloc[:n], params)


def test_ma_marod_lwma_window_shrink_rebuilds_and_stays_exact():
    # lwma の走行和は巻き戻せないため状態を再構築する。値は常に full と一致する。
    adapter = IndicatorComputeAdapter()
    base = _ohlcv(400)
    params = _params("ma_marod", ma_type="lwma", length=30)
    _assert_tail_matches_full(adapter, "ma_marod", base, params)
    for n in (300, 301, 400):
        _assert_tail_matches_full(adapter, "ma_marod", base.iloc[:n], params)


# =========================================================================== #
# 4. 対象外の入力は従来経路（挙動不変）
# =========================================================================== #
@pytest.mark.parametrize("compute_id", _IDS)
def test_short_window_falls_back_to_full_path_and_matches(compute_id):
    adapter = IndicatorComputeAdapter()
    params = _params(compute_id, window_n=10)
    n = 53 if compute_id == "ma_marod" else 102   # 基準線 warm-up の直後（増分の下限未満）
    _assert_tail_matches_full(
        adapter, compute_id, _ohlcv(n), params, incremental=False
    )
    assert incremental_state.stats()["states"] == 0


@pytest.mark.parametrize("compute_id", _IDS)
def test_invalid_quantile_pair_raises_same_error_as_full_path(compute_id):
    from adapter.compute.indicator_compute_adapter import ComputeError

    adapter = IndicatorComputeAdapter()
    df = _ohlcv(400)
    params = _params(compute_id, q_low=0.9, q_high=0.1)
    with pytest.raises(ComputeError) as full_exc:
        full_compute(adapter, compute_id, "default", df, dict(params))
    with pytest.raises(ComputeError) as latest_exc:
        latest_compute(adapter, compute_id, "default", df, dict(params))
    assert latest_exc.value.error_type == full_exc.value.error_type
