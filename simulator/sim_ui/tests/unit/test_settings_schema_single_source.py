"""schema 供給側に MT5 語彙のリテラルが無いことを機械検査する（Phase 8 スライス 1・構造ガード）.

固定する規則（基本設計 §18.3「複製ゼロの機械検査」・`.claude/CLAUDE.md`「同じコードを
手書き複製するな」）:

    時間足ラベル（`Period` の `.ini` ラベル）・`Model` の生値表記・対象接尾辞は
    **唯一の宣言**（`usecase/tester_settings/enums.py` と `main/tester_settings` の
    公開定数）から反復導出する。schema 供給側のモジュールにそれらの**文字列リテラル**が
    現れた時点で単一ソースは壊れている（片方だけ更新される形が必ず生じる）。

禁止語彙をこの検定に**手で列挙しない**のが要点である。列挙すれば、それ自体が 3 つ目の
複製になる。禁止集合は単一ソースから導く。

なぜ AST か（既存 `test_sim_ui_import_direction.py` と同じ理由）: 生テキスト検査は
docstring・コメントでの**言及**を違反と誤判定する。本モジュール群は「`M1` 等を書かない」
という規約そのものを docstring で説明しており、素朴なテキスト検査では常に赤になる。
docstring を除いた文字列定数だけを見る。
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

from simulator.main.tester_settings.ea_input_map import SUBJECT_SUFFIX
from simulator.usecase.tester_settings.enums import TIMEFRAME_INI_LABELS, TickModel

#: `simulator/sim_ui/` の根（本ファイルの parents[2]）。
_SIM_UI_ROOT = Path(__file__).resolve().parents[2]

#: 走査対象（Phase 8 の schema 供給経路）。既存モジュールは対象外である——`period="M1"`
#: のようなデータセットの実測値は本規約の対象ではない（`.ini` 語彙の複製ではない）。
_SCANNED = (
    "usecase/settings_schema_ports.py",
    "usecase/list_settings_schema.py",
    "adapter/tester_settings_schema_catalog.py",
    "adapter/settings_schema_api_controller.py",
)

#: 完全一致で禁止する語彙（`.ini` の値そのもの）。単一ソースから導く。
_FORBIDDEN_EXACT = frozenset(
    {*TIMEFRAME_INI_LABELS.values(), *(str(int(model)) for model in TickModel)}
)
#: 部分一致で禁止する語彙（連結・f-string で埋め込まれる形を捕らえる）。
_FORBIDDEN_SUBSTRING = frozenset({SUBJECT_SUFFIX})


def _docstring_nodes(tree: ast.AST) -> "set[int]":
    """docstring として置かれた文字列定数の id 集合（言及を違反にしないため）。"""
    ids: "set[int]" = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            body = getattr(node, "body", [])
            if body and isinstance(body[0], ast.Expr):
                value = body[0].value
                if isinstance(value, ast.Constant) and isinstance(value.value, str):
                    ids.add(id(value))
    return ids


def _violations(source: str) -> "list[str]":
    """docstring 以外の文字列定数に現れた禁止語彙を返す。"""
    tree = ast.parse(source)
    skip = _docstring_nodes(tree)
    found: "list[str]" = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Constant) or not isinstance(node.value, str):
            continue
        if id(node) in skip:
            continue
        if node.value in _FORBIDDEN_EXACT:
            found.append(node.value)
        found.extend(token for token in _FORBIDDEN_SUBSTRING if token in node.value)
    return sorted(found)


def test_the_forbidden_vocabulary_is_derived_from_the_single_sources() -> None:
    """禁止集合が空（＝検定が空振り）でないこと。"""
    assert len(_FORBIDDEN_EXACT) >= len(TIMEFRAME_INI_LABELS)
    assert _FORBIDDEN_SUBSTRING


def test_the_detector_sees_a_duplicated_timeframe_label() -> None:
    # Arrange: 単一ソースから合成した違反サンプル（リテラルを手書きしない）
    label = sorted(TIMEFRAME_INI_LABELS.values())[0]
    sample = f'PERIODS = ["{label}"]\n'
    # Act / Assert
    assert _violations(sample) == [label]


def test_the_detector_sees_a_duplicated_model_token() -> None:
    # Arrange
    token = str(int(sorted(TickModel)[0]))
    sample = f'MODELS = {{"{token}": "every tick"}}\n'
    # Act / Assert
    assert _violations(sample) == [token]


def test_the_detector_sees_a_subject_suffix_embedded_in_an_f_string() -> None:
    # Arrange
    sample = f'name = f"{{stem}}{SUBJECT_SUFFIX}"\n'
    # Act / Assert
    assert _violations(sample) == [SUBJECT_SUFFIX]


def test_the_detector_does_not_flag_docstring_mentions() -> None:
    # Arrange: 規約そのものを説明する docstring（本モジュール群が実際に持つ形）
    label = sorted(TIMEFRAME_INI_LABELS.values())[0]
    sample = f'"""ラベル（{label}・{SUBJECT_SUFFIX}）を書かない。"""\nX = 1\n'
    # Act / Assert
    assert _violations(sample) == []


@pytest.mark.parametrize("relative", _SCANNED)
def test_schema_modules_carry_no_mt5_vocabulary_literal(relative: str) -> None:
    # Arrange
    path = _SIM_UI_ROOT / relative
    assert path.exists(), relative  # 走査の取りこぼしで空振りしない
    # Act / Assert
    assert _violations(path.read_text(encoding="utf-8")) == []
