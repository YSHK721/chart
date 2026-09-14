"""因果 MTF 系列のバー単位記憶（ISSUE-297）の検証。

固定する機構:
  - 同じ入力の同じ τ は 2 度計算しない（``latest_seq`` の呼び出し本数で数える）。
  - 出力は記憶の有無で変わらない（値の同一性）。
  - 入力が変われば記憶を使わない（形成中バーが伸びた／データが訂正された／確定 H 足が増えた）。
  - 記憶を渡さなければ従来どおり全バーを計算する（既定の挙動不変）。
"""

from __future__ import annotations

from adapter.compute.mtf_causal import causal_mtf_series
from adapter.compute.mtf_causal_memo import CausalBarMemo, memo_for

HOUR = 3600
DAY = 24 * HOUR

_CHART = [
    {"time": 9 * DAY - 2 * HOUR, "open": 1, "high": 2, "low": 1, "close": 1.5, "volume": 1},
    {"time": 9 * DAY - 1 * HOUR, "open": 1.5, "high": 2, "low": 1, "close": 1.6, "volume": 1},
    {"time": 10 * DAY - 2 * HOUR, "open": 2, "high": 3, "low": 2, "close": 2.5, "volume": 1},
    {"time": 10 * DAY - 1 * HOUR, "open": 2.5, "high": 4, "low": 2, "close": 3.0, "volume": 1},
]
_SOURCE = [
    {"time": 9 * DAY, "open": 1, "high": 2, "low": 1, "close": 1.6, "volume": 2},
    {"time": 10 * DAY, "open": 2, "high": 99, "low": 0.1, "close": 50, "volume": 99},
]


def _label(tf: str, unix_sec: int) -> int:
    return ((int(unix_sec) + 3 * HOUR) // DAY) * DAY


class _Counter:
    """latest_seq の呼び出しで「何時点ぶん計算したか」を数える。"""

    def __init__(self) -> None:
        self.points = 0

    def __call__(self, prefix_bars, tails):
        self.points += len(tails)
        return [[{"name": "MA", "kind": "line",
                  "data": [{"time": t[-1]["time"], "value": t[-1]["close"]}]}] for t in tails]


def _run(memo, chart=None):
    rec = _Counter()
    out = causal_mtf_series(
        chart_bars=chart if chart is not None else _CHART, source_bars=_SOURCE,
        compute_tf="1D", bar_time_unix=_label, latest_seq=rec, memo=memo,
    )
    return out, rec.points


def test_same_inputs_are_not_recomputed():
    memo = CausalBarMemo()

    first, n_first = _run(memo)
    second, n_second = _run(memo)

    assert n_first == len(_CHART), "初回は全バーを計算する"
    assert n_second == 0, "2 回目は 1 時点も計算しない（記録がそのまま使われる）"
    assert first == second, "記憶の有無で値は変わらない"


def test_without_memo_every_bar_is_computed():
    _out, n_first = _run(None)
    _out2, n_second = _run(None)

    assert n_first == n_second == len(_CHART), "記憶を渡さなければ従来どおり毎回計算する"


def test_memo_matches_the_no_memo_result():
    with_memo, _ = _run(CausalBarMemo())
    without, _ = _run(None)

    assert with_memo == without


def test_a_growing_forming_bar_is_not_reused():
    """形成中バー（値が伸びる）は畳んだ足が変わる＝別物として計算し直す。"""
    memo = CausalBarMemo()
    grown = [*_CHART[:-1], {**_CHART[-1], "high": 9, "close": 8.0}]

    _out, _n = _run(memo)
    out_grown, n_grown = _run(memo, chart=grown)

    assert n_grown == 1, "伸びた最後の 1 本だけ計算し直す"
    assert out_grown[0]["data"][-1]["value"] == 8.0, "古い値を返さない"


def test_corrected_history_is_not_reused():
    """過去バーのデータが訂正されたら、その時点以降は記録を使わない。"""
    memo = CausalBarMemo()
    corrected = [{**_CHART[0], "close": 1.9, "high": 2.5}, *_CHART[1:]]

    _out, _n = _run(memo)
    out_fixed, n_fixed = _run(memo, chart=corrected)

    assert n_fixed >= 2, "訂正されたバーと、その畳みを引き継ぐ同一期間のバーは計算し直す"
    assert out_fixed[0]["data"][0]["value"] == 1.9


def test_new_data_does_not_change_the_recorded_past():
    """時刻不変（ISSUE-294）: 後ろに C 足が増えても既存の点は変わらない。"""
    memo = CausalBarMemo()

    before, _ = _run(memo, chart=_CHART[:3])
    after, n_after = _run(memo, chart=_CHART)

    old = {p["time"]: p["value"] for p in before[0]["data"]}
    new = {p["time"]: p["value"] for p in after[0]["data"]}
    assert all(old[t] == new[t] for t in old)
    assert n_after < len(_CHART), "重なるぶんは計算し直さない"


def test_capacity_evicts_the_oldest_bar():
    memo = CausalBarMemo(capacity=2)
    memo.put(1, 111, "a")
    memo.put(2, 222, "b")
    memo.put(3, 333, "c")

    assert memo.get(1, 111) is None, "容量を超えたら古い τ から捨てる"
    assert memo.get(3, 333) == "c"


def test_memo_for_separates_conditions_and_shares_the_same_one():
    a = memo_for(compute_tf="1D", indicator="moving_averages", variant="default",
                 params={"length": 9})
    same = memo_for(compute_tf="1D", indicator="moving_averages", variant="default",
                    params={"length": 9})
    other = memo_for(compute_tf="1D", indicator="moving_averages", variant="default",
                     params={"length": 5})

    assert a is same, "同じ計算条件は同じ記憶を共有する"
    assert a is not other, "params が違えば別の記憶"
