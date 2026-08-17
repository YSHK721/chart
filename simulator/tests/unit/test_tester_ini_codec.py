"""字句層 `ini_codec` の単体テスト（内部設計 §4.1・基本設計 §4.4 R1〜R9）。

本モジュールは実装より先に書いた（フェーズ 3 = Red 先行）。実装投入後は Green。

固定する仕様（内部設計 §4.1.2 の 8 関数）:
    read_bytes / decode / split_lines / parse / serialize /
    read_document / write_document / build_document
"""
from __future__ import annotations

from dataclasses import replace
from datetime import date

import pytest

from simulator.adapter.tester_settings import ini_codec
from simulator.domain.tester_settings_exceptions import IniFormatError, SettingsValueError

# corpus 直読の補助（内部設計 §9.3 D-06）は共有補助モジュールが唯一の宣言を持つ。
from simulator.tests.unit.tester_settings_corpus import (
    corpus_files,
    corpus_tester_keys,
    requires_corpus,
)
from simulator.tests.unit.tester_settings_synthetic import (
    CRLF,
    LF,
    RANGE5_INPUT_LINES,
    SCALAR_INPUT_LINES,
    TESTER_INPUTS_SECTION,
    TESTER_SECTION,
    UTF16BE,
    UTF16LE,
    encode_ini,
    synthetic_ini_lines,
    write_ini,
)
from simulator.usecase.tester_settings.enums import (
    DateRangeKind,
    DatesPreset,
    ForwardMode,
    InputForm,
    OptimizationCriterion,
    OptimizationMode,
    SubjectKind,
    TickModel,
    Timeframe,
)
from simulator.usecase.tester_settings.models import (
    DateRange,
    IniLineKind,
    TesterInput as InputDto,
    TesterSettings as SettingsDto,
)


def _text(lines) -> str:
    """行原文列を CRLF 連結し末尾にも改行を付けた本文（BOM なし）。"""
    return CRLF.join(lines) + CRLF


def _parse(lines, **kwargs):
    return ini_codec.parse(_text(lines), encoding=UTF16LE, **kwargs)


class TestStandardKeyOrderSourceOfTruth:
    """`STANDARD_KEY_ORDER` の**独立検証**（本クラスが唯一の突合点）。

    合成データ生成器（`tester_settings_synthetic.py`）は実装の順序表を import する
    ため、生成器側には「実装のキー順が正しい」ことの検証は残らない。その検証を
    ここに 1 箇所だけ置く:

    1. 設計文書（基本設計 §4.4 標準キー順 / §2.2.3 キー出現順）由来のリテラルとの突合
       — corpus 不在の CI でも常時実行し、空洞化させない。
    2. corpus 44 件の**直読**との突合 — 一次情報そのものとの突合。
       `sample/` は Git 追跡外（F-20・CON-05）のため、内部設計 §9.3 D-06 の
       条件付きスキップ機構を用いる（`TESTER_INI_CORPUS_REQUIRED=1` で必須化）。
    """

    #: 基本設計 §4.4「標準キー順（Expert テスト）」＋ Indicator 順の上位集合。
    #: Expert / Indicator は排他（F-1）のため隣接させても双方の実測順を壊さない。
    #: 員数は corpus 実測で 18（和集合）。1 ファイルあたりの実測最大は 15 キー。
    #: §2.2.3 本文の「17 キー」は誤記（ISSUE-389）。
    DESIGN_KEY_ORDER: tuple[str, ...] = (
        "Expert",
        "Indicator",
        "Symbol",
        "Period",
        "Optimization",
        "Model",
        "Dates",
        "FromDate",
        "ToDate",
        "ForwardMode",
        "ForwardDate",
        "Deposit",
        "Currency",
        "ProfitInPips",
        "Leverage",
        "ExecutionMode",
        "OptimizationCriterion",
        "Visual",
    )

    #: 基本設計 F-12: Indicator テストが持たない Expert 専用 8 キー。
    DESIGN_EXPERT_ONLY_KEYS: frozenset[str] = frozenset(
        {
            "Optimization",
            "ForwardMode",
            "Deposit",
            "Currency",
            "ProfitInPips",
            "Leverage",
            "ExecutionMode",
            "OptimizationCriterion",
        }
    )

    def test_standard_key_order_matches_the_design_documents_order(self):
        # Arrange / Act / Assert: 実装の順序表 == 設計文書の標準キー順
        assert ini_codec.STANDARD_KEY_ORDER == self.DESIGN_KEY_ORDER

    def test_supported_key_set_is_derived_from_the_order_table(self):
        assert ini_codec.TESTER_KEYS == frozenset(self.DESIGN_KEY_ORDER)

    def test_expert_only_keys_match_the_design_document(self):
        from simulator.framework.tester_settings import validation

        assert frozenset(validation.EXPERT_ONLY_KEYS) == self.DESIGN_EXPERT_ONLY_KEYS

    def test_section_names_match_the_design_document(self):
        # R4: 許容セクションは 2 種のみ
        assert ini_codec.SECTION_TESTER == "[Tester]"
        assert ini_codec.SECTION_TESTER_INPUTS == "[TesterInputs]"

    @requires_corpus
    def test_corpus_key_appearance_order_is_a_subsequence_of_the_standard_order(self):
        # Arrange: corpus を直読する（実装の parse を通さず一次情報そのものを見る）
        files = corpus_files()
        assert files, "corpus が 0 件（条件付きスキップの判定と矛盾）"
        # Act / Assert
        for path in files:
            keys = corpus_tester_keys(path)
            positions = [ini_codec.STANDARD_KEY_ORDER.index(key) for key in keys]
            assert positions == sorted(positions), (
                f"{path.name} のキー出現順が標準キー順の部分列でない: {keys}"
            )

    @requires_corpus
    def test_every_standard_key_appears_somewhere_in_the_corpus(self):
        # 順序表に「corpus に存在しないキー」を混ぜていないこと（R12 の許容集合の裏取り）
        observed: set[str] = set()
        for path in corpus_files():
            observed.update(corpus_tester_keys(path))
        assert observed == set(ini_codec.STANDARD_KEY_ORDER)

    @requires_corpus
    def test_expert_only_keys_never_appear_in_indicator_files(self):
        # F-12 を corpus 直読で確認する（実装の判定を通さない）
        from simulator.framework.tester_settings import validation

        expert_only = set(validation.EXPERT_ONLY_KEYS)
        indicator_files = expert_files = 0
        for path in corpus_files():
            keys = set(corpus_tester_keys(path))
            if "Indicator" in keys:
                indicator_files += 1
                assert not (keys & expert_only), f"{path.name} に Expert 専用キーが存在する"
            else:
                expert_files += 1
                assert expert_only <= keys, f"{path.name} に Expert 専用キーが欠けている"
        assert (expert_files, indicator_files) == (31, 13)  # §2.2.3 実測

    @requires_corpus
    def test_corpus_files_carry_exactly_the_two_sections(self):
        for path in corpus_files():
            text = path.read_bytes().decode("utf-16")
            assert text.count(ini_codec.SECTION_TESTER + CRLF) == 1, path.name
            assert text.count(ini_codec.SECTION_TESTER_INPUTS + CRLF) == 1, path.name


