"""/live_ticks 末尾値同梱の薄殻テスト（ISSUE-250 Phase 1）。

後方互換（申告が無ければ従来応答）と、対象外の明示除外を固定する。
"""

from __future__ import annotations

import json

import pandas as pd
import pytest

from adapter.controller import live_tick_tails_controller as ctl

_TICKS = [[1_785_757_500_100, 100.0], [1_785_757_500_300, 101.0]]


class _Port:
    def __init__(self, df=None, known=True, tf_known=True):
        self._df = df
        self._known, self._tf = known, tf_known

    def is_known(self, ref): return self._known
    def is_known_timeframe(self, tf): return self._tf
    def load_dataframe(self, ref, tf): return self._df


def _df(n=10):
    return pd.DataFrame({
        "time": [1_785_757_500 - (n - 1 - i) * 900 for i in range(n)],
        "open": [100.0] * n, "high": [101.0] * n, "low": [99.0] * n,
        "close": [100.5] * n, "volume": [1.0] * n,
    })


def _q(**over):
    q = {"specs": [json.dumps([{"instanceId": "i1", "indicatorId": "profit_rsi"}])],
         "datasetRef": ["jp225_tick"], "timeframe": ["15m"]}
    q.update({k: [v] for k, v in over.items()})
    return q


@pytest.fixture
def _port(monkeypatch):
    holder = {}

    def _install(port):
        holder["p"] = port
        monkeypatch.setattr(ctl, "_dataset_port", lambda: port)
    return _install


def test_returns_none_without_specs():
    assert ctl.handle_live_tick_tails({}, _TICKS) is None


def test_returns_none_without_ticks(_port):
    _port(_Port(_df()))
    assert ctl.handle_live_tick_tails(_q(), []) is None


@pytest.mark.parametrize("tf", ["1W", "1M", "unknown", None])
def test_returns_none_for_non_fixed_period_timeframe(tf, _port):
    _port(_Port(_df()))
    q = _q()
    q["timeframe"] = [tf] if tf is not None else []
    assert ctl.handle_live_tick_tails(q, _TICKS) is None


def test_returns_none_for_malformed_specs_json(_port):
    _port(_Port(_df()))
    assert ctl.handle_live_tick_tails(_q(specs="{not json"), _TICKS) is None


def test_returns_none_for_unknown_dataset(_port):
    _port(_Port(_df(), known=False))
    assert ctl.handle_live_tick_tails(_q(), _TICKS) is None


def test_returns_none_for_empty_window(_port):
    _port(_Port(_df(0)))
    assert ctl.handle_live_tick_tails(_q(), _TICKS) is None


def test_emits_one_entry_per_tick_and_drops_non_incremental(_port):
    _port(_Port(_df(400)))
    specs = json.dumps([
        {"instanceId": "rsi", "indicatorId": "profit_rsi",
         "params": {"rsi_period": 6, "apply": 5, "window_n": 500,
                    "q_low": 0.10, "q_high": 0.90, "q_out": 0.99, "k_events": 50}},
        {"instanceId": "stc", "indicatorId": "profit_stc", "params": {}},
    ])
    out = ctl.handle_live_tick_tails(_q(specs=specs), _TICKS)
    assert out is not None and len(out) == len(_TICKS)
    assert [e["tickMs"] for e in out] == [t[0] for t in _TICKS]
    assert "stc" not in out[0]["tails"]      # 増分器なし＝明示的に落とす
