"""宣言した欄に読み手が無ければ落ちる（ISSUE-535・死んだ欄の再発防止）。

何を解くか:
    「`EngineBinding.known_ea_names`」 は **設定されるだけで読み手が 1 つも無い**状態で残って
    いた（ISSUE-525 で N-01 の判定源を合流点の 「`RunScopeInputs`」 へ移したとき、
    「`EngineBinding`」 側の欄が読まれなくなった）。出力は 1 ビットも変わらないため、
    既存の状態検証は**原理的に落ちない**。しかも docstring は「N-01 の事前検証に使う」と
    書き続けており、宣言が嘘になっていた。

    「削除して終わり」にすると次の DTO で同じ取り残しが起きる。したがって**宣言した欄に
    読み手が無ければ落ちる**検査を持つ。測るのは構文木であり、grep の目視ではない。

対象（範囲）と根拠:
    宣言側 … `simulator/main/**`（Composition Root）で宣言される全 `@dataclass`。
        この層は「他の層が値を詰める注入束」の住処であり、供給側と読み手が別パッケージに
        分かれるため、読み手が消えても構築側は無傷で残る——取り残しが起きる形そのもので
        ある（ISSUE-525 の実例がここで起きた）。**特定の DTO を名指さない**ので、
        ここに DTO を 1 つ足せば既定で対象に入る。
    読み手側 … リポジトリの全 `*.py`（生成物・第三者ソースだけを除く）。読み手が本番か
        検定かを問わない——問うと「検定だけが読む欄」を撤去対象に含めることになり、
        本段の承認範囲（死んだ欄 1 件）を超える。より強い基準（本番に読み手が要る）は
        採らないことを明示しておく。
    範囲を広げなかった理由（実測 2026-09-26・本作業ツリー）: 宣言側を `simulator/**` へ
        広げると 155 DTO・1,034 欄のうち **285 件**が読み手 0 と出る。その大半は本器の
        型解決が届かない形（未注釈の戻り値経由など）であり、通すには 285 件の除外表が
        必要になる。それは「既定で拾う」形の裏返し＝逃げ道であり、本段では採らない
        （申し送り: 型解決を広げてから範囲を広げる）。
"""
from __future__ import annotations

from pathlib import Path

import pytest

from simulator.tests.declared_field_readers import (
    DECLARATION_SCOPE,
    EXEMPT_UNREAD_FIELDS,
    READER_SCOPE,
    AmbiguousDeclarationError,
    ExemptionError,
    unread_declared_fields,
)

#: リポジトリ根（本ファイルは `<root>/simulator/tests/unit/` に在る）。
REPOSITORY_ROOT = Path(__file__).resolve().parents[3]


def _write(root, relative: str, source: str) -> None:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(source, encoding="utf-8")


class TestAFieldWithNoReaderIsReported:
    """TC-001: 宣言した欄に読み手が 1 つも無ければ報告される。"""

    def test_a_declared_field_that_nobody_reads_is_reported(self, tmp_path):
        # Arrange: 1 欄だけ読み手が在る DTO を合成する。
        _write(tmp_path, "pkg/dto.py", (
            "from dataclasses import dataclass\n"
            "\n"
            "\n"
            "@dataclass(frozen=True)\n"
            "class Bundle:\n"
            "    used: str\n"
            "    unread: str\n"
        ))
        _write(tmp_path, "pkg/consumer.py", (
            "from pkg.dto import Bundle\n"
            "\n"
            "\n"
            "def consume(bundle: Bundle) -> str:\n"
            "    return bundle.used\n"
        ))

        # Act
        reported = unread_declared_fields(
            root=tmp_path,
            declared_under=("pkg/dto.py",),
            read_under=("pkg",),
            exemptions={},
        )

        # Assert
        assert [(f.dto, f.field) for f in reported] == [("Bundle", "unread")]


