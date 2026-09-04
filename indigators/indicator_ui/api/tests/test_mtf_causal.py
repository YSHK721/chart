"""上位足（MTF）指標の因果系列（ISSUE-294 / 295）— ライブ・リプレイ共通の唯一源。

固定する規約:

    value(τ) = 指標( [τ の期間より前の確定 H 足] + [τ の期間の C 足を τ まで畳んだ H 足] )

これにより各点は自分より後のデータに依存しない（**時刻不変**）。従来の投影
（:mod:`adapter.compute.mtf_projection`）は点の意味が「その期間の値」であったため、過去の
バーの点にそのバーより後の情報が載っていた（各期間がその期間の最終値で塗り潰される）。

Fake は 1D と同型の **ラベル ≠ 期間始端** を再現する（label = 期間右端の深夜／始端 = label-3h）。
属否をラベルで判定すると期間前半の C 足が 1 本も選ばれない（ISSUE-292 の実測）。
構造: Arrange-Act-Assert（AAA）。
"""
from __future__ import annotations

from adapter.compute.mtf_causal import causal_mtf_series, fold_bars, group_by_period

HOUR = 3600
DAY = 86400

_CHART = [
    {"time": 9 * DAY - 2 * HOUR, "open": 1, "high": 2, "low": 0.5, "close": 1.5, "volume": 1},
    {"time": 9 * DAY + 1 * HOUR, "open": 1.5, "high": 1.9, "low": 1.2, "close": 1.6, "volume": 1},
    {"time": 10 * DAY - 2 * HOUR, "open": 2, "high": 3, "low": 1.8, "close": 2.5, "volume": 1},
    {"time": 10 * DAY - 1 * HOUR, "open": 2.5, "high": 4, "low": 2.4, "close": 3.0, "volume": 2},
]
_SOURCE = [
    {"time": 9 * DAY, "open": 1, "high": 2, "low": 0.5, "close": 1.5, "volume": 10},
    # 進行中期間の H 足＝期間全体の OHLC（そのバーの時点では知り得ない値）。使われてはならない。
    {"time": 10 * DAY, "open": 2, "high": 99, "low": 0.1, "close": 50, "volume": 99},
]


def _label(tf: str, unix_sec: int) -> int:
    """1D セッション足と同型のラベル（期間の右端の深夜）。"""
    return ((int(unix_sec) + 3 * HOUR) // DAY) * DAY


class _Recorder:
    """latest_seq の呼び出し（確定プレフィクス・末尾差分）を記録する。"""

    def __init__(self) -> None:
        self.calls: "list[tuple[list[int], list[dict]]]" = []

    def __call__(self, prefix_bars, tails):
        self.calls.append(([int(b["time"]) for b in prefix_bars], [t[-1] for t in tails]))
        return [[{"name": "MA", "kind": "line",
                  "data": [{"time": t[-1]["time"], "value": t[-1]["close"]}]}] for t in tails]

    def formings(self):
        return [f for _prefix, tails in self.calls for f in tails]


def _run(**over):
    rec = _Recorder()
    kwargs = dict(chart_bars=_CHART, source_bars=_SOURCE, compute_tf="1D",
                  bar_time_unix=_label, latest_seq=rec)
    kwargs.update(over)
    return causal_mtf_series(**kwargs), rec


def test_each_chart_bar_gets_the_value_computable_at_that_bar():
    out, _rec = _run()

    assert len(out) == 1 and out[0]["stepped"] is True, "期間境界は段で描く（斜線にしない）"
    assert [p["time"] for p in out[0]["data"]] == [b["time"] for b in _CHART]
    assert [p["value"] for p in out[0]["data"]] == [1.5, 1.6, 2.5, 3.0], (
        "各点はそのバーまでで畳んだ H 足の値（期間の確定値ではない）")


def test_forming_bar_accumulates_within_the_period_and_resets_at_the_boundary():
    _out, rec = _run()

    formings = rec.formings()
    assert [f["time"] for f in formings] == [9 * DAY, 9 * DAY, 10 * DAY, 10 * DAY], (
        "畳んだ足に載せる time は期間のラベル")
    assert [f["high"] for f in formings] == [2, 2, 3, 4], "期間内は累積し、境界で作り直す"
    assert [f["open"] for f in formings] == [1, 1, 2, 2], "open は期間の最初の C 足"


def test_in_progress_source_bar_is_never_used():
    """H 源の進行中期間の足（期間全体の OHLC＝未来）は確定プレフィクスへ入れない。"""
    _out, rec = _run()

    assert [prefix for prefix, _tails in rec.calls] == [[], [9 * DAY]]


def test_window_bars_limits_the_output_but_not_the_folding():
    """出力窓が期間の途中から始まっても、畳みは期間の先頭から行う。

    窓外の C 足は畳み ``acc`` へ寄与させるだけで、その時点の指標計算は発行しない
    （発行しても結果は出力に使われず捨てられる＝ISSUE-450）。よってここで固定するのは
    「発行回数」ではなく **渡された畳み足が期間の先頭から畳まれていること**と、
    **値が全窓計算と一致すること**である。前者は ``open`` が期間先頭の C 足のものか否かで
    判別できる（窓の 1 本だけで畳むと open=2.5 になる）。
    """
    out, rec = _run(window_bars=_CHART[-1:])
    full, _ = _run()

    assert [p["time"] for p in out[0]["data"]] == [_CHART[-1]["time"]], "出力は窓ぶんだけ"

    formings = rec.formings()
    assert len(formings) == 1, "窓外のバーぶんの計算は発行しない（捨てる計算を作らない）"
    assert formings[0]["open"] == 2, "畳みは期間の先頭 C 足から（窓の 1 本だけで畳んでいない）"
    assert formings[0]["high"] == 4, "畳みは窓に縛られない"
    assert formings[0]["low"] == 1.8, "期間先頭からの累積最小"
    assert formings[0]["time"] == 10 * DAY, "畳んだ足に載せる time は期間のラベル"

    assert out[0]["data"][-1]["value"] == full[0]["data"][-1]["value"], (
        "窓を絞っても最終点の値は全窓計算と一致する")


def test_values_do_not_change_when_more_data_arrives():
    """時刻不変: 後ろに C 足が増えても、既存の点の値は変わらない。"""
    before, _ = _run(chart_bars=_CHART[:3])
    after, _ = _run(chart_bars=_CHART)

    old = {p["time"]: p["value"] for p in before[0]["data"]}
    new = {p["time"]: p["value"] for p in after[0]["data"]}
    assert all(old[t] == new[t] for t in old), "過去点が塗り替わる＝記録性が壊れている"


def test_empty_inputs_return_no_series():
    assert causal_mtf_series(chart_bars=[], source_bars=_SOURCE, compute_tf="1D",
                             bar_time_unix=_label, latest_seq=_Recorder()) == []
    assert causal_mtf_series(chart_bars=_CHART, source_bars=[], compute_tf="1D",
                             bar_time_unix=_label, latest_seq=_Recorder()) == []


def test_fold_and_group_are_the_single_source_of_the_shape():
    folded = fold_bars(_CHART[2:], time=10 * DAY)
    assert (folded["open"], folded["high"], folded["low"], folded["close"], folded["volume"]) == (
        2, 4, 1.8, 3.0, 3)

    groups = group_by_period(_CHART, compute_tf="1D", bar_time_unix=_label)
    assert [(label, len(bars)) for label, bars in groups] == [(9 * DAY, 2), (10 * DAY, 2)]
