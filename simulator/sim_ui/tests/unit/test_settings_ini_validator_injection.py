"""A-SettingsIniValidator が検証関数を**注入**で受けることを固定する（ISSUE-479 F-5）。

なぜ必要か（層順序の逆流）:
    ``simulator/sim_ui/adapter/settings_ini_validator.py`` は adapter でありながら
    ``simulator.framework.tester_settings`` を module-level import していた。これは
    simulator 本番における唯一の inner → framework 辺であり、adapter を単体で読み込むだけで
    framework 側の設定検証系一式が引き込まれる。依存の向きは検定でしか保たれない。

本 Wave の解:
    具象検証関数は Composition Root（``composition_root_jobs.build_settings_validation_port``）
    が束縛し、adapter は受け取った関数を呼ぶだけにする（DIP）。**既定値は置かない**——
    既定値を置くと「注入し忘れても動く」ため、逆流が黙って復活する。

翻訳規律は 1 文字も変えない:
    ``SettingsError`` **だけ**を ``JobSubmissionInvalidError`` へ写し、診断値は
    ``rule_id → error_id → key → value`` の順で ``k=v`` を ``・`` 連結する。

計算量検定（絶対命令 2026-08-28）: ``validate()`` 1 回あたりの検証発行は 1 回
（発行 − 使用 = 0）。inputs 10 / 100 要素の 2 点で発行数が変わらない。
"""

from __future__ import annotations

import ast
import inspect
from pathlib import Path

import pytest

from simulator.domain.tester_settings_exceptions import SettingsValueError
from simulator.sim_ui.adapter.settings_ini_validator import SettingsIniValidator
from simulator.sim_ui.main.composition_root_jobs import build_settings_validation_port
from simulator.sim_ui.usecase.job_models import JobSubmissionInvalidError

_SOURCE = Path(inspect.getsourcefile(SettingsIniValidator)).resolve()


# --------------------------------------------------------------------------------------
# 依存の向き（adapter → framework の辺が無い）
# --------------------------------------------------------------------------------------
def _imported_modules(path: Path) -> "set[str]":
    """関数内の遅延 import も含めた import 先モジュール名（宣言を迂回する穴を作らない）。"""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    out: "set[str]" = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            out.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and not node.level:
            out.add(node.module or "")
    return out


def test_the_adapter_does_not_reach_into_the_framework_layer() -> None:
    """adapter が framework を import しない（層順序の逆流ゼロ）。"""
    # Arrange / Act
    offenders = sorted(
        m for m in _imported_modules(_SOURCE)
        if m.split(".")[:2] == ["simulator", "framework"]
    )
    # Assert
    assert offenders == [], (
        f"adapter が framework を import しています: {offenders}。"
        " 具象は Composition Root で束縛し、adapter は注入で受けてください（DIP）。"
    )


def test_the_validator_refuses_to_be_built_without_an_injected_function() -> None:
    """既定値を置かない（注入し忘れても動く実装は逆流を黙って復活させる）。"""
    with pytest.raises(TypeError):
        SettingsIniValidator()  # type: ignore[call-arg]


# --------------------------------------------------------------------------------------
# 翻訳規律（現行文言と 1 文字も変わらない）
# --------------------------------------------------------------------------------------
def _validator(fake):
    return SettingsIniValidator(fake)


def test_a_passing_validation_function_produces_no_error() -> None:
    # Arrange
    validator = _validator(lambda tester, inputs: None)
    # Act / Assert（例外が出ないことが期待）
    assert validator.validate({"Deposit": "1"}, ["a=1"]) is None


