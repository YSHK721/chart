"""UC-R2 causal_compute: fake CausalComputePort 注入の AAA。

proto do_compute 忠実: load_source → RevealClock.truncate(untilTime) → tail(limit) →
空なら [] → mode=='latest' なら FormingBar.apply(forming) → compute。
"""
from __future__ import annotations

from simulator.replay_ui.usecase.causal_compute import (
    CausalComputeRequest,
    causal_compute,
)


class _FakeComputePort:
    def __init__(self, source, unknown=False):
        self._source = source
        self._unknown = unknown
        self.compute_args = None

    def load_source(self, ref, timeframe):
        if self._unknown:
            raise ValueError(f"unknown datasetRef {ref!r}")
        return [dict(b) for b in self._source]

    def compute(self, indicator, variant, mode, bars, params):
        self.compute_args = {
            "indicator": indicator,
            "variant": variant,
            "mode": mode,
            "bars": bars,
            "params": params,
        }
        return [{"name": "MA", "kind": "line", "data": [b["time"] for b in bars]}]


def _source():
    return [
        {"time": 0, "open": 1.0, "high": 2.0, "low": 0.5, "close": 1.5},
        {"time": 60, "open": 1.5, "high": 2.5, "low": 1.0, "close": 2.0},
        {"time": 120, "open": 2.0, "high": 3.0, "low": 1.5, "close": 2.5},
    ]


def _req(**kw):
    base = dict(
        indicator="moving_averages",
        variant="default",
        ref="jp225_tick",
        timeframe="1D",
        limit=None,
        until_time=None,
        mode=None,
        forming=None,
        params={"length": 9},
    )
    base.update(kw)
    return CausalComputeRequest(**base)


def test_full_mode_computes_over_all_source():
    port = _FakeComputePort(_source())
    out = causal_compute(request=_req(), compute_port=port)
    assert port.compute_args["mode"] == "full"
    assert out[0]["data"] == [0, 60, 120]


def test_until_time_truncates_before_compute():
    # untilTime=60 → time<=60 のみ計算対象。
    port = _FakeComputePort(_source())
    causal_compute(request=_req(until_time=60), compute_port=port)
    assert port.compute_args["bars"][-1]["time"] == 60
    assert [b["time"] for b in port.compute_args["bars"]] == [0, 60]


def test_limit_tail_applied_after_truncate():
    port = _FakeComputePort(_source())
    causal_compute(request=_req(limit=2), compute_port=port)
    assert [b["time"] for b in port.compute_args["bars"]] == [60, 120]


def test_empty_after_truncate_returns_empty_without_compute():
    port = _FakeComputePort(_source())
    out = causal_compute(request=_req(until_time=-1), compute_port=port)
    assert out == []
    assert port.compute_args is None


def test_latest_mode_applies_forming_then_computes_latest():
    port = _FakeComputePort(_source())
    forming = {"time": 120, "open": 2.0, "high": 9.0, "low": 1.0, "close": 8.0}
    causal_compute(
        request=_req(mode="latest", forming=forming), compute_port=port
    )
    assert port.compute_args["mode"] == "latest"
    # forming が末尾を暫定 OHLC で置換して compute へ渡る。
    assert port.compute_args["bars"][-1]["high"] == 9.0
    assert port.compute_args["bars"][-1]["close"] == 8.0


def test_latest_mode_without_forming_leaves_source_intact():
    port = _FakeComputePort(_source())
    causal_compute(request=_req(mode="latest", forming=None), compute_port=port)
    assert port.compute_args["mode"] == "latest"
    assert [b["time"] for b in port.compute_args["bars"]] == [0, 60, 120]
    assert port.compute_args["bars"][-1]["close"] == 2.5


def test_unknown_ref_propagates_valueerror():
    port = _FakeComputePort(_source(), unknown=True)
    try:
        causal_compute(request=_req(), compute_port=port)
        assert False, "expected ValueError"
    except ValueError:
        pass