class TestLimits:
    """§7.3 の上限定数（内部設計 §4.1.2）。"""

    def test_max_file_bytes_is_one_mebibyte(self):
        assert ini_codec.MAX_FILE_BYTES == 1 << 20

    def test_max_line_chars(self):
        assert ini_codec.MAX_LINE_CHARS == 4096

    def test_max_input_lines(self):
        assert ini_codec.MAX_INPUT_LINES == 256


class TestReadBytes:
    """`read_bytes`: サイズ上限検査のみを行い、内容は解釈しない。"""

    def test_returns_the_file_bytes_verbatim(self, tmp_path):
        # Arrange
        path = tmp_path / "a.ini"
        payload = encode_ini(synthetic_ini_lines())
        path.write_bytes(payload)
        # Act / Assert
        assert ini_codec.read_bytes(path) == payload

    def test_accepts_a_file_of_exactly_the_size_limit(self, tmp_path):
        # Arrange: 境界値（上限ちょうどは超過ではない）
        path = tmp_path / "limit.bin"
        path.write_bytes(b"\xff\xfe" + b"A" * (ini_codec.MAX_FILE_BYTES - 2))
        # Act / Assert
        assert len(ini_codec.read_bytes(path)) == ini_codec.MAX_FILE_BYTES

    def test_rejects_a_file_over_the_size_limit(self, tmp_path):
        # Arrange: 境界値 +1 バイト
        path = tmp_path / "too_big.bin"
        path.write_bytes(b"\xff\xfe" + b"A" * (ini_codec.MAX_FILE_BYTES - 1))
        # Act / Assert
        with pytest.raises(IniFormatError) as excinfo:
            ini_codec.read_bytes(path)
        assert excinfo.value.context["error_id"] == "E-01"
        assert excinfo.value.context["rule_id"] == "R1"

    def test_missing_file_propagates_file_not_found_error(self, tmp_path):
        # 内部設計 §4.5.3: 呼出側のパス誤りは ConfigError へ翻訳しない
        with pytest.raises(FileNotFoundError):
            ini_codec.read_bytes(tmp_path / "absent.ini")

    def test_accepts_str_path(self, tmp_path):
        path = tmp_path / "a.ini"
        path.write_bytes(b"\xff\xfe")
        assert ini_codec.read_bytes(str(path)) == b"\xff\xfe"


class TestDecode:
    """`decode`（R1）: BOM で UTF-16LE / BE を判定する。BOM 不在は E-01。"""

    def test_utf16le_bom_is_detected(self):
        # Arrange
        data = encode_ini(synthetic_ini_lines(), encoding=UTF16LE)
        # Act
        text, encoding = ini_codec.decode(data)
        # Assert
        assert encoding == UTF16LE
        assert text.startswith(";synthetic tester settings")

    def test_utf16be_bom_is_detected(self):
        data = encode_ini(synthetic_ini_lines(), encoding=UTF16BE)
        text, encoding = ini_codec.decode(data)
        assert encoding == UTF16BE
        assert text.startswith(";synthetic tester settings")

    def test_bom_character_is_stripped_from_the_returned_text(self):
        # BOM を本文に残すと 1 行目のコメント判定（F-18）が壊れる
        data = encode_ini(synthetic_ini_lines(), encoding=UTF16LE)
        text, _ = ini_codec.decode(data)
        assert "﻿" not in text

    def test_missing_bom_raises_ini_format_error(self):
        # Arrange
        data = encode_ini(synthetic_ini_lines(), bom=False)
        # Act / Assert
        with pytest.raises(IniFormatError) as excinfo:
            ini_codec.decode(data)
        assert excinfo.value.context["error_id"] == "E-01"
        assert excinfo.value.context["rule_id"] == "R1"

    def test_utf8_payload_without_bom_raises_ini_format_error(self):
        with pytest.raises(IniFormatError):
            ini_codec.decode("[Tester]\r\n".encode("utf-8"))

    def test_path_is_recorded_in_the_error_context(self):
        with pytest.raises(IniFormatError) as excinfo:
            ini_codec.decode(b"[Tester]", path="/tmp/x.ini")
        assert excinfo.value.context["path"] == "/tmp/x.ini"


