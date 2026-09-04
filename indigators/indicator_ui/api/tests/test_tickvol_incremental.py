"""tickvol 増分計算（ISSUE-239）の検証。

不変条件（最重要・ISSUE-233 と同一）:
    latest の末尾 K 点が full の対応点と **完全一致** する。水準（経験的分位・GPD）は
    確定イベントのみから決まるため、形成中バーの差し替えでは動かない。

そのほか固定する契約:
    - 未対応パラメータ・本数不足は ``prepare`` が None を返し従来経路へ落ちる（挙動不変）。
    - ``emit`` は非破壊（同じ確定状態から何度呼んでも状態が進まない＝足内更新の前提）。
    - 足内更新の 1 ステップで GPD の再当てはめが起きない（水準は状態遷移時に 1 度だけ求める）。

import 規約: conftest.py が api/ を sys.path へ追加済み。既存 src は read-only。
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from adapter.compute import IndicatorComputeAdapter, incremental_state
from adapter.compute.latest_dispatch import full_compute, latest_compute
from adapter.compute.incremental.tickvol import TickvolIncrementer

_PARAMS = {"window_n": 50, "q_low": 0.20, "q_high": 0.80, "q_out": 0.95,
           "k_events": 40}


def _ohlcv(n: int = 1200, seed: int = 7) -> pd.DataFrame:
    """定常な下地に周期スパイク・連続超過を混ぜた OHLCV（tickvol テストと同流儀）。"""
    rng = np.random.default_rng(seed)
    vol = rng.gamma(shape=2.0, scale=100.0, size=n) + 10.0
    vol[::17] *= 3.0
    for s in range(0, n, 101):
        vol[s:s + 4] *= 4.0
    idx = pd.date_range("2026-01-05 00:00:00", periods=n, freq="5min")
    base = 100.0 + np.sin(np.linspace(0.0, 6.0 * np.pi, n))
    return pd.DataFrame(
        {"open": base, "high": base + 1.0, "low": base - 1.0, "close": base + 0.5,
         "volume": np.round(vol)},
        index=idx,
    )


def _tails(series: list) -> dict:
    return {s["name"]: (s.get("data") or [])[-1:] for s in series}


@pytest.fixture(autouse=True)
def _clean_state():
    incremental_state.reset()
    yield
    incremental_state.reset()


def test_latest_matches_full_exactly_for_every_series():
    adapter = IndicatorComputeAdapter()
    df = _ohlcv()
    full = full_compute(adapter, "tickvol", "default", df, dict(_PARAMS))
    latest = latest_compute(adapter, "tickvol", "default", df, dict(_PARAMS))
    assert {s["name"] for s in latest} == {s["name"] for s in full}
    assert _tails(latest) == _tails(full)


def test_latest_matches_full_across_sequential_bar_advances():
    # 状態を使い回しながらバーを送っても full と一致し続ける（adapt の前進が正しい）。
    adapter = IndicatorComputeAdapter()
    base = _ohlcv(1200)
    for n in range(1150, 1200):
        df = base.iloc[:n]
        assert _tails(latest_compute(adapter, "tickvol", "default", df, dict(_PARAMS))) == \
            _tails(full_compute(adapter, "tickvol", "default", df, dict(_PARAMS))), n


def test_latest_matches_full_after_a_rewind():
    # リプレイの巻き戻し（確定バーが減る）でも adapt が正しく縮める。
    adapter = IndicatorComputeAdapter()
    base = _ohlcv(1200)
    latest_compute(adapter, "tickvol", "default", base, dict(_PARAMS))
    short = base.iloc[:1000]
    assert _tails(latest_compute(adapter, "tickvol", "default", short, dict(_PARAMS))) == \
        _tails(full_compute(adapter, "tickvol", "default", short, dict(_PARAMS)))


@pytest.mark.parametrize("params", [
    {},                                                        # 既定値
    {"window_n": 200, "q_low": 0.05, "q_high": 0.85, "q_out": 0.99, "k_events": 30},
    {"q_out": None},                                           # 極端分位オフ
    {"q_out": 0.5},                                            # 無効値（黙ってオフ）
])
def test_latest_matches_full_for_parameter_variations(params):
    adapter = IndicatorComputeAdapter()
    df = _ohlcv()
    assert _tails(latest_compute(adapter, "tickvol", "default", df, dict(params))) == \
        _tails(full_compute(adapter, "tickvol", "default", df, dict(params)))


def test_forming_bar_replacement_moves_only_the_histogram():
    # 足内更新: 形成中バーの tick 数だけが動き、水準 3 本は動かない（確定イベント依存）。
    adapter = IndicatorComputeAdapter()
    df = _ohlcv()
    first = _tails(latest_compute(adapter, "tickvol", "default", df, dict(_PARAMS)))
    bumped = df.copy()
    bumped.iloc[-1, bumped.columns.get_loc("volume")] += 5000.0
    second = _tails(latest_compute(adapter, "tickvol", "default", bumped, dict(_PARAMS)))

    assert first["tickvol"] != second["tickvol"]
    # 正常帯・水準はいずれも確定系列／確定イベントのみに依存する（足内で動かない）。
    for name in ("tickvol_q20", "tickvol_q80",
                 "tickvol_evq_med_hi", "tickvol_evq_ext_hi", "tickvol_gpd_hi"):
        assert first[name] == second[name], name


def test_emit_is_non_destructive():
    # 同じ確定状態から何度 emit しても結果が変わらない（状態を進めていない）。
    adapter = IndicatorComputeAdapter()
    df = _ohlcv()
    runs = [_tails(latest_compute(adapter, "tickvol", "default", df, dict(_PARAMS)))
            for _ in range(4)]
    assert all(r == runs[0] for r in runs)


def test_intrabar_step_does_not_refit_the_gpd(monkeypatch):
    # 水準は状態遷移時に 1 度だけ求める＝足内の 1 ステップで GPD 当てはめは 0 回。
    from common import gpd as gpd_mod

    adapter = IndicatorComputeAdapter()
    df = _ohlcv()
    latest_compute(adapter, "tickvol", "default", df, dict(_PARAMS))  # 状態構築

    calls = {"n": 0}
    original = gpd_mod.gpd_fit

    def counted(excess):
        calls["n"] += 1
        return original(excess)

    monkeypatch.setattr(gpd_mod, "gpd_fit", counted)
    bumped = df.copy()
    bumped.iloc[-1, bumped.columns.get_loc("volume")] += 1234.0
    latest_compute(adapter, "tickvol", "default", bumped, dict(_PARAMS))
    assert calls["n"] == 0


@pytest.mark.parametrize("params", [
    {"window_n": 1},          # MIN_STAT_OBS 未満
    {"q_high": 1.5},          # 範囲外
    {"q_low": 0.9, "q_high": 0.5},   # 分位ペアの順序違反
    {"k_events": 0},          # 1 未満
    {"time_column": "date"},  # 時刻列の明示指定は未対応
])
def test_prepare_declines_unsupported_parameters(params):
    assert TickvolIncrementer().prepare(_ohlcv(200), dict(params)) is None


def test_prepare_declines_without_volume_column():
    assert TickvolIncrementer().prepare(_ohlcv(200).drop(columns=["volume"]), {}) is None


def test_prepare_declines_too_few_bars():
    assert TickvolIncrementer().prepare(_ohlcv(2), {}) is None
