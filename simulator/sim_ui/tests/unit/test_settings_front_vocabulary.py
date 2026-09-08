"""front が列挙メンバ名を複製しないことの機械検査（ISSUE-420 残項目 1・§18.3 走査要件の残余）.

## 何を防ぐか

Tester Settings の選択肢ラベルは schema（`GET /sim/settings-schema`）が配る。無ラベルの
選択肢では**列挙メンバ名**そのものがラベルとして流れるため、front が
その名前（EVERY_TICK 等の平文表記）をリテラルで持ち込むと「列挙を改名・増減しても UI だけ古い」食い違いが静かに
生まれる。既存の front 語彙ガード（`web/tests/import_source.test.js` 2b）は Model 生値表・
時間足ラベル代表・対象接尾辞を検出するが、**メンバ名の複製は検出しなかった**（本検定が
その残余を塞ぐ）。

## 施行する不変条件

front（`web/js/adapter/front/*.js`）の実行されるコードに、schema が配る列挙
（時間足・Model・Optimization・Dates・ForwardMode・OptimizationCriterion）の
メンバ名が 1 件も現れない。禁止集合は列挙（単一ソース）から導き、**手で列挙しない**
（列挙すればそれ自体が複製になる。`test_settings_schema_single_source.py` と同じ要点）。

## 方式

front は node で動くため python から実行できない。ソーステキストから機械抽出する
（`test_settings_trigger_vocabulary.py` が採る手段と同型）。コメントは剥がして走査する
——front の註釈は面の名前（M1〜M7）や規約の説明でメンバ名と同形の語を正当に使うため、
生テキスト走査は常に赤になる。検出器が空振りしていないことは自己検定で固定する。
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

from simulator.usecase.tester_settings.enums import (
    DatesPreset,
    ForwardMode,
    OptimizationCriterion,
    OptimizationMode,
    TickModel,
    Timeframe,
)

_ROOT = Path(__file__).resolve().parents[4]
_FRONT = _ROOT / "simulator" / "sim_ui" / "web" / "js" / "adapter" / "front"

#: schema が配る列挙（catalog の導出元と同じ集合）。メンバ名は無ラベル時にそのまま
#: ラベルとして front へ流れる語彙である。
_SCHEMA_ENUMS = (
    Timeframe, TickModel, OptimizationMode, DatesPreset, ForwardMode, OptimizationCriterion,
)

#: 禁止語彙＝メンバ名の全集合（単一ソースからの導出。手で列挙しない）。
_MEMBER_NAMES = frozenset(member.name for enum in _SCHEMA_ENUMS for member in enum)


def _strip_js_comments(src: str) -> str:
    """実行されるコードだけを残す（`import_source.test.js` の stripComments と同じ規則）。"""
    src = re.sub(r"/\*.*?\*/", "", src, flags=re.S)
    src = re.sub(r"""(^|[^:"'`\\])//.*$""", r"\1", src, flags=re.M)
    return src


def _offenses(source: str) -> "list[str]":
    """コメントを剥がしたソースに現れた列挙メンバ名を返す（0 件が合格）。"""
    code = _strip_js_comments(source)
    return sorted(
        name for name in _MEMBER_NAMES if re.search(rf"\b{re.escape(name)}\b", code)
    )


def _front_files() -> "list[Path]":
    files = sorted(_FRONT.glob("*.js"))
    assert files, f"走査対象が空です（収集条件の壊れ）: {_FRONT}"
    return files


def test_the_forbidden_vocabulary_is_derived_from_the_enums() -> None:
    """禁止集合が空（＝検定が空振り）でなく、全列挙のメンバが寄与していること。"""
    assert _MEMBER_NAMES
    for enum in _SCHEMA_ENUMS:
        members = {member.name for member in enum}
        assert members and members <= _MEMBER_NAMES, enum.__name__


def test_the_detector_sees_a_duplicated_member_name() -> None:
    """検出力の自己検定: メンバ名をコードへ持ち込んだ変異を検出できること。"""
    # Arrange: 単一ソースから合成した違反サンプル（名前を手書きしない）
    name = sorted(member.name for member in TickModel)[0]
    sample = f'const LABELS = {{ a: "{name}" }};\n'
    # Act / Assert
    assert _offenses(sample) == [name]


def test_the_detector_does_not_flag_comment_mentions() -> None:
    """規約や面名を説明する註釈（コメント）での言及は違反にしないこと。"""
    # Arrange
    name = sorted(member.name for member in TickModel)[0]
    sample = f"// {name} を書かない（schema のラベルを使う）\n/* {name} */\nconst x = 1;\n"
    # Act / Assert
    assert _offenses(sample) == []


def test_the_detector_does_not_flag_embedded_identifiers() -> None:
    """メンバ名を含むだけの別識別子（語境界の外）は違反にしないこと（過検出の防止）。"""
    # Arrange
    name = sorted(member.name for member in TickModel)[0]
    sample = f"const {name}_STATE_OF_SOMETHING_ELSE = 1;\n"
    # Act / Assert
    assert _offenses(sample) == []


@pytest.mark.parametrize("path", _front_files(), ids=lambda p: p.name)
def test_front_modules_carry_no_enum_member_name(path: Path) -> None:
    # Arrange / Act / Assert
    assert _offenses(path.read_text(encoding="utf-8")) == [], (
        f"{path.name} が列挙メンバ名を複製しています（schema の label/token を使うこと）"
    )