class TestSplitLines:
    """`split_lines`（R2）: CRLF / LF の双方を受容し、行内容と改行種別を返す。"""

    def test_crlf_text(self):
        # Arrange
        text = "a\r\nb\r\n"
        # Act
        lines, newline, trailing = ini_codec.split_lines(text)
        # Assert
        assert lines == ("a", "b")
        assert newline == CRLF
        assert trailing is True

    def test_lf_text(self):
        lines, newline, trailing = ini_codec.split_lines("a\nb\n")
        assert lines == ("a", "b")
        assert newline == LF
        assert trailing is True

    def test_missing_trailing_newline_is_reported(self):
        lines, _, trailing = ini_codec.split_lines("a\r\nb")
        assert lines == ("a", "b")
        assert trailing is False

    def test_carriage_return_is_removed_from_line_content(self):
        lines, _, _ = ini_codec.split_lines("Visual=1\r\n")
        assert lines == ("Visual=1",)

    def test_blank_lines_are_preserved(self):
        lines, _, _ = ini_codec.split_lines("a\r\n\r\nb\r\n")
        assert lines == ("a", "", "b")


class TestParseLineKinds:
    """`parse`（R3〜R5）: 行種別 4 種の付与とセクション帰属。"""

    def test_all_four_line_kinds_are_recognised(self):
        # Arrange
        lines = synthetic_ini_lines(blank_lines=True, inputs=SCALAR_INPUT_LINES)
        # Act
        doc = _parse(lines)
        # Assert
        kinds = {line.kind for line in doc.lines}
        assert kinds == {
            IniLineKind.COMMENT,
            IniLineKind.BLANK,
            IniLineKind.SECTION,
            IniLineKind.ENTRY,
        }

    def test_line_text_is_preserved_verbatim(self):
        lines = synthetic_ini_lines(inputs=RANGE5_INPUT_LINES)
        doc = _parse(lines)
        assert tuple(line.text for line in doc.lines) == lines

    def test_lineno_is_one_based_and_contiguous(self):
        doc = _parse(synthetic_ini_lines())
        assert [line.lineno for line in doc.lines] == list(range(1, len(doc.lines) + 1))

    def test_entries_are_attributed_to_their_section(self):
        # Arrange / Act
        doc = _parse(synthetic_ini_lines(inputs=SCALAR_INPUT_LINES))
        # Assert
        assert doc.entry(TESTER_SECTION, "Symbol") == "JP225"
        assert doc.entry(TESTER_INPUTS_SECTION, "inpSymbol") == ""

    def test_key_order_matches_the_standard_order(self):
        doc = _parse(synthetic_ini_lines())
        assert doc.key_order(TESTER_SECTION)[:5] == (
            "Expert",
            "Symbol",
            "Period",
            "Optimization",
            "Model",
        )

    def test_header_comment_is_available_from_the_document(self):
        doc = _parse(synthetic_ini_lines(header_comment=";Indicator visual test: X"))
        assert doc.header_comment() == ";Indicator visual test: X"

    def test_comment_lines_are_not_parsed_as_entries(self):
        doc = _parse(synthetic_ini_lines(header_comment=";Model=9"))
        assert doc.entry(TESTER_SECTION, "Model") == "1"

    def test_value_may_contain_further_equal_signs(self):
        # R5: 最初の `=` 1 個で分割する
        doc = _parse(synthetic_ini_lines(inputs=("expr=a=b",)))
        assert doc.entry(TESTER_INPUTS_SECTION, "expr") == "a=b"

    def test_empty_value_is_accepted(self):
        # F-14: `inpSymbol=`
        doc = _parse(synthetic_ini_lines(inputs=("inpSymbol=",)))
        assert doc.entry(TESTER_INPUTS_SECTION, "inpSymbol") == ""

    def test_five_field_input_is_kept_as_a_single_raw_token(self):
        # R7 / §4.4.2: `||` 分解は検証層の責務であり parse は生トークンを保持する
        doc = _parse(synthetic_ini_lines(inputs=("MAPeriod=3||2||1||22||Y",)))
        assert doc.entry(TESTER_INPUTS_SECTION, "MAPeriod") == "3||2||1||22||Y"

    def test_empty_tester_inputs_section_is_accepted(self):
        doc = _parse(synthetic_ini_lines(inputs=()))
        assert doc.entries(TESTER_INPUTS_SECTION) == ()


class TestParseDocumentMetadata:
    """`parse` が返す `IniDocument` のメタ情報。"""

    def test_encoding_is_taken_from_the_argument(self):
        assert _parse(synthetic_ini_lines()).encoding == UTF16LE

    def test_newline_is_detected(self):
        assert _parse(synthetic_ini_lines()).newline == CRLF

    def test_has_bom_is_true_because_r1_requires_it(self):
        assert _parse(synthetic_ini_lines()).has_bom is True

    def test_trailing_newline_is_detected(self):
        # Arrange: 末尾改行なしの本文
        text = CRLF.join(synthetic_ini_lines())
        # Act
        doc = ini_codec.parse(text, encoding=UTF16LE)
        # Assert
        assert doc.trailing_newline is False