class TestAFieldWithAReaderIsNotReported:
    """誤検出しない対照。読み手に届く経路ごとに 1 件ずつ固定する。

    ここが弱いと検査は「読み手が在るのに落ちる」器になり、除外表で黙らせる運用へ倒れる。
    各経路は本番のコードに実在する形から取った（散文の言い換えをしない）。
    """

    def test_a_reader_reached_through_another_dto_field_counts(self, tmp_path):
        """TC-002: 「`ctx.binding.field`」 の形（文脈 DTO が注入束を持つ・本番実在）。"""
        # Arrange
        _write(tmp_path, "pkg/dto.py", (
            "from dataclasses import dataclass\n"
            "\n"
            "\n"
            "@dataclass(frozen=True)\n"
            "class Bundle:\n"
            "    reached: str\n"
            "\n"
            "\n"
            "@dataclass(frozen=True)\n"
            "class Context:\n"
            "    bundle: Bundle\n"
        ))
        _write(tmp_path, "pkg/consumer.py", (
            "from pkg.dto import Context\n"
            "\n"
            "\n"
            "def consume(ctx: Context) -> str:\n"
            "    return ctx.bundle.reached\n"
        ))

        # Act
        reported = unread_declared_fields(
            root=tmp_path,
            declared_under=("pkg/dto.py",),
            read_under=("pkg",),
            exemptions={},
        )

        # Assert: 「`Context.bundle`」 も 「`Bundle.reached`」 も読まれている。
        assert reported == ()

    def test_a_reader_iterating_a_declared_table_counts(self, tmp_path):
        """TC-003: 宣言表を回して読む形（`for rule in RULES: rule.field`・本番実在）。"""
        # Arrange
        _write(tmp_path, "pkg/dto.py", (
            "from dataclasses import dataclass\n"
            "\n"
            "\n"
            "@dataclass(frozen=True)\n"
            "class Rule:\n"
            "    reached: str\n"
            "\n"
            "\n"
            'RULES: "tuple[Rule, ...]" = (Rule(reached="a"),)\n'
        ))
        _write(tmp_path, "pkg/consumer.py", (
            "from pkg.dto import RULES\n"
            "\n"
            "\n"
            "def consume() -> list:\n"
            "    return [rule.reached for rule in RULES]\n"
        ))

        # Act
        reported = unread_declared_fields(
            root=tmp_path,
            declared_under=("pkg/dto.py",),
            read_under=("pkg",),
            exemptions={},
        )

        # Assert
        assert reported == ()

    def test_a_reader_of_an_unpacked_tuple_member_counts(self, tmp_path):
        """TC-011: 組で返る戻りを展開して読む形（`_, _, meta = run(...)`・本番実在）。"""
        # Arrange
        _write(tmp_path, "pkg/dto.py", (
            "from dataclasses import dataclass\n"
            "\n"
            "\n"
            "@dataclass(frozen=True)\n"
            "class Metadata:\n"
            "    reached: str\n"
            "\n"
            "\n"
            'def run() -> "tuple[int, Metadata]":\n'
            '    return 0, Metadata(reached="a")\n'
        ))
        _write(tmp_path, "pkg/consumer.py", (
            "from pkg.dto import run\n"
            "\n"
            "\n"
            "def consume() -> str:\n"
            "    _code, metadata = run()\n"
            "    return metadata.reached\n"
        ))

        # Act
        reported = unread_declared_fields(
            root=tmp_path,
            declared_under=("pkg/dto.py",),
            read_under=("pkg",),
            exemptions={},
        )

        # Assert
        assert reported == ()

    def test_a_reader_of_an_unannotated_table_literal_counts(self, tmp_path):
        """TC-010: 注釈の無い宣言表を引いて読む形（`_FORMS = {...}` → `.get()`・本番実在）。

        中身が同じ型のコンストラクタ呼出だけで書かれているなら、注釈が無くても中身は解ける。
        """
        # Arrange
        _write(tmp_path, "pkg/dto.py", (
            "from dataclasses import dataclass\n"
            "\n"
            "\n"
            "@dataclass(frozen=True)\n"
            "class Form:\n"
            "    reached: str\n"
            "\n"
            "\n"
            'FORMS = {"a": Form(reached="x"), "b": Form(reached="y")}\n'
        ))
        _write(tmp_path, "pkg/consumer.py", (
            "from pkg.dto import FORMS\n"
            "\n"
            "\n"
            "def consume(form: str):\n"
            "    spec = FORMS.get(form)\n"
            "    return spec.reached\n"
        ))

        # Act
        reported = unread_declared_fields(
            root=tmp_path,
            declared_under=("pkg/dto.py",),
            read_under=("pkg",),
            exemptions={},
        )

        # Assert
        assert reported == ()

    def test_a_reader_over_concatenated_tables_counts(self, tmp_path):
        """TC-009: 明示表と導出表を連結して回す形（`EXPLICIT + derived(...)`・本番実在）。"""
        # Arrange
        _write(tmp_path, "pkg/dto.py", (
            "from dataclasses import dataclass\n"
            "\n"
            "\n"
            "@dataclass(frozen=True)\n"
            "class Binding:\n"
            "    reached: str\n"
            "\n"
            "\n"
            'EXPLICIT: "tuple[Binding, ...]" = ()\n'
            "\n"
            "\n"
            'def derived() -> "tuple[Binding, ...]":\n'
            "    return ()\n"
        ))
        _write(tmp_path, "pkg/consumer.py", (
            "from pkg.dto import EXPLICIT, derived\n"
            "\n"
            "\n"
            "def consume() -> list:\n"
            "    bindings = EXPLICIT + derived()\n"
            "    return [b.reached for b in bindings]\n"
        ))

        # Act
        reported = unread_declared_fields(
            root=tmp_path,
            declared_under=("pkg/dto.py",),
            read_under=("pkg",),
            exemptions={},
        )

        # Assert
        assert reported == ()

    def test_a_reader_through_a_nested_mapping_lookup_counts(self, tmp_path):
        """TC-008: 二段の対応表を引いて読む形（`TABLE.get(k).get(n).field`・本番実在）。"""
        # Arrange
        _write(tmp_path, "pkg/dto.py", (
            "from dataclasses import dataclass\n"
            "\n"
            "\n"
            "@dataclass(frozen=True)\n"
            "class InputBinding:\n"
            "    reached: str\n"
            "\n"
            "\n"
            'BINDINGS: "dict[str, dict[str, InputBinding]]" = {}\n'
        ))
        _write(tmp_path, "pkg/consumer.py", (
            "from pkg.dto import BINDINGS\n"
            "\n"
            "\n"
            "def consume(ea_name: str, input_name: str):\n"
            "    table = BINDINGS.get(ea_name, {})\n"
            "    binding = table.get(input_name)\n"
            "    return binding.reached\n"
        ))

        # Act
        reported = unread_declared_fields(
            root=tmp_path,
            declared_under=("pkg/dto.py",),
            read_under=("pkg",),
            exemptions={},
        )

        # Assert
        assert reported == ()

    def test_a_reader_of_an_annotated_return_value_counts(self, tmp_path):
        """TC-007: 組立関数の戻りを受けて読む形（`metadata = build(...)`・本番実在）。"""
        # Arrange
        _write(tmp_path, "pkg/dto.py", (
            "from dataclasses import dataclass\n"
            "\n"
            "\n"
            "@dataclass(frozen=True)\n"
            "class Metadata:\n"
            "    reached: str\n"
            "\n"
            "\n"
            "def build() -> Metadata:\n"
            '    return Metadata(reached="a")\n'
        ))
        _write(tmp_path, "pkg/consumer.py", (
            "from pkg.dto import build\n"
            "\n"
            "\n"
            "def consume() -> str:\n"
            "    metadata = build()\n"
            "    return metadata.reached\n"
        ))

        # Act
        reported = unread_declared_fields(
            root=tmp_path,
            declared_under=("pkg/dto.py",),
            read_under=("pkg",),
            exemptions={},
        )

        # Assert
        assert reported == ()

    def test_reflective_access_counts_as_reading_every_field(self, tmp_path):
        """TC-006: 欄名を走査して読む形（`getattr(ctx.window, name)`・本番実在）。

        反射で読む側は欄名を書かない。ここを数えないと、反射で全欄を配っている DTO が
        まるごと「読み手 0」に見える。
        """
        # Arrange
        _write(tmp_path, "pkg/dto.py", (
            "from dataclasses import dataclass\n"
            "\n"
            "\n"
            "@dataclass(frozen=True)\n"
            "class Window:\n"
            "    start: int\n"
            "    end: int\n"
        ))
        _write(tmp_path, "pkg/consumer.py", (
            "from pkg.dto import Window\n"
            "\n"
            "\n"
            "def consume(window: Window, name: str):\n"
            "    return getattr(window, name)\n"
        ))

        # Act
        reported = unread_declared_fields(
            root=tmp_path,
            declared_under=("pkg/dto.py",),
            read_under=("pkg",),
            exemptions={},
        )

        # Assert
        assert reported == ()

    def test_a_reader_inside_the_dto_itself_counts(self, tmp_path):
        """TC-005: 自分の持ちものを自分の演算で読む形（「`self.field`」・本番実在）。"""
        # Arrange
        _write(tmp_path, "pkg/dto.py", (
            "from dataclasses import dataclass\n"
            "\n"
            "\n"
            "@dataclass(frozen=True)\n"
            "class Bundle:\n"
            "    reached: str\n"
            "\n"
            "    def shout(self) -> str:\n"
            "        return self.reached.upper()\n"
        ))

        # Act
        reported = unread_declared_fields(
            root=tmp_path,
            declared_under=("pkg/dto.py",),
            read_under=("pkg",),
            exemptions={},
        )

        # Assert
        assert reported == ()

    def test_a_reader_iterating_a_declared_mapping_counts(self, tmp_path):
        """TC-004: `for rule_id, rule in RULES.items(): rule.field` の形（本番実在）。"""
        # Arrange
        _write(tmp_path, "pkg/dto.py", (
            "from dataclasses import dataclass\n"
            "\n"
            "\n"
            "@dataclass(frozen=True)\n"
            "class Rule:\n"
            "    reached: str\n"
            "\n"
            "\n"
            'RULES: "dict[str, Rule]" = {"a": Rule(reached="a")}\n'
        ))
        _write(tmp_path, "pkg/consumer.py", (
            "from pkg.dto import RULES\n"
            "\n"
            "\n"
            "def consume() -> list:\n"
            "    seen = []\n"
            "    for rule_id, rule in RULES.items():\n"
            "        seen.append((rule_id, rule.reached))\n"
            "    return seen\n"
        ))

        # Act
        reported = unread_declared_fields(
            root=tmp_path,
            declared_under=("pkg/dto.py",),
            read_under=("pkg",),
            exemptions={},
        )

        # Assert
        assert reported == ()


