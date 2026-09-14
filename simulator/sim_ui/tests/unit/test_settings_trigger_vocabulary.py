"""発火条件の語彙が front と宣言側で一致することの機械検査（R-9 の結線ガード）。

## 何を防ぐか（実測された穴）

`UnsupportedNotice.trigger` は **文字列**で front へ渡り、front は `switch` で照合する。
両者を結んでいるのは文字列一致だけであり、片方だけ改名しても**どのゲートも落ちない**。
実測（2026-08-19・変異注入）: `unsupported.UI_TRIGGER_OFF_PROFILE` の値を改名すると、
python 側は定数を記号で参照するため 458 passed、web 側は fixture が独自に語彙を持つため
291 passed で通過した。front の該当判定だけが静かに死ぬ（＝R-9 で塞いだはずの
「宣言と実装が静かにずれる」構造が、語彙の層に残っていた）。

## 施行する不変条件

1. front が実装する発火条件（`const TRIGGER_* = "..."`）は、宣言側の語彙
   （`UI_TRIGGER_MODES`）の**部分集合**である（front が知らない語彙を作らない）。
2. 宣言側にあって front に無い語彙は `UI_TRIGGER_NONE` **ちょうど 1 つ**である。
   `none` は「生トークンでは判定できない」＝front に分岐を持たない形であり、front の
   `switch` の `default` が受ける。それ以外の形を front が実装し忘れていれば落とす。
3. テストダブル（`_settings_schema_fixture.js`）が書く `trigger` も宣言側の語彙に属する。
   fixture が実在しない語彙で緑になっていると、web 検定が「front の実装」ではなく
   「fixture と front の 2 者だけの取り決め」を固定してしまう。

## 方式

front は node で動くため python から実行できない。**ソーステキストから機械抽出**する
（`web/tests/import_source.test.js` が JS 側で採る手段と同型。構造検定の唯一の手段）。
検出器が空振りしていないことは、本ファイル自身の自己検定
（`test_the_extractor_sees_a_renamed_vocabulary`）が固定する。
"""
from __future__ import annotations

import re
from pathlib import Path

from simulator.main.tester_settings.unsupported import UI_TRIGGER_MODES, UI_TRIGGER_NONE

_ROOT = Path(__file__).resolve().parents[4]
_FRONT = _ROOT / "simulator" / "sim_ui" / "web" / "js" / "adapter" / "front"
_PANEL = _FRONT / "sim_tester_settings_panel_view.js"
_FIXTURE = _ROOT / "simulator" / "sim_ui" / "web" / "tests" / "_settings_schema_fixture.js"

#: front の発火条件の宣言（`const TRIGGER_ON_TOKENS = "on_tokens";`）。
_FRONT_CONST = re.compile(r'const\s+TRIGGER_\w+\s*=\s*"(\w+)"')
#: テストダブルが書く発火条件（`trigger: "on_tokens"`）。
_FIXTURE_TRIGGER = re.compile(r'trigger:\s*"(\w+)"')


def _front_vocabulary(source: str) -> "set[str]":
    return set(_FRONT_CONST.findall(source))


def _fixture_vocabulary(source: str) -> "set[str]":
    return set(_FIXTURE_TRIGGER.findall(source))


def test_the_extractor_sees_the_front_declarations() -> None:
    """抽出器が空振りしていないこと（0 件なら以下の主張はすべて無意味になる）。"""
    # Arrange / Act
    found = _front_vocabulary(_PANEL.read_text(encoding="utf-8"))
    # Assert
    assert len(found) >= 2, f"front の発火条件を抽出できていません: {found}"


def test_the_extractor_sees_a_renamed_vocabulary() -> None:
    """検出力の自己検定: 語彙を 1 つ改名した変異を抽出器が別物として見ること。"""
    # Arrange
    original = 'const TRIGGER_OFF_PROFILE = "off_profile";'
    mutated = 'const TRIGGER_OFF_PROFILE = "off_profile_RENAMED";'
    # Act / Assert
    assert _front_vocabulary(original) == {"off_profile"}
    assert _front_vocabulary(mutated) == {"off_profile_RENAMED"}
    assert not _front_vocabulary(mutated) <= UI_TRIGGER_MODES, (
        "改名された語彙が宣言側の集合に含まれてしまっています（検定が素通りする）"
    )


def test_front_implements_only_declared_trigger_vocabulary() -> None:
    """front が宣言側に無い発火条件を実装していないこと。"""
    # Arrange / Act
    front = _front_vocabulary(_PANEL.read_text(encoding="utf-8"))
    # Assert
    unknown = sorted(front - UI_TRIGGER_MODES)
    assert not unknown, (
        f"front が宣言側に無い発火条件を持っています: {unknown}"
        f"（宣言側: {sorted(UI_TRIGGER_MODES)}）"
    )


def test_the_only_form_front_does_not_declare_is_the_unevaluable_one() -> None:
    """宣言側にあって front に無い形は `none` ちょうど 1 つであること。

    `none` は front の `switch` の `default`（＝発火しない）が受けるため定数を持たない。
    それ以外の形が欠けていれば、その形を使う rule が front で**静かに発火しなくなる**。
    """
    # Arrange / Act
    front = _front_vocabulary(_PANEL.read_text(encoding="utf-8"))
    # Assert
    assert UI_TRIGGER_MODES - front == {UI_TRIGGER_NONE}, (
        f"front が実装していない発火条件: {sorted(UI_TRIGGER_MODES - front - {UI_TRIGGER_NONE})}"
    )


def test_the_test_double_uses_only_declared_trigger_vocabulary() -> None:
    """テストダブルの `trigger` も宣言側の語彙に属すること。"""
    # Arrange / Act
    fixture = _fixture_vocabulary(_FIXTURE.read_text(encoding="utf-8"))
    # Assert
    assert fixture, "fixture から `trigger` を抽出できていません（抽出器の空振り）"
    unknown = sorted(fixture - UI_TRIGGER_MODES)
    assert not unknown, (
        f"テストダブルが実在しない発火条件を使っています: {unknown}"
        "（front と fixture の 2 者だけの取り決めを固定してしまう）"
    )