class TestParseErrors:
    """E-01（`IniFormatError`）の異常系。すべて Fail-Stop（沈黙受容しない）。"""

    def _assert_e01(self, lines, rule_id=None):
        with pytest.raises(IniFormatError) as excinfo:
            _parse(lines)
        assert excinfo.value.context["error_id"] == "E-01"
        if rule_id is not None:
            assert excinfo.value.context["rule_id"] == rule_id
        return excinfo.value

    def test_unknown_section_is_rejected(self):
        # R4: 許容セクションは 2 種のみ
        self._assert_e01((TESTER_SECTION, "Symbol=JP225", "[Report]", TESTER_INPUTS_SECTION), "R4")

    def test_section_order_violation_is_rejected(self):
        # R4: [Tester] → [TesterInputs] の順に固定
        self._assert_e01((TESTER_INPUTS_SECTION, TESTER_SECTION, "Symbol=JP225"), "R4")

    def test_missing_tester_inputs_section_is_rejected(self):
        # R4: 本文が空でも省略しない
        self._assert_e01((TESTER_SECTION, "Symbol=JP225"), "R4")

    def test_missing_tester_section_is_rejected(self):
        self._assert_e01((TESTER_INPUTS_SECTION,), "R4")

    def test_duplicated_section_is_rejected(self):
        self._assert_e01(
            (TESTER_SECTION, "Symbol=JP225", TESTER_SECTION, TESTER_INPUTS_SECTION), "R4"
        )

    def test_non_empty_line_without_equal_sign_is_rejected(self):
        # R5: `=` を含まない非空行はエントリでない
        self._assert_e01((TESTER_SECTION, "Symbol", TESTER_INPUTS_SECTION), "R5")

    def test_unterminated_section_header_is_rejected(self):
        self._assert_e01(("[Tester", "Symbol=JP225", TESTER_INPUTS_SECTION))

    @pytest.mark.parametrize(
        ("value", "field_count"),
        [
            ("3||2", 2),
            ("3||2||1", 3),
            ("3||2||1||22", 4),
            ("3||2||1||22||Y||Z", 6),
        ],
    )
    def test_input_with_an_invalid_field_count_is_rejected(self, value, field_count):
        # R8: フィールド数は 1 または 5 のみ
        assert value.count("||") + 1 == field_count
        self._assert_e01(synthetic_ini_lines(inputs=(f"MAPeriod={value}",)), "R8")

    @pytest.mark.parametrize("flag", ["y", "n", "Yes", "1", "", "T"])
    def test_input_with_a_non_yn_flag_is_rejected(self, flag):
        # R8: 5 件目のフラグは `Y` / `N` のみ
        self._assert_e01(synthetic_ini_lines(inputs=(f"MAPeriod=3||2||1||22||{flag}",)), "R8")

    @pytest.mark.parametrize("flag", ["Y", "N"])
    def test_input_with_a_valid_yn_flag_is_accepted(self, flag):
        doc = _parse(synthetic_ini_lines(inputs=(f"MAPeriod=3||2||1||22||{flag}",)))
        assert doc.entry(TESTER_INPUTS_SECTION, "MAPeriod").endswith(flag)

    def test_duplicated_key_in_the_tester_section_is_rejected(self):
        # 内部設計 §4.1.2 補足 2: 後勝ちの沈黙上書きを禁ずる
        error = self._assert_e01(
            (TESTER_SECTION, "Symbol=JP225", "Symbol=JP225c", TESTER_INPUTS_SECTION), "R5"
        )
        assert error.context["key"] == "Symbol"

    def test_line_longer_than_the_limit_is_rejected(self):
        # Arrange: 上限 +1 文字
        long_line = "X=" + "a" * (ini_codec.MAX_LINE_CHARS - 1)
        assert len(long_line) == ini_codec.MAX_LINE_CHARS + 1
        # Act / Assert: 行長上限は §7.3 の資源制約であり設計文書が rule_id を定めていない。
        # 実装値をそのまま期待値に引く（characterization）ことを避け error_id のみ固定する。
        self._assert_e01(synthetic_ini_lines(inputs=(long_line,)))

    def test_line_at_the_limit_is_accepted(self):
        # Arrange: 境界値ちょうど（上限は超過ではない）
        line = "X=" + "a" * (ini_codec.MAX_LINE_CHARS - 2)
        assert len(line) == ini_codec.MAX_LINE_CHARS
        # Act / Assert
        assert _parse(synthetic_ini_lines(inputs=(line,))) is not None

    def test_error_context_carries_lineno_and_truncated_line(self):
        with pytest.raises(IniFormatError) as excinfo:
            _parse((TESTER_SECTION, "Symbol", TESTER_INPUTS_SECTION))
        context = excinfo.value.context
        assert context["lineno"] == 2
        assert context["line"] == "Symbol"


class TestSerialize:
    """`serialize`（R1・R2・R9）: BOM + 行原文 + 改行。"""

    def test_output_starts_with_the_utf16le_bom(self):
        doc = _parse(synthetic_ini_lines())
        assert ini_codec.serialize(doc).startswith(b"\xff\xfe")

    def test_round_trip_of_a_parsed_document_is_byte_identical(self):
        # Arrange
        payload = encode_ini(synthetic_ini_lines(inputs=RANGE5_INPUT_LINES))
        text, encoding = ini_codec.decode(payload)
        # Act
        doc = ini_codec.parse(text, encoding=encoding)
        # Assert
        assert ini_codec.serialize(doc) == payload

    def test_document_without_trailing_newline_is_restored_without_it(self):
        # Arrange
        payload = encode_ini(synthetic_ini_lines(), trailing_newline=False)
        text, encoding = ini_codec.decode(payload)
        # Act / Assert
        assert ini_codec.serialize(ini_codec.parse(text, encoding=encoding)) == payload

    def test_lf_document_is_restored_with_lf(self):
        payload = encode_ini(synthetic_ini_lines(), newline=LF)
        text, encoding = ini_codec.decode(payload)
        assert ini_codec.serialize(ini_codec.parse(text, encoding=encoding)) == payload

    def test_numeric_tokens_are_not_reformatted(self):
        # R7: `Deposit=139500` を `139500.0` へ整形しない
        payload = encode_ini(synthetic_ini_lines(Deposit="139500"))
        text, encoding = ini_codec.decode(payload)
        assert b"139500.0" not in ini_codec.serialize(ini_codec.parse(text, encoding=encoding))


