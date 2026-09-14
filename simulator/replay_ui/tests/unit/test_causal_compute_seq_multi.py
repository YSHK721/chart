"""UC causal_compute_seq_multi（ISSUE-300）: 複数指標の足内一括計算の AAA。

固定する契約:
  1. **同値**: 各 instanceId の結果は、同じ入力で ``causal_compute_seq`` を指標ごとに呼んだ
     ものと 1 点ずつ一致する（値が変わってはならない）。
  2. **共有**: チャート足 C の窓ロードは指標数に依らず 1 回。計算足 H の窓素材は
     **計算足ごとに 1 回**（同じ 1D を要求する指標が何本あっても 1 回）。ここが本機能の目的で、
     回帰すると 1 足あたりの直列処理時間が指標数に比例して戻る。
  3. 空 forming_seq / 空 specs は {}（呼び出しを無害化）。
"""
from __future__ import annotations

from simulator.replay_ui.usecase.causal_compute import (
    CausalComputeSeqMultiRequest,
    CausalComputeSeqRequest,
    CausalComputeSeqSpec,
    causal_compute_seq,
    causal_compute_seq_multi,
)


class _FakeComputePort:
    """load_source の呼び出しを (ref, timeframe) 別に数える fake。"""

    def __init__(self, by_tf):
        self._by_tf = by_tf
        self.load_calls = []

    def load_source(self, ref, timeframe):
        self.load_calls.append(timeframe)
        return [dict(b) for b in self._by_tf[timeframe]]

    def compute(self, indicator, variant, mode, bars, params):
        return [{
            "name": f"{indicator}:{params.get('k', '')}", "kind": "line",
            "data": [{"time": bars[-1]["time"], "value": bars[-1]["close"] + params.get("k", 0)}],
        }]

    def compute_latest_seq(self, indicator, variant, prefix_bars, tails, params):
        return [
            self.compute(indicator, variant, "latest", list(prefix_bars) + list(tail), params)
            for tail in tails
        ]

    # 1D はラベル＝当日 00:00・始端も同じ（テスト用の最小規約）。
    def bar_time(self, timeframe, unix_sec):
        if timeframe != "1D":
            return int(unix_sec)
        return (int(unix_sec) // 86400) * 86400

    def period_start(self, timeframe, unix_sec):
        return self.bar_time(timeframe, unix_sec)


C_BARS = [
    {"time": 0, "open": 1.0, "high": 2.0, "low": 0.5, "close": 1.5},
    {"time": 60, "open": 1.5, "high": 2.5, "low": 1.0, "close": 2.0},
    {"time": 120, "open": 2.0, "high": 3.0, "low": 1.5, "close": 2.5},
]
H_BARS = [
    {"time": 0, "open": 1.0, "high": 3.0, "low": 0.5, "close": 2.5},
    {"time": 86400, "open": 2.5, "high": 4.0, "low": 2.0, "close": 3.0},
]
SEQ = [
    {"time": 120, "open": 2.0, "high": 2.1, "low": 2.0, "close": 2.1},
    {"time": 120, "open": 2.0, "high": 2.4, "low": 2.0, "close": 2.4},
]
SPECS = [
    CausalComputeSeqSpec("a#1", "ind_a", "default", {"k": 1}),
    CausalComputeSeqSpec("b#1", "ind_b", "default", {"k": 2}),
    CausalComputeSeqSpec("c#1", "ind_c", "default", {"k": 3}, compute_timeframe="1D"),
    CausalComputeSeqSpec("d#1", "ind_d", "default", {"k": 4}, compute_timeframe="1D"),
]


def _port():
    return _FakeComputePort({"1m": C_BARS, "1D": H_BARS})


def _multi_req(**kw):
    base = dict(
        ref="jp225_m1", timeframe="1m", limit=3, until_time=120,
        forming_seq=SEQ, specs=SPECS,
    )
    base.update(kw)
    return CausalComputeSeqMultiRequest(**base)


def _single(spec, port):
    return causal_compute_seq(
        request=CausalComputeSeqRequest(
            indicator=spec.indicator, variant=spec.variant, ref="jp225_m1", timeframe="1m",
            limit=3, until_time=120, forming_seq=SEQ, params=spec.params,
            compute_timeframe=spec.compute_timeframe,
        ),
        compute_port=port,
    )


def test_一括は指標ごとの単発と同値である():
    # Arrange
    port_multi, port_single = _port(), _port()
    # Act
    got = causal_compute_seq_multi(request=_multi_req(), compute_port=port_multi)
    # Assert
    for spec in SPECS:
        assert got[spec.instance_id] == _single(spec, port_single), spec.instance_id


def test_窓のロード回数は指標数に依らない():
    # Arrange: 同じ構成を 2 倍（8 本）にしても、共有できる仕事は増えないはず。
    doubled = [
        CausalComputeSeqSpec(f"{s.instance_id}x", s.indicator, s.variant, dict(s.params, k=s.params["k"] + 10), s.compute_timeframe)
        for s in SPECS
    ]
    port4, port8 = _port(), _port()
    # Act
    causal_compute_seq_multi(request=_multi_req(), compute_port=port4)
    causal_compute_seq_multi(request=_multi_req(specs=SPECS + doubled), compute_port=port8)
    # Assert: 4 本でも 8 本でも load_source の回数は同じ（＝指標数に比例しない）。
    assert port4.load_calls == port8.load_calls, (port4.load_calls, port8.load_calls)


def test_計算足の窓素材は計算足ごとに1回である():
    # Arrange: 1D を要求する指標が 2 本あるが、H の素材づくりは 1 回でよい。
    port = _port()
    # Act
    causal_compute_seq_multi(request=_multi_req(), compute_port=port)
    # Assert: H(1D) の load は 1 回（1D 指標 2 本ぶん重ねて呼ばない）。
    assert port.load_calls.count("1D") == 1, port.load_calls


def test_空入力は無害である():
    port = _port()
    assert causal_compute_seq_multi(request=_multi_req(forming_seq=[]), compute_port=port) == {}
    assert causal_compute_seq_multi(request=_multi_req(specs=[]), compute_port=port) == {}
    assert port.load_calls == []
