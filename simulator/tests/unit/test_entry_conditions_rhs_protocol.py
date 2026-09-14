"""エントリ条件の演算子表と rhs 多相を固定する（ISSUE-479 Wave2 フェーズ 1-B）。

固定する仕様:
    1. 比較演算子は `_OPS`（トークン → 演算）という**表 1 つ**が宣言でも評価でもある。
       構築時の検証メッセージも同じ表から導くので、宣言と評価が食い違えない。
    2. rhs（比較の右辺）は Rhs という**振る舞い**で表現する。定数も指標参照も
       `shift_of()` / `value(sample)` / `validate()` を持ち、呼び出し側は種別を
       尋ねない（isinstance による分岐が 0 になる）。

なぜ表にするか:
    旧実装は許容集合を frozenset で宣言し、評価は `if c.op == ">" … else …` という
    別の場所に書いていた。宣言と評価が別物なので、片方だけ増やしても構造上は矛盾せず
    静かに壊れる（`>=` を集合に足すと、評価側の else が `<` として扱う）。
    表なら追加は 1 行で両方に効く。**TBD-11 により `>=` / `<=` / `==` は不採用**であり、
    表は拡張点ではなく単一ソース化のための構造である。

なぜ rhs を多相にするか（LSP / OCP）:
    rhs の種別を尋ねる isinstance は、旧実装で検証・warmup 境界・評価の 3 か所に散って
    いた。種別が増えると 3 か所すべてを直す必要があり、1 か所忘れると
    「構築は通るのに評価だけ落ちる」形で壊れる。振る舞いを rhs 自身に持たせれば
    呼び出し側は 1 つも分岐を持たない。

なぜ計算量を測るか:
    `matches` は AND 連鎖であり、偽が出た時点で以降の系列参照は不要になる。短絡を
    やめても出力は同じなので、状態検証では原理的に落ちない。系列参照（sample）の
    発行回数を数えて「発行 − 使用 = 0」を固定する。

byte 不変の要求:
    ConfigError のメッセージは現行と 1 バイトも変えない（既存検定が文言を見ている）。
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

from simulator.domain.entry_conditions import (
    _OPS,
    Condition,
    Constant,
    EntryConditions,
    IndicatorRef,
    Rhs,
)
from simulator.domain.exceptions import ConfigError

_SOURCE = Path(__file__).resolve().parents[2] / "domain" / "entry_conditions.py"


class TestTheOperatorTableIsTheSingleDeclaration:
    """比較演算子の宣言と評価が同一の表から来ること。"""

    def test_the_table_holds_exactly_the_strict_inequalities(self):
        assert set(_OPS) == {">", "<"}

    @pytest.mark.parametrize(
        "op,lhs,rhs,expected",
        [
            (">", 2.0, 1.0, True),
            (">", 1.0, 1.0, False),   # 境界（厳密不等号なので偽）
            (">", 0.0, 1.0, False),
            ("<", 0.0, 1.0, True),
            ("<", 1.0, 1.0, False),   # 境界
            ("<", 2.0, 1.0, False),
        ],
        ids=["gt_true", "gt_boundary", "gt_false", "lt_true", "lt_boundary", "lt_false"],
    )
    def test_the_table_entry_is_the_strict_comparison(self, op, lhs, rhs, expected):
        assert _OPS[op](lhs, rhs) is expected

    def test_the_config_error_message_is_derived_from_the_table(self):
        """文言は現行と byte 一致（sorted(_OPS) 由来）。"""
        with pytest.raises(ConfigError) as excinfo:
            EntryConditions([Condition(indicator="ema", shift=0, op=">=", rhs=1.0)])
        assert str(excinfo.value).startswith(
            "op は ['<', '>'] のいずれか（TBD-11: 厳密不等号のみ）"
        )

    def test_no_operator_literal_is_compared_outside_the_table(self):
        """`c.op == ">"` のような比較が本体に 0 個（評価が表から離れていない）。"""
        tree = ast.parse(_SOURCE.read_text(encoding="utf-8"), filename=str(_SOURCE))
        table_lines = [
            (node.lineno, getattr(node, "end_lineno", node.lineno))
            for node in tree.body
            if isinstance(node, (ast.Assign, ast.AnnAssign))
            and any(
                isinstance(t, ast.Name) and t.id == "_OPS"
                for t in (node.targets if isinstance(node, ast.Assign) else [node.target])
            )
        ]
        assert table_lines, "_OPS の宣言が見つからない"
        outside = [
            node.lineno
            for node in ast.walk(tree)
            if isinstance(node, ast.Compare)
            and any(
                isinstance(c, ast.Constant) and c.value in {">", "<"}
                for c in node.comparators
            )
            and not any(lo <= node.lineno <= hi for lo, hi in table_lines)
        ]
        assert outside == [], f"演算子リテラルの比較が表の外に残っています: {outside}"


class TestRhsIsPolymorphic:
    """定数も指標参照も同じ振る舞い（Rhs）で扱えること。"""

    @pytest.mark.parametrize(
        "rhs",
        [Constant(5.0), IndicatorRef(indicator="close", shift=1)],
        ids=["constant", "indicator_ref"],
    )
    def test_both_kinds_satisfy_the_rhs_protocol(self, rhs):
        assert isinstance(rhs, Rhs)

    def test_a_constant_contributes_no_shift(self):
        assert Constant(5.0).shift_of() == 0

    def test_an_indicator_ref_contributes_its_own_shift(self):
        assert IndicatorRef(indicator="close", shift=3).shift_of() == 3

    def test_a_constant_yields_itself_without_sampling(self):
        def forbidden(name, shift):
            raise AssertionError("定数の評価で系列参照が発行された")

        assert Constant(5.0).value(forbidden) == 5.0

    def test_an_indicator_ref_yields_the_sampled_value(self):
        assert IndicatorRef(indicator="close", shift=1).value(
            lambda name, shift: 42.0 if (name, shift) == ("close", 1) else 0.0
        ) == 42.0

    def test_a_constant_never_fails_validation(self):
        assert Constant(-3.0).validate() is None

    def test_an_indicator_ref_with_a_negative_shift_fails_validation(self):
        with pytest.raises(ConfigError) as excinfo:
            IndicatorRef(indicator="sma", shift=-2).validate()
        assert str(excinfo.value).startswith("rhs 参照の shift は 0 以上")


class TestTheExternalConstructionApiIsUnchanged:
    """float をそのまま渡す既存の構築経路が壊れないこと。"""

    def test_a_bare_float_rhs_is_normalised_into_an_rhs(self):
        condition = Condition(indicator="ema", shift=0, op=">", rhs=5.0)
        assert isinstance(condition.rhs, Rhs)

    def test_the_normalised_rhs_still_compares_equal_to_the_raw_number(self):
        # 既存検定（test_strategy_spec_loader）が `cond.rhs == 5.0` を見ている。
        assert Condition(indicator="ema", shift=0, op=">", rhs=5.0).rhs == 5.0

    def test_the_normalised_rhs_is_not_an_indicator_ref(self):
        assert not isinstance(
            Condition(indicator="ema", shift=0, op=">", rhs=5.0).rhs, IndicatorRef
        )

    def test_an_indicator_ref_rhs_is_kept_as_is(self):
        ref = IndicatorRef(indicator="close", shift=1)
        assert Condition(indicator="ema", shift=0, op=">", rhs=ref).rhs is ref


class TestTheEvaluationHasNoTypeBranch:
    """rhs の種別を尋ねる分岐が本体に残っていないこと。"""

    def test_no_isinstance_against_indicator_ref_remains(self):
        tree = ast.parse(_SOURCE.read_text(encoding="utf-8"), filename=str(_SOURCE))
        hits = [
            node.lineno
            for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "isinstance"
            and any(
                isinstance(arg, ast.Name) and arg.id == "IndicatorRef"
                for arg in ast.walk(node)
                if isinstance(arg, ast.Name)
            )
        ]
        assert hits == [], f"IndicatorRef への isinstance が残っています: {hits}"


class TestTheConditionEvaluationDoesNotWasteWork:
    """計算量検定（Test Spy・発行 − 使用 = 0）。測るのは時間ではなく回数。"""

    @staticmethod
    def _spy():
        calls: "list[tuple[str, int]]" = []

        def sample(name: str, shift: int) -> float:
            calls.append((name, shift))
            return 1.0

        return calls, sample

    def test_a_constant_condition_issues_one_series_lookup(self):
        calls, sample = self._spy()
        conditions = EntryConditions(
            [Condition(indicator="ema", shift=0, op=">", rhs=0.0)]
        )
        assert conditions.matches(sample) is True
        # 発行（系列参照）− 使用（lhs 1 件）= 0。定数側で余分な参照を出さない。
        assert len(calls) - 1 == 0

    def test_an_indicator_ref_condition_issues_one_lookup_per_operand(self):
        calls, sample = self._spy()
        conditions = EntryConditions([
            Condition(
                indicator="ema", shift=0, op=">",
                rhs=IndicatorRef(indicator="close", shift=1),
            )
        ])
        conditions.matches(sample)
        # 発行 − 使用（lhs 1 + rhs 参照 1）= 0。
        assert len(calls) - 2 == 0

    def test_the_and_chain_short_circuits_on_the_first_false(self):
        calls, sample = self._spy()
        conditions = EntryConditions([
            Condition(indicator="a", shift=0, op="<", rhs=0.0),   # 1.0 < 0.0 → 偽
            Condition(indicator="b", shift=0, op=">", rhs=0.0),
            Condition(indicator="c", shift=0, op=">", rhs=0.0),
        ])
        assert conditions.matches(sample) is False
        # 偽が出た後の条件は結果に使われない。使わない計算は発行しない。
        assert len(calls) - 1 == 0

    @pytest.mark.parametrize("count", [2, 8], ids=["conditions_2", "conditions_8"])
    def test_the_issue_count_is_determined_by_the_operand_count_alone(self, count):
        """条件 2 / 8 の 2 点で「発行数 == 被演算子数」（オーダーの表明）。"""
        calls, sample = self._spy()
        conditions = EntryConditions([
            Condition(indicator=f"i{i}", shift=0, op=">", rhs=0.0) for i in range(count)
        ])
        assert conditions.matches(sample) is True
        assert len(calls) - count == 0

    @pytest.mark.parametrize("count", [2, 8], ids=["conditions_2", "conditions_8"])
    def test_the_issue_count_grows_with_indicator_operands_only(self, count):
        """rhs も系列参照のとき、条件 2 / 8 の 2 点で「発行数 == 被演算子数」。"""
        calls: "list[tuple[str, int]]" = []

        def sample(name: str, shift: int) -> float:
            calls.append((name, shift))
            return 2.0 if name.startswith("i") else 1.0   # 全条件を真にして短絡を避ける

        conditions = EntryConditions([
            Condition(
                indicator=f"i{i}", shift=0, op=">",
                rhs=IndicatorRef(indicator=f"r{i}", shift=0),
            )
            for i in range(count)
        ])
        assert conditions.matches(sample) is True
        assert len(calls) - 2 * count == 0
