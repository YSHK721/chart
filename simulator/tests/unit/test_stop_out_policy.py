"""StopOutPolicy: 証拠金割れで何をするかの決定点（ISSUE-479 Wave2 4-4・O-3）。

固定する仕様:
    証拠金維持率が stop-out 水準を割ったときの振る舞い（run を捨てて送出する／
    全保有玉を強制決済して完走する）を、**名前から決定へ引く 1 つの表**で決める。

なぜ表にするか:
    移設前は `if config.stop_out_action != "close_and_halt":` という比較が実行経路の
    3 箇所（バー open 評価・バー close 評価・ティック評価）に書き写されていた。
    比較の向きが「既定でない方の名前」を見る形だったため、

      * 名前を 1 つ増やすには 3 箇所すべてを直す必要がある（OCP 違反）
      * 3 箇所のうち 1 箇所だけ直し忘れると、評価点によって違う方針で走る

    という 2 つの欠陥があった。後者は「どの評価点で割れたか」に依存するので、
    fixture 次第で緑のまま残る。決定点を 1 つにすれば、どちらも起こり得なくなる。
"""
from __future__ import annotations

import pytest

from simulator.usecase.ports import StopOutPolicyPort
from simulator.usecase.stop_out_policy import (
    STOP_OUT_POLICIES,
    StopOutContext,
    StopOutDecision,
    resolve_stop_out_policy,
)


def _ctx(**overrides):
    base = dict(margin_level=10.0, stop_out_level=50.0, bar_index=3, open_trade_count=1)
    base.update(overrides)
    return StopOutContext(**base)


class TestThePolicyTableDecidesWhatHappensOnABreach:
    """名前 → 方針 → 決定の 3 段が 1 つの表に閉じていること。"""

    def test_fail_stop_discards_the_run_instead_of_liquidating(self):
        # Arrange / Act
        decision = resolve_stop_out_policy("fail_stop").on_breach(_ctx())
        # Assert: 強制決済しない＝呼出側が送出して部分結果を捨てる。
        assert decision.liquidate is False

    def test_close_and_halt_liquidates_and_lets_the_run_finish(self):
        # Arrange / Act
        decision = resolve_stop_out_policy("close_and_halt").on_breach(_ctx())
        # Assert
        assert decision.liquidate is True

    def test_an_unknown_action_is_treated_as_fail_stop(self):
        """表に無い名前は「既定でない方ではない」＝ fail_stop（移設前の比較と同値）。

        移設前は `!= "close_and_halt"` の 1 比較だったため、綴り違いや未知の名前は
        すべて fail_stop 側に落ちた。表引きでも同じ側へ落ちることを固定する
        （ここが変わると、設定を書き間違えた run が黙って完走してしまう）。
        """
        assert resolve_stop_out_policy("clsoe_and_halt").on_breach(_ctx()).liquidate is False
        assert resolve_stop_out_policy(None).on_breach(_ctx()).liquidate is False
        assert resolve_stop_out_policy("").on_breach(_ctx()).liquidate is False

    def test_every_declared_policy_satisfies_the_port(self):
        for name, policy in STOP_OUT_POLICIES.items():
            assert isinstance(policy, StopOutPolicyPort), name

    def test_the_table_declares_both_documented_actions(self):
        # models.py の宣言が許す名前（framework の Literal と対称）が表に在ること。
        assert sorted(STOP_OUT_POLICIES) == ["close_and_halt", "fail_stop"]


class TestTheDecisionIsAValue:
    """決定は run の途中で書き換わらない値であること。"""

    def test_a_decision_cannot_be_mutated(self):
        decision = StopOutDecision(liquidate=True)
        with pytest.raises(Exception):
            decision.liquidate = False

    def test_a_context_cannot_be_mutated(self):
        with pytest.raises(Exception):
            _ctx().margin_level = 999.0

    def test_the_context_carries_the_facts_of_the_breach(self):
        ctx = _ctx(margin_level=12.5, stop_out_level=50.0, bar_index=7, open_trade_count=2)
        assert (ctx.margin_level, ctx.stop_out_level, ctx.bar_index, ctx.open_trade_count) == (
            12.5, 50.0, 7, 2
        )


class TestThePolicyLookupDoesNotWasteWork:
    """計算量検定（発行 − 使用 = 0）。測るのは時間ではなく回数。"""

    def test_the_policies_are_not_rebuilt_on_every_lookup(self):
        """方針は表の中の同じ実体である（引くたびに組み直さない）。"""
        assert resolve_stop_out_policy("fail_stop") is resolve_stop_out_policy("fail_stop")
        assert resolve_stop_out_policy("close_and_halt") is resolve_stop_out_policy(
            "close_and_halt"
        )

    def test_an_unknown_name_resolves_to_the_same_instance_as_fail_stop(self):
        # 既定への落ち方も表の実体を指す（既定用に別の実体を作らない）。
        assert resolve_stop_out_policy("nope") is STOP_OUT_POLICIES["fail_stop"]

    def test_the_lookup_reads_the_table_once_per_call(self, monkeypatch):
        # Arrange: 表の読み取り（get）を数える。
        reads: "list[str]" = []
        original_get = dict.get

        class _CountingTable(dict):
            def get(self, key, default=None):
                reads.append(key)
                return original_get(self, key, default)

        import simulator.usecase.stop_out_policy as mod

        monkeypatch.setattr(mod, "STOP_OUT_POLICIES", _CountingTable(STOP_OUT_POLICIES))
        # Act
        for name in ("fail_stop", "close_and_halt", "unknown"):
            mod.resolve_stop_out_policy(name)
        # Assert: 発行（表引き）− 使用（解決した方針の数）= 0。
        assert len(reads) - 3 == 0