class TestReadWriteDocument:
    """`read_document` / `write_document`（API-01・API-02 の I/O 部）。"""

    def test_read_document_composes_read_decode_parse(self, tmp_path):
        # Arrange
        path = write_ini(tmp_path / "a.ini", synthetic_ini_lines(inputs=SCALAR_INPUT_LINES))
        # Act
        doc = ini_codec.read_document(path)
        # Assert
        assert doc.has_bom is True
        assert doc.encoding == UTF16LE
        assert doc.newline == CRLF
        assert doc.trailing_newline is True
        assert doc.entry(TESTER_SECTION, "Symbol") == "JP225"

    def test_read_document_round_trip_is_byte_identical(self, tmp_path):
        path = write_ini(tmp_path / "a.ini", synthetic_ini_lines(inputs=RANGE5_INPUT_LINES))
        assert ini_codec.serialize(ini_codec.read_document(path)) == path.read_bytes()

    def test_read_document_is_deterministic(self, tmp_path):
        path = write_ini(tmp_path / "a.ini", synthetic_ini_lines())
        assert ini_codec.read_document(path) == ini_codec.read_document(path)

    def test_write_document_creates_the_file(self, tmp_path):
        # Arrange
        source = write_ini(tmp_path / "a.ini", synthetic_ini_lines())
        doc = ini_codec.read_document(source)
        target = tmp_path / "b.ini"
        # Act
        ini_codec.write_document(doc, target)
        # Assert
        assert target.read_bytes() == source.read_bytes()

    def test_write_document_refuses_to_overwrite(self, tmp_path):
        # K-15 / 内部設計 §4.5.3
        source = write_ini(tmp_path / "a.ini", synthetic_ini_lines())
        doc = ini_codec.read_document(source)
        with pytest.raises(FileExistsError):
            ini_codec.write_document(doc, source)


def _expert_settings(**overrides) -> SettingsDto:
    values = dict(
        subject_kind=SubjectKind.EXPERT,
        subject_path="TC24051903.ex5",
        symbol="JP225",
        timeframe=Timeframe.D1,
        tick_model=TickModel.ONE_MINUTE_OHLC,
        date_range=DateRange(kind=DateRangeKind.PRESET, preset=DatesPreset.ENTIRE_HISTORY),
        forward_mode=ForwardMode.DISABLED,
        deposit=139500.0,
        currency="JPY",
        profit_in_pips=True,
        leverage=10,
        execution_delay=50,
        optimization=OptimizationMode.DISABLED,
        optimization_criterion=OptimizationCriterion.CRITERION_0,
        visual=True,
    )
    values.update(overrides)
    return SettingsDto(**values)


class TestBuildDocument:
    """`build_document`（R6 標準キー順・R3 コメント非生成）。"""

    def test_expert_key_order_matches_the_standard_order(self):
        # Arrange / Act
        doc = ini_codec.build_document(_expert_settings())
        # Assert: 基本設計 §4.4 標準キー順（Expert テスト）
        assert doc.key_order(TESTER_SECTION) == (
            "Expert",
            "Symbol",
            "Period",
            "Optimization",
            "Model",
            "Dates",
            "ForwardMode",
            "Deposit",
            "Currency",
            "ProfitInPips",
            "Leverage",
            "ExecutionMode",
            "OptimizationCriterion",
            "Visual",
        )

    def test_custom_date_range_emits_from_and_to_instead_of_dates(self):
        doc = ini_codec.build_document(
            _expert_settings(
                date_range=DateRange(
                    kind=DateRangeKind.CUSTOM,
                    from_date=date(2020, 3, 30),
                    to_date=date(2024, 5, 18),
                )
            )
        )
        keys = doc.key_order(TESTER_SECTION)
        assert "Dates" not in keys
        assert keys[5:7] == ("FromDate", "ToDate")
        assert doc.entry(TESTER_SECTION, "FromDate") == "2020.03.30"
        assert doc.entry(TESTER_SECTION, "ToDate") == "2024.05.18"

    def test_forward_date_is_emitted_only_for_custom_date_mode(self):
        doc = ini_codec.build_document(
            _expert_settings(forward_mode=ForwardMode.CUSTOM_DATE, forward_date=date(2023, 5, 22))
        )
        keys = doc.key_order(TESTER_SECTION)
        assert keys.index("ForwardDate") == keys.index("ForwardMode") + 1
        assert doc.entry(TESTER_SECTION, "ForwardDate") == "2023.05.22"

    def test_forward_date_key_is_absent_when_mode_is_disabled(self):
        doc = ini_codec.build_document(_expert_settings())
        assert "ForwardDate" not in doc.key_order(TESTER_SECTION)

    def test_visual_key_is_absent_when_visual_is_none(self):
        # 基本設計 §4.2 #16: `None` はキー欠落を表す（キーを発明しない）
        doc = ini_codec.build_document(_expert_settings(visual=None))
        assert "Visual" not in doc.key_order(TESTER_SECTION)

    def test_indicator_emits_only_six_keys(self):
        # F-12: Indicator テストは Expert 専用 8 キーを持たない
        doc = ini_codec.build_document(
            SettingsDto(
                subject_kind=SubjectKind.INDICATOR,
                subject_path="PRO!fit_Band.ex5",
                symbol="JP225",
                timeframe=Timeframe.H8,
                tick_model=TickModel.REAL_TICKS,
                date_range=DateRange(
                    kind=DateRangeKind.PRESET, preset=DatesPreset.ENTIRE_HISTORY
                ),
                visual=True,
            )
        )
        assert doc.key_order(TESTER_SECTION) == (
            "Indicator",
            "Symbol",
            "Period",
            "Model",
            "Dates",
            "Visual",
        )

    def test_boolean_fields_are_written_as_zero_or_one(self):
        # R11
        doc = ini_codec.build_document(_expert_settings(profit_in_pips=False, visual=False))
        assert doc.entry(TESTER_SECTION, "ProfitInPips") == "0"
        assert doc.entry(TESTER_SECTION, "Visual") == "0"

    def test_timeframe_is_written_as_its_ini_label(self):
        # D-03: `Daily` / `H1` / `H8` は corpus 実測ラベル
        doc = ini_codec.build_document(_expert_settings(timeframe=Timeframe.H8))
        assert doc.entry(TESTER_SECTION, "Period") == "H8"

    def test_no_comment_line_is_generated(self):
        # R3 / 内部設計 §4.1.2 補足 3: MT5 生成情報を偽造しない
        doc = ini_codec.build_document(
            _expert_settings(header_comment=";Expert Advisor visual test: X")
        )
        assert all(line.kind is not IniLineKind.COMMENT for line in doc.lines)
        assert doc.header_comment() is None

    def test_tester_inputs_section_is_emitted_even_when_empty(self):
        # R4: 空でも省略しない
        doc = ini_codec.build_document(_expert_settings())
        assert any(
            line.kind is IniLineKind.SECTION and line.text == TESTER_INPUTS_SECTION
            for line in doc.lines
        )

    def test_inputs_are_emitted_from_their_raw_text(self):
        doc = ini_codec.build_document(
            _expert_settings(
                inputs=(
                    InputDto(
                        name="MAPeriod",
                        form=InputForm.RANGE_5,
                        current="3",
                        raw="MAPeriod=3||2||1||22||Y",
                        start="2",
                        step="1",
                        stop="22",
                        optimize=True,
                    ),
                )
            )
        )
        assert doc.entry(TESTER_INPUTS_SECTION, "MAPeriod") == "3||2||1||22||Y"

    def test_generated_document_is_serialisable_and_reparsable(self):
        # Arrange
        doc = ini_codec.build_document(_expert_settings())
        # Act: 生成 → バイト列 → 再解析
        payload = ini_codec.serialize(doc)
        text, encoding = ini_codec.decode(payload)
        reparsed = ini_codec.parse(text, encoding=encoding)
        # Assert
        assert ini_codec.serialize(reparsed) == payload

    def test_generated_document_uses_crlf_and_bom(self):
        doc = ini_codec.build_document(_expert_settings())
        assert doc.newline == CRLF
        assert doc.has_bom is True
        assert doc.trailing_newline is True


