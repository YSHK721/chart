"""共有組み立て器（`tester_settings_engine_fixtures`）が手書きの名前表を持たないこと。

固定する事項:
    1. 前提: `SymbolSpec` のフィールド名集合が `RunProfile` のフィールド名集合に
       **含まれる**こと。この包含が成立している限り、両者の対応は名前一致で機械的に
       導出でき、手書きの対応表を持つ必要がない。包含が崩れた時点で機械導出は
       不可能になるため、崩壊を本テストが検出する（沈黙で手書き表へ退行させない）。
    2. `jp225_symbol_spec` が対応表を**コードとして持たない**こと。前提 1 が成立して
       いても、実装が 8 行の手書き代入を並べていれば、フィールドの増減時に片方だけが
       腐る（プロジェクト規約「同じコードを手書き複製するな」）。宣言だけでは退行を
       止められないため、AST による機械的検査で担保する。
    3. 振る舞い不変: 返る `SymbolSpec` の各フィールドがカタログの同名値と一致すること
       （機械導出への置換が値を変えていないことの安全網）。

検査を「ソース文字列の grep」ではなく AST で行う理由:
    `inspect.getsource` は docstring を含む。説明文にフィールド名を書いた瞬間に
    検査が偽陽性になるため、docstring を除いた**実行されるコード**だけを対象にする。

本モジュールは `simulator/tests/tester_settings_engine_fixtures.py`（本移植で追加した
成果物）を検証対象とする。既存資産は 1 行も変更しない。
"""
from __future__ import annotations

import ast
import inspect
import textwrap
from dataclasses import fields

import pytest

from simulator.sim_ui.usecase.run_options_ports import RunProfile
from simulator.tests.tester_settings_engine_fixtures import jp225_symbol_spec
from simulator.usecase.models import SymbolSpec


def _field_names(dataclass_type: type) -> "frozenset[str]":
    return frozenset(field.name for field in fields(dataclass_type))


def _executable_source(function) -> str:
    """``function`` の**実行されるコード**を復元する（docstring を除く）。

    docstring はモジュールの説明責務であり、対応表の有無とは無関係である。
    含めたまま検査すると「説明にフィールド名を書いた」だけで落ちる。
    """
    tree = ast.parse(textwrap.dedent(inspect.getsource(function)))
    definition = tree.body[0]
    assert isinstance(definition, ast.FunctionDef)
    body = list(definition.body)
    if (
        body
        and isinstance(body[0], ast.Expr)
        and isinstance(body[0].value, ast.Constant)
        and isinstance(body[0].value.value, str)
    ):
        body = body[1:]
    return "\n".join(ast.unparse(node) for node in body)


class TestNameMatchPremise:
    """名前一致で機械導出できるという前提そのものを固定する。"""

    def test_symbol_spec_field_names_are_a_subset_of_run_profile(self):
        # 包含が崩れると `SymbolSpec(**{f.name: getattr(profile, f.name) ...})` が
        # AttributeError になる。崩壊を実装ではなく本テストで先に検出する。
        missing = sorted(_field_names(SymbolSpec) - _field_names(RunProfile))
        assert missing == [], (
            "SymbolSpec のフィールドが RunProfile に同名で存在しない: "
            f"{missing}。名前一致による機械導出の前提が崩れている"
        )

    def test_the_premise_covers_every_symbol_spec_field(self):
        # 空集合どうしの包含で緑になる退化を塞ぐ（`SymbolSpec` は 8 フィールドを持つ）。
        assert len(_field_names(SymbolSpec)) == len(fields(SymbolSpec)) > 0


class TestNoHandWrittenFieldTable:
    """対応表をコードとして持たないことを機械的に担保する（宣言では退行を止められない）。"""

    @pytest.mark.parametrize("field_name", sorted(_field_names(SymbolSpec)))
    def test_field_name_does_not_appear_in_the_builder_source(self, field_name):
        source = _executable_source(jp225_symbol_spec)
        assert field_name not in source, (
            f"jp225_symbol_spec が {field_name!r} を名指ししている。"
            "RunProfile → SymbolSpec の対応は dataclasses.fields による名前一致で"
            "導出し、手書きの表を置かない"
        )


class TestBehaviourIsUnchanged:
    """カタログの権威値がそのまま `SymbolSpec` に載ることの安全網。"""

    @pytest.mark.parametrize("field_name", sorted(_field_names(SymbolSpec)))
    def test_each_field_equals_the_catalog_profile_value(self, field_name):
        from simulator.sim_ui.main.composition_root_jobs import build_run_options_port

        profile = build_run_options_port().datasets()[0]
        assert getattr(jp225_symbol_spec(), field_name) == getattr(profile, field_name)
