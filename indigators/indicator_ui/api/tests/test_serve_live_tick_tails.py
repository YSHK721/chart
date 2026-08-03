"""serve_live_tick_tails（ISSUE-250 Phase 1）の純ロジックテスト。

最重要は「形成中バーの畳み方がフロント ``_applyTick`` / リプレイ ``formingStatesAt`` と
同一規則であること」。ここがずれると描画状態と指標値が食い違う（ISSUE-232 の失敗モード）。

本モジュールは**時間足を一切知らない**（周期秒・floor・セッション日・暦周期の分岐を持たない）。
バー帰属は注入された ``bar_time_fn`` ただ 1 つで決まる＝全時間足が同じ経路を通る。
テストも tf ごとの分岐を持たず、規則を差し替えるだけで同じ振る舞いを確認する。
"""

from __future__ import annotations

from usecase.serve_live_tick_tails import (
    FormingState,
    TailSpec,
    forming_states,
    parse_specs,
    states_for_batch,
    tails_for_ticks,
)

TF = 900  # 15m


def bt(tf_sec=TF):
    """テスト用のバー帰属規則（floor）。本番は marketdata.tf_meta.bar_time_unix を注入する。"""
    return lambda ms: (int(ms) // 1000 // tf_sec) * tf_sec


def test_forming_states_accumulates_ohlc_and_volume_per_tick():
    base = 1_785_757_500_000
    ticks = [[base + 100, 100.0], [base + 200, 102.0], [base + 300, 99.0], [base + 400, 101.0]]
    got = forming_states(ticks, bt())
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
    got = forming_states([[a, 100.0], [b, 105.0]], bt())
    assert got[0].time != got[1].time
    assert got[1].open == got[1].high == got[1].low == got[1].close == 105.0
    assert got[1].volume == 1


def test_forming_states_continues_from_seed_in_same_period():
    seed = {"time": 1_785_757_500, "open": 90.0, "high": 95.0, "low": 88.0,
            "close": 92.0, "volume": 10}
    got = forming_states([[1_785_757_500_000 + 500, 96.0]], bt(), seed=seed)
    assert got[0].open == 90.0          # seed の open を保つ
    assert got[0].high == 96.0          # 累積最大を更新
    assert got[0].low == 88.0
    assert got[0].volume == 11


def test_forming_states_ignores_seed_from_other_period():
    seed = {"time": 1_785_756_600, "open": 90.0, "high": 95.0, "low": 88.0,
            "close": 92.0, "volume": 10}
    got = forming_states([[1_785_757_500_000 + 500, 96.0]], bt(), seed=seed)
    assert got[0].open == 96.0 and got[0].volume == 1


def test_forming_states_empty_ticks_returns_empty():
    assert forming_states([], bt()) == []


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


# =========================================================================== #
# ISSUE-251: states_for_batch — 増分しか無い呼び出し側が周期の累積を保つ入口
# =========================================================================== #

def test_states_for_batch_returns_only_the_batch_states():
    prior = [[60_000, 10.0], [61_000, 12.0]]
    batch = [[62_000, 11.0], [63_000, 13.0]]
    states = states_for_batch(prior, batch, bt(300))
    assert [s.tick_ms for s in states] == [62_000, 63_000]


def test_states_for_batch_keeps_the_accumulation_across_seed_and_prior():
    seed = {"time": 0, "open": 5.0, "high": 6.0, "low": 4.0, "close": 6.0, "volume": 7}
    prior = [[60_000, 9.0]]
    batch = [[61_000, 8.0]]
    last = states_for_batch(prior, batch, bt(300), seed=seed)[-1]
    assert last.open == 5.0                      # 周期の最初（seed 由来）
    assert last.high == 9.0 and last.low == 4.0  # seed ∪ prior ∪ batch の max/min
    assert last.volume == 7 + 1 + 1              # seed + prior + batch の tick 数
    assert last.close == 8.0                     # close は常に当該 tick


def test_states_for_batch_drops_a_seed_from_another_period():
    seed = {"time": -300, "open": 5.0, "high": 6.0, "low": 4.0, "close": 6.0, "volume": 7}
    last = states_for_batch([], [[61_000, 8.0]], bt(300), seed=seed)[-1]
    assert last.open == 8.0 and last.volume == 1


def test_states_for_batch_empty_batch_is_empty():
    assert states_for_batch([[1, 1.0]], [], bt(300)) == []


def test_bar_attribution_comes_only_from_the_injected_rule():
    """暦周期（1W/1M）もセッション日（1D）も、注入規則が返す time がそのままバー識別になる。

    本モジュールに tf 分岐が無いことの固定点: 規則を差し替えるだけで、日中足と同じコードが
    同じ振る舞い（同一バーは累積・変われば新バー）をする。
    """
    ticks = [[86_400_000, 10.0], [86_500_000, 11.0]]
    states = forming_states(ticks, lambda ms: (ms // 1000) - 100)
    assert [s.time for s in states] == [86_300, 86_400]   # 注入規則がそのままバー time になる
    assert states[1].open == 11.0                          # バーが変われば新しいバー