class TestDocumentFromEntries:
    """`document_from_entries`（生トークン対 → ``IniDocument``）。

    受理集合（検証層が読める値）と出力集合（字句層が書ける値）を一致させる低位関数。
    `build_document` は「型付き値 → 生トークン整形 → 本関数へ委譲」と定義され、
    整形規則の複製を作らない（是正 1・レビュー指摘 🟡-1）。
    """

    def test_raw_token_pairs_become_tester_entries_in_the_given_order(self):
        # Arrange: `[Tester]` の生トークン対（標準キー順の部分集合）
        entries = {"Indicator": "PRO!fit_Band.ex5", "Symbol": "JP225", "Period": "H8", "Model": "4"}
        # Act
        doc = ini_codec.document_from_entries(entries, ())
        # Assert
        assert doc.entries(TESTER_SECTION) == tuple(entries.items())
        assert doc.key_order(TESTER_SECTION) == tuple(entries)

    def test_input_lines_are_kept_verbatim_in_the_inputs_section(self):
        # Arrange / Act
        doc = ini_codec.document_from_entries(
            {"Indicator": "PRO!fit_Band.ex5", "Symbol": "JP225", "Period": "H8", "Model": "4"},
            RANGE5_INPUT_LINES,
        )
        # Assert: `||` 連結を組み立て直さず行原文を用いる（R7）
        assert doc.entry(TESTER_INPUTS_SECTION, "MAPeriod") == "3||2||1||22||Y"
        assert doc.key_order(TESTER_INPUTS_SECTION) == ("LotSize", "MAPeriod", "MAMethod")

    def test_a_non_integer_deposit_token_is_kept_verbatim(self):
        # 是正 1: 検証層が受理する `139500.50` を字句層も出力できる（写像不能を消す）
        doc = ini_codec.document_from_entries({"Deposit": "139500.50"}, ())
        assert doc.entry(TESTER_SECTION, "Deposit") == "139500.50"

    def test_the_generated_document_is_utf16le_crlf_with_bom(self):
        # 新規生成は LE 固定（R1 の趣旨。読込元がある文書の書出しとは別経路）
        doc = ini_codec.document_from_entries({"Symbol": "JP225"}, ())
        assert doc.encoding == UTF16LE
        assert doc.newline == CRLF
        assert doc.has_bom is True
        assert doc.trailing_newline is True

    def test_build_document_produces_the_same_bytes_as_the_low_level_function(self):
        # Arrange: 型付き値からの生成物と、その生トークンを本関数へ渡した生成物
        settings = _expert_settings()
        built = ini_codec.build_document(settings)
        # Act
        delegated = ini_codec.document_from_entries(
            dict(built.entries(TESTER_SECTION)), tuple(item.raw for item in settings.inputs)
        )
        # Assert: 整形規則が 1 箇所であることの外形（生成物が同一バイト列）
        assert ini_codec.serialize(delegated) == ini_codec.serialize(built)


class TestSerializeHonoursTheDocumentEncoding:
    """是正 2（レビュー指摘 🟡-2）: 読込元の符号化を沈黙で変えない。

    `decode` は BOM で LE / BE を判定して BE を正規受理する（R1）。書出しを LE 固定に
    すると、BE 入力で規則 R9（読込元があるならバイト一致）が例外も警告もなく破れる。
    読み込んだ文書は ``doc.encoding`` で書き出し、新規生成のみ LE 固定とする。
    """

    def test_a_big_endian_document_round_trips_byte_identically(self):
        # Arrange
        payload = encode_ini(synthetic_ini_lines(inputs=RANGE5_INPUT_LINES), encoding=UTF16BE)
        text, encoding = ini_codec.decode(payload)
        # Act
        restored = ini_codec.serialize(ini_codec.parse(text, encoding=encoding))
        # Assert: R9
        assert restored == payload

    def test_a_big_endian_document_keeps_the_big_endian_bom(self):
        payload = encode_ini(synthetic_ini_lines(), encoding=UTF16BE)
        text, encoding = ini_codec.decode(payload)
        assert ini_codec.serialize(ini_codec.parse(text, encoding=encoding))[:2] == b"\xfe\xff"

    def test_a_big_endian_file_round_trips_through_read_document(self, tmp_path):
        path = write_ini(tmp_path / "be.ini", synthetic_ini_lines(), encoding=UTF16BE)
        doc = ini_codec.read_document(path)
        assert doc.encoding == UTF16BE
        assert ini_codec.serialize(doc) == path.read_bytes()

    def test_a_little_endian_document_still_keeps_the_little_endian_bom(self):
        payload = encode_ini(synthetic_ini_lines())
        text, encoding = ini_codec.decode(payload)
        assert ini_codec.serialize(ini_codec.parse(text, encoding=encoding))[:2] == b"\xff\xfe"

    def test_a_newly_generated_document_is_little_endian(self):
        # 新規生成（読込元なし）は LE 固定（R1 の趣旨を維持する）
        doc = ini_codec.document_from_entries({"Symbol": "JP225"}, ())
        assert doc.encoding == UTF16LE
        assert ini_codec.serialize(doc)[:2] == b"\xff\xfe"

    def test_a_document_built_from_settings_is_little_endian(self):
        doc = ini_codec.build_document(_expert_settings())
        assert doc.encoding == UTF16LE
        assert ini_codec.serialize(doc)[:2] == b"\xff\xfe"


