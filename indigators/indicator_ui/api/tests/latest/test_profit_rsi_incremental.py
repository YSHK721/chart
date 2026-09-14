"""profit_rsi の増分計算テスト（ISSUE-249）。

通過条件（ISSUE-233 / ISSUE-158 で確立した規律をそのまま流用）:
  1. ``full`` を参照実装とし、``latest`` の末尾 K 点が **完全一致**（max_dev = 0・time も一致）
  2. 足内更新は非破壊（同じ確定状態から形成中バーを差し替えて何度呼んでも値が変わらない）
  3. バー確定の前進・窓の伸長で一致が保たれる
  4. 増分器が扱えない入力は従来経路へ落ち、そこでも full と一致する

import 規約: api/tests/conftest.py が api/ を sys.path へ追加済み。
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from adapter.compute import IndicatorComputeAdapter, incremental_state
from adapter.compute.latest_dispatch import full_compute, latest_compute
from adapter.compute.latest_meta import latest_meta

_COMPUTE_ID = "profit_rsi"
_VARIANT = "default"


@pytest.fixture(autouse=True)
def _clean_state():
    incremental_state.reset()
    yield
    incremental_state.reset()


def _ohlcv(n: int = 400, seed: int = 0) -> pd.DataFrame:
    """昇順 OHLC（date 列つき）。水準（正常帯・POT/GPD）が定義される長さを満たす。"""
    rng = np.random.default_rng(seed)
    close = 100.0 + np.cumsum(rng.normal(0.0, 1.0, n))
    return pd.DataFrame(
        {
            "date": pd.date_range("2024-01-01", periods=n, freq="h"),
            "open": close,
            "high": close + 1.0,
            "low": close - 1.0,
            "close": close,
            "volume": np.full(n, 1000.0),
        }
    )


def _params(**overrides) -> dict:
    p = {
        "rsi_period": 6, "apply": 5, "window_n": 500,
        "q_low": 0.10, "q_high": 0.90, "q_out": 0.99, "k_events": 50,
    }
    p.update(overrides)
    return p


def _tails(series: list, k: int = 1) -> dict:
    return {s["name"]: (s.get("data") or [])[-k:] for s in series}


def _assert_tail_matches_full(adapter, df, params, k: int = 1, *, min_tail=None) -> dict:
    """latest の末尾 K 点が full の末尾 K 点と完全一致することを固定する。"""
    full = _tails(full_compute(adapter, _COMPUTE_ID, _VARIANT, df, params), k)
    latest = _tails(
        latest_compute(adapter, _COMPUTE_ID, _VARIANT, df, params, min_tail=min_tail), k
    )
    assert set(full) == set(latest)
    assert full, "系列が空（テスト条件が不正）"
    for name, expected in full.items():
        assert latest[name] == expected, name        # time / value とも完全一致
    return latest


# --------------------------------------------------------------------------- #
# 宣言
# --------------------------------------------------------------------------- #
def test_declared_as_incremental():
    meta = latest_meta(_COMPUTE_ID, _VARIANT, _params())
    assert meta.archetype == "incremental"
    assert meta.incremental == "profit_rsi"
    assert meta.min_window is None and meta.trailing_k == 1


# --------------------------------------------------------------------------- #
# 1. full との完全一致
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("n", [200, 400, 800])
def test_latest_equals_full_exactly(n):
    _assert_tail_matches_full(IndicatorComputeAdapter(), _ohlcv(n), _params())


@pytest.mark.parametrize("rsi_period", [2, 6, 14, 50])
def test_latest_equals_full_across_rsi_period(rsi_period):
    _assert_tail_matches_full(
        IndicatorComputeAdapter(), _ohlcv(400), _params(rsi_period=rsi_period)
    )


@pytest.mark.parametrize("apply_v", [1, 2, 3, 4, 5, 6, 0])
def test_latest_equals_full_across_applied_price(apply_v):
    _assert_tail_matches_full(IndicatorComputeAdapter(), _ohlcv(400), _params(apply=apply_v))


@pytest.mark.parametrize("window_n,k_events", [(50, 5), (200, 20), (500, 50)])
def test_latest_equals_full_across_level_params(window_n, k_events):
    _assert_tail_matches_full(
        IndicatorComputeAdapter(), _ohlcv(600),
        _params(window_n=window_n, k_events=k_events),
    )


@pytest.mark.parametrize("q_out", [0.95, 0.99, None])
def test_latest_equals_full_across_q_out_including_disabled(q_out):
    _assert_tail_matches_full(IndicatorComputeAdapter(), _ohlcv(500), _params(q_out=q_out))


@pytest.mark.parametrize("min_tail", [2, 5, 30])
def test_latest_equals_full_with_min_tail(min_tail):
    """min_tail（形成中バー合成ぶんの下限点数）で末尾が広がっても full と一致する。"""
    _assert_tail_matches_full(
        IndicatorComputeAdapter(), _ohlcv(400), _params(), k=min_tail, min_tail=min_tail
    )


# --------------------------------------------------------------------------- #
# 2. 足内更新の非破壊性（同じ確定状態から形成中バーを差し替える）
# --------------------------------------------------------------------------- #
def test_intrabar_steps_are_non_destructive():
    adapter = IndicatorComputeAdapter()
    base = _ohlcv(400)
    params = _params()
    seen = []
    for bump in (0.0, 1.5, -2.0, 0.3, 0.0):
        df = base.copy()
        last = float(base["close"].iloc[-1]) + bump
        df.loc[df.index[-1], ["open", "high", "low", "close"]] = [
            last, last + 1.0, last - 1.0, last
        ]
        seen.append(_assert_tail_matches_full(adapter, df, params))
    # 同じ形成中バー（bump=0.0）へ戻したとき、初回と同一値に戻る＝状態が汚れていない。
    assert seen[0] == seen[-1]


# --------------------------------------------------------------------------- #
# 3. バー確定の前進・窓の伸長
# --------------------------------------------------------------------------- #
def test_bar_advance_keeps_exact_match():
    adapter = IndicatorComputeAdapter()
    full_df = _ohlcv(420)
    params = _params()
    for n in range(400, 420):
        _assert_tail_matches_full(adapter, full_df.iloc[:n], params)


def test_window_shrink_falls_back_and_stays_exact():
    """窓が縮むと状態を巻き戻せないため再構築へ落ちる。値は full と一致し続ける。"""
    adapter = IndicatorComputeAdapter()
    full_df = _ohlcv(420)
    params = _params()
    _assert_tail_matches_full(adapter, full_df.iloc[:420], params)
    _assert_tail_matches_full(adapter, full_df.iloc[:405], params)   # 縮小
    _assert_tail_matches_full(adapter, full_df.iloc[:420], params)   # 再伸長


def test_left_edge_shift_rebuilds_and_stays_exact():
    """左端がずれた（別プレフィクス）ときは状態を流用せず再構築し、値は一致する。"""
    adapter = IndicatorComputeAdapter()
    df = _ohlcv(420)
    params = _params()
    _assert_tail_matches_full(adapter, df.iloc[:400], params)
    _assert_tail_matches_full(adapter, df.iloc[20:420].reset_index(drop=True), params)


# --------------------------------------------------------------------------- #
# 4. 増分器が扱えない入力 → 従来経路（挙動不変）
# --------------------------------------------------------------------------- #
def test_short_window_falls_back_to_full_path_and_matches():
    """seed 未達（本数 < rsi_period + 2）は prepare が None を返し従来経路へ落ちる。"""
    _assert_tail_matches_full(IndicatorComputeAdapter(), _ohlcv(7), _params(rsi_period=6))


def test_state_cache_is_reused_across_calls():
    """2 回目以降の latest が状態を再構築しない（キャッシュが効く）ことを固定する。"""
    adapter = IndicatorComputeAdapter()
    df = _ohlcv(400)
    params = _params()
    latest_compute(adapter, _COMPUTE_ID, _VARIANT, df, params)
    before = incremental_state.stats()["states"]
    latest_compute(adapter, _COMPUTE_ID, _VARIANT, df, params)
    assert incremental_state.stats()["states"] == before >= 1
