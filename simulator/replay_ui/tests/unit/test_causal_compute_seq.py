"""UC causal_compute_seq（ISSUE-232）: 足内一括計算の AAA。

固定する契約:
  1. 窓のロード / truncate / tail は **1 回だけ**（forming_seq の要素数に依らない）。
     ここが本機能の目的（1 ステップの限界費用を指標計算だけにする）であり、回帰すると
     再生中の先読みが間に合わなくなる。
  2. 各ステップは mode='latest' の単発計算と **完全同値**（同一窓・同一 apply_forming・
     同一 latest 呼び出し）。値が変わってはならない。
  3. 空 forming_seq / 空窓は [] （呼び出しを無害化）。
"""
from __future__ import annotations

from simulator.replay_ui.usecase.causal_compute import (
    CausalComputeRequest,
    CausalComputeSeqRequest,
    causal_compute,
    causal_compute_seq,
)


class _FakeComputePort:
    """load_source の呼び出し回数と compute への引数列を記録する fake。"""

    def __init__(self, source):
        self._source = source
        self.load_calls = 0
        self.compute_calls = []

    def load_source(self, ref, timeframe):
        self.load_calls += 1
        return [dict(b) for b in self._source]

    def compute(self, indicator, variant, mode, bars, params):
        self.compute_calls.append({
            "indicator": indicator, "variant": variant, "mode": mode,
            "bars": [dict(b) for b in bars], "params": dict(params),
        })
        # 末尾バーの close をそのまま返す＝forming の反映を値で観測できるようにする。
        return [{"name": "MA", "kind": "line", "data": [{"time": bars[-1]["time"], "value": bars[-1]["close"]}]}]


def _source():
    return [
        {"time": 0, "open": 1.0, "high": 2.0, "low": 0.5, "close": 1.5},
        {"time": 60, "open": 1.5, "high": 2.5, "low": 1.0, "close": 2.0},
        {"time": 120, "open": 2.0, "high": 3.0, "low": 1.5, "close": 2.5},
    ]


def _seq():
    """足内推移（同一バー time=120 の暫定 OHLC が close だけ動く）。"""
    return [
        {"time": 120, "open": 2.0, "high": 2.1, "low": 2.0, "close": 2.1},
        {"time": 120, "open": 2.0, "high": 2.4, "low": 2.0, "close": 2.4},
        {"time": 120, "open": 2.0, "high": 2.4, "low": 1.9, "close": 1.9},
    ]


def _seq_req(**kw):
    base = dict(
        indicator="moving_averages", variant="default", ref="jp225_tick",
        timeframe="1D", limit=None, until_time=None, forming_seq=_seq(), params={},
    )
    base.update(kw)
    return CausalComputeSeqRequest(**base)


def test_窓のロードは要素数に依らず1回だけ():
    # Arrange
    port = _FakeComputePort(_source())
    # Act
    steps = causal_compute_seq(request=_seq_req(), compute_port=port)
    # Assert
    assert port.load_calls == 1, "forming_seq の要素ごとに窓をロードしている（限界費用の削減が効かない）"
    assert len(steps) == 3
    assert len(port.compute_calls) == 3


def test_各ステップは単発latestと完全同値():
    # Arrange: 同一入力で 単発 mode='latest' を 3 回 と 一括 を 1 回。
    seq = _seq()
    single_port = _FakeComputePort(_source())
    singles = [
        causal_compute(
            request=CausalComputeRequest(
                indicator="moving_averages", variant="default", ref="jp225_tick",
                timeframe="1D", limit=None, until_time=None, mode="latest",
                forming=f, params={},
            ),
            compute_port=single_port,
        )
        for f in seq
    ]
    batch_port = _FakeComputePort(_source())
    # Act
    batch = causal_compute_seq(request=_seq_req(forming_seq=seq), compute_port=batch_port)
    # Assert: 出力が同値、かつ compute へ渡った bars/params/mode も同値。
    assert batch == singles, "一括計算の値が単発 latest と一致しない（同値性の破れ）"
    assert [c["bars"] for c in batch_port.compute_calls] == [c["bars"] for c in single_port.compute_calls]
    assert all(c["mode"] == "latest" for c in batch_port.compute_calls)


def test_untilTimeとlimitは単発と同じ窓を作る():
    # Arrange: untilTime=60 で切り、limit=1 で末尾 1 本にした窓を、単発と一括で突き合わせる。
    seq = _seq()
    single_port = _FakeComputePort(_source())
    for f in seq:
        causal_compute(
            request=CausalComputeRequest(
                indicator="moving_averages", variant="default", ref="jp225_tick",
                timeframe="1D", limit=1, until_time=60, mode="latest", forming=f, params={},
            ),
            compute_port=single_port,
        )
    batch_port = _FakeComputePort(_source())
    # Act
    causal_compute_seq(request=_seq_req(until_time=60, limit=1), compute_port=batch_port)
    # Assert: 各ステップへ渡る窓が単発と 1 バーずつ一致する（truncate/tail/forming が同一）。
    assert [c["bars"] for c in batch_port.compute_calls] == [c["bars"] for c in single_port.compute_calls]


def test_空のforming_seqは計算を呼ばない():
    port = _FakeComputePort(_source())
    assert causal_compute_seq(request=_seq_req(forming_seq=[]), compute_port=port) == []
    assert port.load_calls == 0, "空要求で窓をロードしている（無駄な I/O）"
    assert port.compute_calls == []


def test_空窓は空を返す():
    port = _FakeComputePort([])
    assert causal_compute_seq(request=_seq_req(), compute_port=port) == []
    assert port.compute_calls == []