class TestNewGenerationFailStopIsPreserved:
    """是正 1 の副条件: `_format_deposit` の Fail-Stop は**新規生成経路にのみ**残る。

    自己レビュー（pre-mortem）で確認した不変条件。是正 1 は「読み込んだ / 生トークンを
    受け取った値」の写像不能を消すものであり、実測にない表記を**発明する**ことは許さない。
    `source` を持たない（検証層を通っていない）設定の整形だけが Fail-Stop する。
    """

    def test_build_document_still_fails_stop_on_a_non_integer_deposit(self):
        # R7: corpus 44 件に非整数 `Deposit` の表記実測がないため出力表記を発明しない
        with pytest.raises(SettingsValueError) as excinfo:
            ini_codec.build_document(_expert_settings(deposit=139500.5))
        assert excinfo.value.context["key"] == "Deposit"
        assert excinfo.value.context["rule_id"] == "R7"

    def test_build_document_accepts_an_integer_valued_deposit(self):
        doc = ini_codec.build_document(_expert_settings(deposit=139500.0))
        assert doc.entry(TESTER_SECTION, "Deposit") == "139500"

    def test_the_low_level_function_never_invents_a_notation(self):
        # 生トークン経路は整形器を通さないため Fail-Stop の対象にならない
        doc = ini_codec.document_from_entries({"Deposit": "139500.50"}, ())
        assert doc.entry(TESTER_SECTION, "Deposit") == "139500.50"


class TestValuesThatCannotBeWrittenFailStopAtConstruction:
    """自己レビュー（pre-mortem）で確認: `.ini` に表現できない値の検出点。

    改行を含む値は行構造を壊すため `.ini` に書けない。是正 1 の前は API-03 が受理し
    API-04 / `dump` で E-01 になっていた（＝写像不能値）。是正 1 により文書構築が
    API-03 で行われるため、同じ E-01 が**構築時**に出る。受理集合が狭まったのでは
    なく、写像不能値の検出が前倒しになった（API-04 は API-03 の像に対して送出例外なし
    のまま。``source`` を持たない直接構築物は射程外＝是正 A）。
    """

    def test_the_offending_input_is_named_in_the_diagnostic(self):
        # 是正 C: どの対が原因かを診断が指す（詳細は
        # `TestSuppliedTokensAreDiagnosedAsTheCallersOwnInput`）
        with pytest.raises(IniFormatError) as excinfo:
            ini_codec.document_from_entries({"Symbol": "JP\r\n225"}, ())
        assert excinfo.value.context["key"] == "Symbol"

    def test_a_value_containing_a_newline_cannot_be_written(self):
        with pytest.raises(IniFormatError):
            ini_codec.document_from_entries({"Symbol": "JP\r\n225"}, ())

    def test_a_value_containing_a_newline_is_also_rejected_by_the_formatter(self):
        with pytest.raises(IniFormatError):
            ini_codec.build_document(_expert_settings(symbol="JP\r\n225"))


class TestSuppliedTokensAreDiagnosedAsTheCallersOwnInput:
    """是正 C: 診断は**呼出側が供給したもの**を指す（E-01 / R5・`key` / `value` 付き）。

    ``document_from_entries`` の呼出側は「対（キー・値）」と「行原文」だけを供給し、
    「行番号」も「改行」も供給しない。にもかかわらず、改行を含む生トークンは行構造を
    壊したうえで ``parse`` へ流れ、行数に基づく R2 診断（CRLF/LF 混在）になっていた。
    実測（是正前）:
        `{"Symbol": "A\\nB"}`      → R2「改行が CRLF と LF で混在しています（CRLF 16 行 / 全 17 行）」
        `("a=1\\r\\nb=2",)`          → **例外なし**。1 行が 2 行へ黙って分割された

    後者は診断の質ではなく**沈黙で表現を変える経路**（1 巡目 🟡-2 と同型）であり、
    供給トークンが 1 行であることを事前に課すことで両方が消える。
    行数に基づく R2 診断は ``split_lines`` を通るファイル読込経路の専用診断に留める。
    """

    NEWLINES = (LF, "\r", CRLF)

    @pytest.mark.parametrize("newline", NEWLINES)
    def test_a_value_with_a_line_break_names_the_key_and_the_value(self, newline):
        # Arrange
        bad = f"JP{newline}225"
        # Act
        with pytest.raises(IniFormatError) as excinfo:
            ini_codec.document_from_entries({"Symbol": bad}, ())
        # Assert
        context = excinfo.value.context
        assert context["rule_id"] == "R5"
        assert context["key"] == "Symbol"
        assert context["value"] == bad

    @pytest.mark.parametrize("newline", NEWLINES)
    def test_a_key_with_a_line_break_names_the_key(self, newline):
        bad = f"Sym{newline}bol"
        with pytest.raises(IniFormatError) as excinfo:
            ini_codec.document_from_entries({bad: "JP225"}, ())
        context = excinfo.value.context
        assert context["rule_id"] == "R5"
        assert context["key"] == bad

    @pytest.mark.parametrize("newline", NEWLINES)
    def test_an_input_line_with_a_line_break_is_rejected_instead_of_being_split(self, newline):
        # 是正前は CRLF のとき**沈黙で 2 行**になっていた（1 行を供給したのに 2 行）
        bad = f"a=1{newline}b=2"
        with pytest.raises(IniFormatError) as excinfo:
            ini_codec.document_from_entries({"Symbol": "JP225"}, (bad,))
        context = excinfo.value.context
        assert context["rule_id"] == "R5"
        assert context["line"] == bad

    def test_the_line_count_diagnostic_is_not_used_for_supplied_tokens(self):
        with pytest.raises(IniFormatError) as excinfo:
            ini_codec.document_from_entries({"Symbol": f"A{LF}B"}, ())
        assert "混在" not in excinfo.value.context["reason"]

    def test_the_line_count_diagnostic_still_covers_the_file_read_path(self):
        # R2（CRLF/LF 混在）は `split_lines` を通る経路の診断として残る
        with pytest.raises(IniFormatError) as excinfo:
            ini_codec.split_lines(f"a{CRLF}b{LF}c{CRLF}")
        assert excinfo.value.context["rule_id"] == "R2"
        assert "混在" in excinfo.value.context["reason"]

    def test_well_formed_tokens_are_still_accepted(self):
        # 受理集合を狭めていない（改行を含まない値・行はそのまま通る）
        doc = ini_codec.document_from_entries(
            {"Symbol": "JP225", "Deposit": "139500.50"}, ("a=1||2||3||4||N",)
        )
        assert doc.entry(TESTER_SECTION, "Deposit") == "139500.50"
        assert doc.entry(TESTER_INPUTS_SECTION, "a") == "1||2||3||4||N"