class TestExemptionsCannotBecomeAnEscapeHatch:
    """除外は「理由つきの宣言」でしか成立しない（既定で拾う形へ倒さない）。

    `sim_offered` の先例と同じ規律である: 宣言の欠落を「提供しない」へ倒さず、
    宣言の側に Fail-Stop を置く。ここでは「理由の無い除外」「もう当たらない除外」を
    どちらも失敗させる。
    """

    def _two_field_tree(self, tmp_path) -> None:
        _write(tmp_path, "pkg/dto.py", (
            "from dataclasses import dataclass\n"
            "\n"
            "\n"
            "@dataclass(frozen=True)\n"
            "class Bundle:\n"
            "    used: str\n"
            "    unread: str\n"
        ))
        _write(tmp_path, "pkg/consumer.py", (
            "from pkg.dto import Bundle\n"
            "\n"
            "\n"
            "def consume(bundle: Bundle) -> str:\n"
            "    return bundle.used\n"
        ))

    def test_an_exemption_without_a_reason_fails(self, tmp_path):
        """TC-012: 理由の無い除外は認めない（空白だけも同じ）。"""
        # Arrange
        self._two_field_tree(tmp_path)

        # Act / Assert
        with pytest.raises(ExemptionError) as excinfo:
            unread_declared_fields(
                root=tmp_path,
                declared_under=("pkg/dto.py",),
                read_under=("pkg",),
                exemptions={("Bundle", "unread"): "   "},
            )
        assert "Bundle.unread" in str(excinfo.value)

    def test_an_exemption_with_a_reason_silences_that_field_only(self, tmp_path):
        """TC-013: 理由つきの除外はその 1 欄だけを黙らせる。"""
        # Arrange
        self._two_field_tree(tmp_path)

        # Act
        reported = unread_declared_fields(
            root=tmp_path,
            declared_under=("pkg/dto.py",),
            read_under=("pkg",),
            exemptions={("Bundle", "unread"): "外部の取込様式が要求する欄であり本体は読まない"},
        )

        # Assert
        assert reported == ()

    def test_an_exemption_that_no_longer_applies_fails(self, tmp_path):
        """TC-014: もう当たらない除外は認めない（読み手ができた／欄が消えた）。

        残った除外は「既定で拾わない範囲」を静かに広げる。解消したら消させる。
        """
        # Arrange: どの欄にも読み手が在る木（＝除外すべきものが 1 つも無い）。
        _write(tmp_path, "pkg/dto.py", (
            "from dataclasses import dataclass\n"
            "\n"
            "\n"
            "@dataclass(frozen=True)\n"
            "class Bundle:\n"
            "    used: str\n"
        ))
        _write(tmp_path, "pkg/consumer.py", (
            "from pkg.dto import Bundle\n"
            "\n"
            "\n"
            "def consume(bundle: Bundle) -> str:\n"
            "    return bundle.used\n"
        ))

        # Act / Assert
        with pytest.raises(ExemptionError) as excinfo:
            unread_declared_fields(
                root=tmp_path,
                declared_under=("pkg/dto.py",),
                read_under=("pkg",),
                exemptions={("Bundle", "used"): "かつて読み手が無かった"},
            )
        assert "Bundle.used" in str(excinfo.value)