def test_a_settings_error_is_translated_with_the_diagnostics_in_a_fixed_order() -> None:
    """診断値は rule_id → error_id → key → value の順で ``・`` 連結される。"""
    # Arrange
    error = SettingsValueError(key="Deposit", value="0", expected="> 0", rule_id="B-3")

    def _fake(tester, inputs):
        raise error

    validator = _validator(_fake)
    # Act
    with pytest.raises(JobSubmissionInvalidError) as ei:
        validator.validate({"Deposit": "0"}, [])
    # Assert
    message = str(ei.value)
    assert message.startswith("Tester Settings が設定規則に反しています: ")
    diagnostics = message[message.index("（") + 1:message.rindex("）")]
    assert diagnostics == "rule_id='B-3'・error_id='E-04'・key='Deposit'・value='0'"
    assert ei.value.__cause__ is error


def test_an_error_without_diagnostics_gets_no_parenthesised_suffix() -> None:
    """境界: context が空なら括弧を出さない（空の括弧を残さない）。"""
    # Arrange
    class _Bare(SettingsValueError):
        ERROR_ID = ""

    def _fake(tester, inputs):
        raise _Bare("だめ")

    # Act
    with pytest.raises(JobSubmissionInvalidError) as ei:
        _validator(_fake).validate({}, [])
    # Assert
    assert str(ei.value) == "Tester Settings が設定規則に反しています: だめ"


def test_unexpected_exceptions_are_not_disguised_as_a_bad_request() -> None:
    """異常系: SettingsError 以外は翻訳しない（実装の壊れを「設定が悪い」に見せない）。"""
    def _fake(tester, inputs):
        raise AttributeError("実装の壊れ")

    with pytest.raises(AttributeError):
        _validator(_fake).validate({}, [])


def test_the_injected_function_receives_copies_not_the_caller_objects() -> None:
    """境界の受け渡しは dict / list へ写して渡す（呼出側の器を握らせない）。"""
    # Arrange
    seen = {}

    def _fake(tester, inputs):
        seen["tester"] = tester
        seen["inputs"] = inputs

    tester_in = {"Deposit": "1"}
    inputs_in = ("a=1",)
    # Act
    _validator(_fake).validate(tester_in, inputs_in)
    # Assert
    assert seen["tester"] == {"Deposit": "1"} and seen["tester"] is not tester_in
    assert seen["inputs"] == ["a=1"]


# --------------------------------------------------------------------------------------
# Composition Root の束縛（具象の選択は 1 箇所）
# --------------------------------------------------------------------------------------
def test_the_composition_root_binds_the_real_validation_function() -> None:
    """Root が framework の実体を束縛する（規則の第 2 実装を作らない）。"""
    # Arrange
    from simulator.framework.tester_settings import tester_settings_from_mapping
    # Act
    port = build_settings_validation_port()
    # Assert
    assert isinstance(port, SettingsIniValidator)
    assert port._validate_mapping is tester_settings_from_mapping


# --------------------------------------------------------------------------------------
# 計算量検定（Test Spy・発行 − 使用 = 0）
# --------------------------------------------------------------------------------------
def test_validate_issues_exactly_one_validation_per_call() -> None:
    """``validate()`` 1 回あたりの検証発行は 1 回（作って捨てる検証が無い）。"""
    # Arrange
    calls = []

    def _spy(tester, inputs):
        calls.append((tester, inputs))

    validator = _validator(_spy)
    # Act
    validations_requested = 3
    for _ in range(validations_requested):
        validator.validate({"Deposit": "1"}, ["a=1"])
    # Assert（期待値は要求回数から導出する。リテラルを焼き込まない）
    assert len(calls) - validations_requested == 0


def test_validate_issue_count_does_not_grow_with_the_input_size() -> None:
    """inputs 10 / 100 要素の 2 点で発行数が変わらない（オーダーの表明）。"""
    # Arrange / Act
    measured = {}
    for size in (10, 100):
        calls = []
        _validator(lambda tester, inputs: calls.append(1)).validate(
            {f"k{i}": str(i) for i in range(size)}, [f"a{i}={i}" for i in range(size)]
        )
        measured[size] = len(calls)
    # Assert
    assert measured[10] == measured[100], f"入力量で発行数が変わりました: {measured}"
    assert measured[10] - 1 == 0  # validate() 1 回 = 検証 1 回（発行 − 使用 = 0）