class TestSerializeAcceptsOnlyTheLexicalEncodings:
    """是正 D: 受理集合と出力集合の一致を**符号化軸**でも成立させる。

    ``decode`` の像は BOM 由来の 2 値（``_BOM_ENCODINGS``）だけである一方、
    ``serialize`` は ``IniDocument.encoding`` の任意 ``str`` を受けていた。
    是正前の実測（``dataclasses.replace(doc, encoding=X)`` → ``serialize``）:

        "utf-8"                    → 書出し成功するが読み戻し不能（BOM が EF BB BF）
        "utf-16"                   → BOM 二重（fffefffe）
        "ascii" / "cp932" / "latin-1" → ``UnicodeEncodeError`` が ``SettingsError`` へ
                                      未翻訳のまま ``write_document`` / ``dump`` から漏れる
        "bogus-codec"              → ``LookupError`` が同様に漏れる

    最初の 2 件は 1 巡目 🟡-2（沈黙で表現を変える経路）と同型であり、後の 4 件は
    例外体系の外へ漏れる経路である。許容集合は ``_BOM_ENCODINGS`` から**導出**し、
    手書きの第 2 の集合を作らない。
    """

    #: 是正前に「成功するが読み戻せない」「例外が漏れる」のいずれかだった符号化。
    REJECTED_ENCODINGS = ("utf-8", "utf-16", "ascii", "cp932", "latin-1", "bogus-codec")

    @staticmethod
    def _document():
        return ini_codec.document_from_entries({"Symbol": "JP225"}, ())

    def test_the_allowed_set_is_derived_from_the_bom_table(self):
        # 手書きの第 2 の集合を作らない（BOM 表が唯一の宣言）
        assert ini_codec.WRITE_ENCODINGS == frozenset(
            encoding for _, encoding in ini_codec._BOM_ENCODINGS
        )

    def test_the_new_generation_encoding_is_inside_the_allowed_set(self):
        assert ini_codec.ENCODING_WRITE in ini_codec.WRITE_ENCODINGS

    @pytest.mark.parametrize("encoding", [UTF16LE, UTF16BE])
    def test_both_lexical_encodings_remain_writable(self, encoding):
        # `decode` の像はそのまま書き出せる（受理集合 == 出力集合）
        doc = replace(self._document(), encoding=encoding)
        assert ini_codec.serialize(doc).startswith(
            b"\xff\xfe" if encoding == UTF16LE else b"\xfe\xff"
        )

    @pytest.mark.parametrize("encoding", REJECTED_ENCODINGS)
    def test_serialize_rejects_encodings_outside_the_lexical_set(self, encoding):
        # Arrange
        doc = replace(self._document(), encoding=encoding)
        # Act
        with pytest.raises(IniFormatError) as excinfo:
            ini_codec.serialize(doc)
        # Assert: E-01 / R1（符号化の規則）で、原因の符号化名と許容集合を指す。
        # 診断語彙は既存の `value` / `allowed`（E-05 の未知キーと同じ用法）を使い、
        # 語彙表（内部設計 §4.5.2）へ新語を足さない。
        context = excinfo.value.context
        assert context["rule_id"] == "R1"
        assert context["value"] == encoding
        assert set(context["allowed"]) == ini_codec.WRITE_ENCODINGS

    @pytest.mark.parametrize("encoding", REJECTED_ENCODINGS)
    def test_write_document_does_not_leak_a_non_settings_error(self, encoding, tmp_path):
        doc = replace(self._document(), encoding=encoding)
        with pytest.raises(IniFormatError):
            ini_codec.write_document(doc, tmp_path / f"{encoding}.ini")

    @pytest.mark.parametrize("encoding", REJECTED_ENCODINGS)
    def test_a_rejected_encoding_leaves_no_file_behind(self, encoding, tmp_path):
        # 部分書出しを残さない（検査は書出しより前）
        target = tmp_path / f"{encoding}.ini"
        with pytest.raises(IniFormatError):
            ini_codec.write_document(replace(self._document(), encoding=encoding), target)
        assert not target.exists()
