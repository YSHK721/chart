"""serve_live_tick_tails（ISSUE-250 Phase 1）の純ロジックテスト。

最重要は「形成中バーの畳み方がフロント ``_applyTick`` / リプレイ ``formingStatesAt`` と
同一規則であること」。ここがずれると描画状態と指標値が食い違う（ISSUE-232 の失敗モード）。
"""

from __future__ import annotations

from usecase.serve_live_tick_tails import (
    FormingState,
    TailSpec,
    forming_states,
    parse_specs,
    period_of,
    tails_for_ticks,
)

TF = 900  # 15m


def test_period_of_floors_to_timeframe_start():
    assert period_of(1_785_757_500_000, TF) == 1_785_757_500
    assert period_of(1_785_757_500_999, TF) == 1_785_757_500
    assert period_of(1_785_757_499_999, TF) == 1_785_756_600


def test_forming_states_accumulates_ohlc_and_volume_per_tick():
    base = 1_785_757_500_000
    ticks = [[base + 100, 100.0], [base + 200, 102.0], [base + 300, 99.0], [base + 400, 101.0]]
    got = forming_states(ticks, TF)
    assert len(got) == 4
    assert [s.close for s in got] == [100.0, 102.0, 99.0, 101.0]
    assert [s.high for s in got] == [100.0, 102.0, 102.0, 102.0]
    assert [s.low for s in got] == [100.0, 100.0, 99.0, 99.0]
    assert all(s.open == 100.0 for s in got)          # open は最初の tick で固定
    assert [s.volume for s in got] == [1, 2, 3, 4]
    assert all(s.time == 1_785_757_500 for s in got)


def test_forming_states_starts_new_bar_on_period_rollover():
    a = 1_785_757_500_000 + 100
    b = 1_785_758_400_000 + 100        # 次の 15m 周期
    got = forming_states([[a, 100.0], [b, 105.0]], TF)
    assert got[0].time != got[1].time
    assert got[1].open == got[1].high == got[1].low == got[1].close == 105.0
    assert got[1].volume == 1


def test_forming_states_continues_from_seed_in_same_period():
    seed = {"time": 1_785_757_500, "open": 90.0, "high": 95.0, "low": 88.0,
            "close": 92.0, "volume": 10}
    got = forming_states([[1_785_757_500_000 + 500, 96.0]], TF, seed=seed)
    assert got[0].open == 90.0          # seed の open を保つ
    assert got[0].high == 96.0          # 累積最大を更新
    assert got[0].low == 88.0
    assert got[0].volume == 11


def test_forming_states_ignores_seed_from_other_period():
    seed = {"time": 1_785_756_600, "open": 90.0, "high": 95.0, "low": 88.0,
            "close": 92.0, "volume": 10}
    got = forming_states([[1_785_757_500_000 + 500, 96.0]], TF, seed=seed)
    assert got[0].open == 96.0 and got[0].volume == 1


def test_forming_states_empty_ticks_returns_empty():
    assert forming_states([], TF) == []


def _state(ms: int) -> FormingState:
    return FormingState(time=0, open=1.0, high=1.0, low=1.0, close=1.0, volume=1, tick_ms=ms)


def test_tails_for_ticks_emits_one_entry_per_tick():
    specs = [TailSpec("i1", "profit_rsi", "default", {})]
    got = tails_for_ticks([_state(10), _state(20)], specs,
                          lambda s, st: {"rsi": float(st.tick_ms)})
    assert [e["tickMs"] for e in got] == [10, 20]
    assert got[0]["tails"]["i1"] == {"rsi": 10.0}


def test_tails_for_ticks_drops_spec_when_calculator_returns_none():
    specs = [TailSpec("ok", "profit_rsi", "default", {}),
             TailSpec("ng", "profit_stc", "default", {})]
    got = tails_for_ticks(
        [_state(1)], specs,
        lambda s, st: {"v": 1.0} if s.indicator_id == "profit_rsi" else None,
    )
    assert set(got[0]["tails"]) == {"ok"}     # 保証対象外は黙って劣化させず明示的に落とす


def test_parse_specs_keeps_valid_and_drops_malformed():
    got = parse_specs([
        {"instanceId": "a", "indicatorId": "profit_rsi", "params": {"rsi_period": 6}},
        {"instanceId": "b"},                 # indicatorId 欠落
        "not-a-dict",
        {"instanceId": "c", "indicatorId": "tickvol", "variant": "v2", "params": None},
    ])
    assert [s.instance_id for s in got] == ["a", "c"]
    assert got[0].variant == "default" and got[0].params == {"rsi_period": 6}
    assert got[1].variant == "v2" and got[1].params == {}


def test_parse_specs_non_list_is_empty():
    assert parse_specs(None) == [] and parse_specs({"a": 1}) == []