class TestEveryDeclaredFieldInTheCompositionRootHasAReader:
    """本番の門（ISSUE-535 の本体）。宣言側は 「`DECLARATION_SCOPE`」 の宣言 1 箇所から引く。"""

    def test_no_declared_field_is_left_without_a_reader(self):
        """TC-015: Composition Root の全 DTO の全欄に読み手が在る。

        撤去前は 「`EngineBinding.known_ea_names`」 で落ちる（読み手 0 の欄が在る）。
        新しい DTO・新しい欄を足して読み手を書かなければ、同じようにここで落ちる。
        """
        # Arrange / Act
        reported = unread_declared_fields(
            root=REPOSITORY_ROOT,
            declared_under=DECLARATION_SCOPE,
            read_under=READER_SCOPE,
            exemptions=EXEMPT_UNREAD_FIELDS,
        )

        # Assert
        assert reported == (), "読み手が 1 つも無い欄: " + ", ".join(str(f) for f in reported)


class TestTheCountIsTypeAwareNotNameBased:
    """属性名だけを数える器では本件を捕まえられない（本件がまさにその形）。

    「`known_ea_names`」 は合流点の別 DTO では読まれている。名前だけを数えると、その読み手を
    こちらの読み手と取り違えて緑になる。変異 M3（土台の型を見ない器へ退化させる）は
    この検定だけが捕まえる。
    """

    def test_a_field_read_only_on_another_dto_is_still_reported(self, tmp_path):
        """TC-016: 同じ綴りの欄が別の DTO で読まれていても、こちらの欄は報告される。"""
        # Arrange: 綴りが同じ欄を 2 つの DTO が持ち、読み手は片方だけに在る。
        _write(tmp_path, "pkg/dto.py", (
            "from dataclasses import dataclass\n"
            "\n"
            "\n"
            "@dataclass(frozen=True)\n"
            "class Injected:\n"
            "    shared_name: str\n"
            "\n"
            "\n"
            "@dataclass(frozen=True)\n"
            "class Confluence:\n"
            "    shared_name: str\n"
        ))
        _write(tmp_path, "pkg/consumer.py", (
            "from pkg.dto import Confluence\n"
            "\n"
            "\n"
            "def consume(inputs: Confluence) -> str:\n"
            "    return inputs.shared_name\n"
        ))

        # Act
        reported = unread_declared_fields(
            root=tmp_path,
            declared_under=("pkg/dto.py",),
            read_under=("pkg",),
            exemptions={},
        )

        # Assert: 読み手が在るのは 「`Confluence`」 の側だけ。
        assert [(f.dto, f.field) for f in reported] == [("Injected", "shared_name")]


