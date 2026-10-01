"""保証境界の適用範囲の分割を機械で固定する（ISSUE-525・合流点への適用）。

何を解くか:
    非対象の宣言表（`simulator/main/tester_settings/unsupported.py`）は、実行要求時の
    判定を 1 箇所に集めている。しかしその表を適用する関数（「`apply_unsupported_rules`」）の
    呼び手は写像層ただ 1 つであり、写像層を通らない投入経路（「`settings`」 ブロックが無い
    投入）は**宣言の外へ出られた**（ISSUE-525 の実測 2026-09-25: 同じフォームの既定値で、
    schema が取れれば N-17 で exit 2、取れなければ受理されて走り続けた）。

    是正は「run 自身の引数だけで判定できる規則を、両経路が必ず通る合流点
    （`build_interactor`）で適用する」ことである。本ファイルはその**切り分けが人手の
    列挙にならない**ことを機械で固定する。

固定する不変条件:
    P1  各規則が読む判定入力は、その `detect` の構文木から測った集合と宣言（`reads`）が
        一致する（宣言の写しが腐らない）。
    P2  実行要求時の規則は「合流点で適用する」「設定の語彙を読む」の 2 つに**分割**される
        （重なりなし・漏れなし）。
    P3  合流点で解決できる入力だけを読む規則は、必ず合流点側にある。「合流点で解決
        できる」の判定は `RUN_SCOPE_INPUTS` の宣言と `build_interactor` の実シグネチャで
        行う——規則を 1 つ足したとき、それが run 引数だけで判定できるのに宣言を広げ
        忘れたら落ちる。
    P4  合流点が適用するのは `RUN_SCOPE_RULES` **そのもの**である（合流点側に規則の
        名前を書き写していない）。判定は、合流点が読む宣言へ規則を 1 件差し込み、
        `build_interactor` がそれで止まることで測る。
    P5  設定の語彙を読む規則が現行経路で落ちないのは**迂回ではなく入力の不在**である
        （それらの読む入力が、合流点で解決できる入力の集合に 1 つも無い）。
"""
from __future__ import annotations

import ast
import inspect
import textwrap

import pytest

from simulator.main import build_interactor, unsupported_run_scope
from simulator.main.tester_settings import unsupported
from simulator.main.tester_settings.unsupported import (
    NOT_VIOLATED,
    RUN_REQUEST_RULES,
    RUN_SCOPE_INPUTS,
    RUN_SCOPE_RULES,
    SETTINGS_SCOPE_RULES,
    RunScopeInputs,
    UnsupportedRule,
    select_run_scope_rules,
)


#: 宣言した出所のうち、単純な識別子のもの（`build_interactor` の仮引数を名指す）。
_IDENTIFIER_SOURCES = tuple(
    (name, source) for name, source in RUN_SCOPE_INPUTS.items() if source.isidentifier()
)

#: 宣言した出所のうち、散文で導き方を書いたもの（仮引数そのものではない）。
_EXPLAINED_SOURCES = tuple(
    (name, source)
    for name, source in RUN_SCOPE_INPUTS.items()
    if not source.isidentifier()
)


def _source_id(value) -> str:
    return str(value)


@pytest.fixture()
def runnable_run_kwargs(tmp_path):
    """保証境界の内側にある現行経路の引数束（合流点が組み上がることが前提）。"""
    from simulator.tests.route_parity_fixtures import run_kwargs_for, write_marketdata_csv

    data_path = write_marketdata_csv(tmp_path / "spreadless.csv", with_spread=False)
    return run_kwargs_for(data_path)


def _reads_measured(rule: UnsupportedRule) -> "frozenset[str]":
    """`detect` が判定入力から読む属性名を構文木で測る（宣言を見ない）。

    測り方: `detect` の仮引数 2 つ（実効設定・注入束）に**直接**ぶら下がる属性参照の
    名前を集める。`x = effective.date_range` の後に読む 「`x.preset`」 は数えない——それは
    判定入力から取り出した値の内部であり、判定入力そのものではない。
    """
    tree = ast.parse(textwrap.dedent(inspect.getsource(rule.detect)))
    function = tree.body[0]
    assert isinstance(function, ast.FunctionDef)
    parameters = {argument.arg for argument in function.args.args}
    return frozenset(
        node.attr
        for node in ast.walk(function)
        if isinstance(node, ast.Attribute)
        and isinstance(node.value, ast.Name)
        and node.value.id in parameters
    )


class TestDeclaredReadsMatchTheCode:
    """P1: 宣言（`reads`）と構文木で測った集合が一致する。"""

    @pytest.mark.parametrize("rule", RUN_REQUEST_RULES, ids=lambda r: r.unsupported_id)
    def test_the_declared_reads_equal_the_measured_reads(self, rule):
        assert frozenset(rule.reads) == _reads_measured(rule)

    def test_every_run_request_rule_declares_what_it_reads(self):
        # 空の宣言は「何も読まない規則」を意味してしまい、P3 が無条件で真になる。
        assert all(rule.reads for rule in RUN_REQUEST_RULES)


class TestThePartition:
    """P2: 実行要求時の規則が 2 つに分割される。"""

    def test_the_two_scopes_cover_the_run_request_rules(self):
        assert set(RUN_SCOPE_RULES) | set(SETTINGS_SCOPE_RULES) == set(RUN_REQUEST_RULES)

    def test_the_two_scopes_do_not_overlap(self):
        assert not set(RUN_SCOPE_RULES) & set(SETTINGS_SCOPE_RULES)

    def test_both_scopes_are_non_empty(self):
        # どちらかが空なら、分割そのものが何も表明しない。
        assert RUN_SCOPE_RULES and SETTINGS_SCOPE_RULES


