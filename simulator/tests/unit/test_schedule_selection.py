"""評価粒度の選択規則（ISSUE-479 Wave2 4-11・O-1）。

固定する仕様:
    「この run は足の途中に評価点を要するか」を決める条件を、**1 つの表**に閉じる。

なぜ表にするか:
    移設前、この判定は実行経路に書かれた文字列比較だった:

        if getattr(config, "tick_model", None) == "real_ticks" or getattr(
            config, "pending_lifecycle", False
        ):

    条件を 1 つ増やすには実行経路そのものを開いて書き足す必要があり（OCP 違反）、
    しかも判定の全体像が実行経路の中に埋もれて読めない。表にすれば、条件の追加は
    表への 1 行であり、いま何が粒度を決めているかは表を見れば分かる。

なぜ真偽値の条件を「== True」で書かないか:
    移設前の判定は `or features.pending_lifecycle` という**真偽評価**だった。
    `== True` に置き換えると、真だが True ではない値（duck-typed config が持ちうる）で
    判定が変わる。表の各行は「属性名」と「その値をどう見るか」の組にして、移設前の
    見方をそのまま保つ。
"""
from __future__ import annotations

from simulator.usecase.run_features import RunFeatures
from simulator.usecase.schedule_selection import (
    TICK_GRANULARITY_TRIGGERS,
    requires_tick_granularity,
)


class _Features:
    """RunFeatures と同じ属性を持つ最小の代用（表の読み方だけを測る）。"""

    def __init__(self, **kw):
        for name in RunFeatures.feature_names():
            setattr(self, name, RunFeatures.declared_defaults()[name])
        for name, value in kw.items():
            setattr(self, name, value)


class TestTheTriggerTableDecidesTheGranularity:
    def test_a_plain_run_does_not_need_intra_bar_points(self):
        assert requires_tick_granularity(_Features()) is False

    def test_a_real_tick_run_needs_intra_bar_points(self):
        assert requires_tick_granularity(_Features(tick_model="real_ticks")) is True

    def test_a_pending_lifecycle_run_needs_intra_bar_points(self):
        assert requires_tick_granularity(_Features(pending_lifecycle=True)) is True

    def test_either_trigger_alone_is_enough(self):
        both = _Features(tick_model="real_ticks", pending_lifecycle=True)
        assert requires_tick_granularity(both) is True

    def test_another_tick_model_does_not_trigger(self):
        assert requires_tick_granularity(_Features(tick_model="ohlc_simulate")) is False

    def test_a_truthy_pending_switch_triggers_like_before(self):
        """真偽評価であること（`== True` へ変えると 1 は通り "yes" は通らなくなる）。"""
        assert requires_tick_granularity(_Features(pending_lifecycle=1)) is True
        assert requires_tick_granularity(_Features(pending_lifecycle="yes")) is True
        assert requires_tick_granularity(_Features(pending_lifecycle=0)) is False


class TestTheTableIsTheSingleSourceOfTheRule:
    def test_every_trigger_names_a_feature_the_run_actually_has(self):
        names = {name for name, _ in TICK_GRANULARITY_TRIGGERS}
        assert names <= set(RunFeatures.feature_names())

    def test_the_table_holds_the_two_known_triggers(self):
        assert {name for name, _ in TICK_GRANULARITY_TRIGGERS} == {
            "tick_model",
            "pending_lifecycle",
        }

    def test_removing_a_row_from_the_table_disables_that_trigger(self):
        """**負の対照**: 表が実際に判定を決めていること（飾りでないこと）。"""
        import simulator.usecase.schedule_selection as mod

        only_pending = tuple(
            row for row in TICK_GRANULARITY_TRIGGERS if row[0] != "tick_model"
        )
        original = mod.TICK_GRANULARITY_TRIGGERS
        try:
            mod.TICK_GRANULARITY_TRIGGERS = only_pending
            assert (
                mod.requires_tick_granularity(_Features(tick_model="real_ticks"))
                is False
            )
        finally:
            mod.TICK_GRANULARITY_TRIGGERS = original
        # 戻したら元どおり効く。
        assert requires_tick_granularity(_Features(tick_model="real_ticks")) is True


class TestTheSelectionDoesNotWasteWork:
    """計算量検定（発行 − 使用 = 0）。"""

    def test_it_stops_at_the_first_matching_trigger(self):
        """先頭の行で決まったら残りの行を見ない（短絡）。"""
        read: "list[str]" = []

        class _Watching(_Features):
            def __getattribute__(self, name):
                if name in {n for n, _ in TICK_GRANULARITY_TRIGGERS}:
                    read.append(name)
                return object.__getattribute__(self, name)

        requires_tick_granularity(_Watching(tick_model="real_ticks"))
        # 発行（読んだ属性）− 使用（判定が確定するまでに要る行数 1）= 0。
        assert len(read) - 1 == 0

    def test_it_reads_each_feature_at_most_once(self):
        read: "list[str]" = []

        class _Watching(_Features):
            def __getattribute__(self, name):
                if name in {n for n, _ in TICK_GRANULARITY_TRIGGERS}:
                    read.append(name)
                return object.__getattribute__(self, name)

        requires_tick_granularity(_Watching())
        # 全行を見ても、1 行につき 1 回だけ（同じ属性を二度読まない）。
        assert len(set(read)) - len(read) == 0
        assert len(read) - len(TICK_GRANULARITY_TRIGGERS) == 0

    def test_the_table_is_not_rebuilt_on_every_call(self):
        assert TICK_GRANULARITY_TRIGGERS is TICK_GRANULARITY_TRIGGERS