class TestTheMeasurementStopsWhenItCannotSayWhatItMeasured:
    """解けない入力では黙って数えない（測れないものを測ったふりにしない）。"""

    def test_a_dto_name_declared_in_two_places_fails_the_scan(self, tmp_path):
        """TC-017: 同名の DTO が 2 つの実体で宣言されていたら止まる。

        名前で引く索引は同名を 1 つに畳んでしまう。畳むと片方の読み手がもう片方の読み手として
        数えられ、**死んだ欄を隠す**（本段が捕まえたい向きの誤り）。リポジトリ全体には現に
        同名の `@dataclass` が複数ある（実測 2026-09-26: 14 件）ので、対象側に同名が入って
        きたら必ず止める。
        """
        # Arrange: 対象側と対象外に同名の DTO を置く。
        _write(tmp_path, "pkg/dto.py", (
            "from dataclasses import dataclass\n"
            "\n"
            "\n"
            "@dataclass(frozen=True)\n"
            "class Bundle:\n"
            "    unread: str\n"
        ))
        _write(tmp_path, "other/dto.py", (
            "from dataclasses import dataclass\n"
            "\n"
            "\n"
            "@dataclass(frozen=True)\n"
            "class Bundle:\n"
            "    unread: str\n"
        ))
        _write(tmp_path, "other/consumer.py", (
            "from other.dto import Bundle\n"
            "\n"
            "\n"
            "def consume(bundle: Bundle) -> str:\n"
            "    return bundle.unread\n"
        ))

        # Act / Assert
        with pytest.raises(AmbiguousDeclarationError) as excinfo:
            unread_declared_fields(
                root=tmp_path,
                declared_under=("pkg",),
                read_under=(".",),
                exemptions={},
            )
        assert "Bundle" in str(excinfo.value)

    def test_a_class_level_constant_is_not_counted_as_a_field(self, tmp_path):
        """TC-018: 「`ClassVar`」 は欄ではない（読み手が無くても報告しない）。

        `dataclass` は 「`ClassVar`」 を欄にしない。欄として数えると、定数を 1 つ置いた
        だけで検査が赤くなり、除外表で黙らせる運用へ倒れる。
        """
        # Arrange
        _write(tmp_path, "pkg/dto.py", (
            "from dataclasses import dataclass\n"
            "from typing import ClassVar\n"
            "\n"
            "\n"
            "@dataclass(frozen=True)\n"
            "class Bundle:\n"
            '    NOBODY_READS_THIS: ClassVar[str] = "constant"\n'
            "    reached: str\n"
        ))
        _write(tmp_path, "pkg/consumer.py", (
            "from pkg.dto import Bundle\n"
            "\n"
            "\n"
            "def consume(bundle: Bundle) -> str:\n"
            "    return bundle.reached\n"
        ))

        # Act
        reported = unread_declared_fields(
            root=tmp_path,
            declared_under=("pkg/dto.py",),
            read_under=("pkg",),
            exemptions={},
        )

        # Assert
        assert reported == ()