class TestTheClassificationIsMechanical:
    """P3: 合流点で解決できる入力だけを読む規則は必ず合流点側にある。"""

    def test_the_declared_inputs_are_exactly_what_the_confluence_carries(self):
        # 宣言が「合流点で解決できる」と言う入力は、合流点が実際に運ぶ束の全フィールドで
        # なければならない（運べない入力を宣言すると P3 が空手形になる）。
        carried = {field for field in RunScopeInputs.__dataclass_fields__}
        assert set(RUN_SCOPE_INPUTS) == carried

    @pytest.mark.parametrize(("name", "source"), _IDENTIFIER_SOURCES, ids=_source_id)
    def test_an_identifier_source_is_a_real_parameter(self, name, source):
        # 宣言の右辺が単純な識別子なら、それは `build_interactor` の仮引数でなければ
        # ならない（存在しない引数を出所と書くと、宣言が嘘になる）。
        assert source in set(inspect.signature(build_interactor).parameters)

    def test_both_kinds_of_source_are_present(self):
        # 空振り防止: どちらかが空なら、上と下の 2 つの表明の片方が何も測らない。
        assert _IDENTIFIER_SOURCES and _EXPLAINED_SOURCES

    @pytest.mark.parametrize(("name", "source"), _EXPLAINED_SOURCES, ids=_source_id)
    def test_an_explained_source_is_not_a_bare_identifier(self, name, source):
        # 仮引数でない出所は**散文で導き方を書く**（識別子だけ書くと、実在しない引数名を
        # 出所と称しても検定が通ってしまう）。
        assert not source.isidentifier()

    @pytest.mark.parametrize(
        "rule", SETTINGS_SCOPE_RULES, ids=lambda r: r.unsupported_id
    )
    def test_a_rule_that_only_reads_run_arguments_lands_at_the_confluence(self, rule):
        """run 引数だけで判定できる規則は、宣言を広げ忘れたらここで落ちる。

        「run 引数だけで判定できる」は `build_interactor` の実シグネチャで測る——判定入力の
        名前が仮引数に在るなら、その規則は現行経路でも判定できる。
        """
        parameters = set(inspect.signature(build_interactor).parameters)
        resolvable = {
            name for name in rule.reads
            if name in parameters or name in RUN_SCOPE_INPUTS
        }
        assert resolvable != set(rule.reads), (
            f"{rule.unsupported_id} は run 引数だけで判定できるのに合流点へ載っていない"
        )


class TestTheConfluenceAppliesTheDeclaredSet:
    """P4: 合流点が適用するのは宣言された集合そのものである。"""

    def test_a_rule_added_to_the_declared_set_stops_the_confluence(
        self, monkeypatch, runnable_run_kwargs
    ):
        # Arrange: run 引数だけを読む規則を 1 件、宣言へ差し込む。
        marker = "N-TEST-CONFLUENCE"
        probe = UnsupportedRule(
            unsupported_id=marker,
            field="subject_path",
            reason="検定が差し込んだ規則（run 引数だけで判定できる）",
            detect=lambda effective, binding: effective.ea_name,
            reads=("ea_name",),
        )
        widened = select_run_scope_rules(RUN_REQUEST_RULES + (probe,))
        assert probe in widened, "差し込んだ規則が合流点側に分類されていない（前提の崩れ）"
        # 差し替えるのは**所有者**の宣言である（設定層のは再輸出であり、そこを差し替えても
        # 適用器は見ていない——この区別を誤ると検定が何も測らずに緑になる）。
        monkeypatch.setattr(unsupported_run_scope, "RUN_SCOPE_RULES", widened)

        # Act / Assert
        with pytest.raises(Exception) as caught:
            build_interactor(**runnable_run_kwargs)
        assert marker in str(caught.value)

    def test_the_confluence_does_not_apply_the_settings_scope_rules(
        self, monkeypatch, runnable_run_kwargs
    ):
        # 設定の語彙を読む規則を「必ず違反する」形へ差し替えても、合流点は止まらない
        # （合流点が別の集合を勝手に適用していないことの表明）。
        always = tuple(
            UnsupportedRule(
                unsupported_id=rule.unsupported_id,
                field=rule.field,
                reason=rule.reason,
                detect=lambda effective, binding: "violated",
                reads=rule.reads,
            )
            for rule in SETTINGS_SCOPE_RULES
        )
        monkeypatch.setattr(unsupported, "SETTINGS_SCOPE_RULES", always)
        controller, request = build_interactor(**runnable_run_kwargs)
        assert controller is not None and request is not None


class TestSettingsScopeIsAbsenceOfInput:
    """P5: 設定の語彙を読む規則は、合流点に対応する入力が 1 つも無い。"""

    @pytest.mark.parametrize(
        "rule", SETTINGS_SCOPE_RULES, ids=lambda r: r.unsupported_id
    )
    def test_no_read_of_a_settings_scope_rule_is_resolvable_at_the_confluence(self, rule):
        assert not set(rule.reads) & set(RUN_SCOPE_INPUTS)


class TestTheDetectorsSeeTheCarriedView:
    """合流点が運ぶ束が、合流点側の規則の判定入力として十分であること。"""

    def test_every_run_scope_rule_can_be_evaluated_on_the_carried_view(self):
        inputs = RunScopeInputs(
            ea_name="TC24051901",
            symbol="JP225",
            data_path=None,
            tick_store_root=None,
            tick_model=None,
            known_ea_names=frozenset({"TC24051901"}),
            spread_dependent_ea_names=frozenset(),
        )
        for rule in RUN_SCOPE_RULES:
            assert rule.detect is not None
            assert rule.detect(inputs, inputs) is NOT_VIOLATED
