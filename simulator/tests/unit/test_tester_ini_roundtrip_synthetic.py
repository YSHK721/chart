"""合成 `.ini` 12 件の往復バイト一致（T-01b・内部設計 §9.2 / §9.3）。

corpus（`sample/` 配下・Git 追跡外）に依存せず CI で常時実行する往復ガード。
`read_document` → `serialize` がバイト列一致することを固定し、NFR-02（往復バイト
一致）の継続的検証が corpus 不在で空洞化するのを防ぐ。

⚠️ 本モジュールは**実装より先に書いたテスト**である（フェーズ 3 = Red）。
`simulator.adapter.tester_settings.ini_codec` / `framework.tester_settings.loader`
は未実装のため、現時点では**収集エラー（ImportError）** になる。

合成データの生成器は `tester_settings_synthetic.py` の 1 箇所にのみ置く
（テスト間で生成ロジックを複製しない）。
"""
from __future__ import annotations

import pytest

from dataclasses import replace

from simulator.adapter.tester_settings import ini_codec
from simulator.domain.tester_settings_exceptions import IniFormatError
from simulator.framework.tester_settings.loader import (
    dump_tester_settings,
    load_tester_settings,
    tester_settings_from_mapping,
    tester_settings_to_mapping,
)
from simulator.tests.unit.tester_settings_synthetic import (
    CRLF,
    RANGE5_INPUT_LINES,
    SYNTHETIC_CASES,
    TESTER_INPUTS_SECTION,
    TESTER_SECTION,
    UTF16BE,
    UTF16LE,
    case_ids,
    encode_ini,
    synthetic_ini_lines,
    write_ini,
)

#: `pytest.mark.parametrize` へ渡す 12 ケース。
CASES = list(SYNTHETIC_CASES)
CASE_IDS = list(case_ids())


@pytest.fixture()
def ini_path(tmp_path, request):
    """パラメータ化されたケースをファイルへ書き出して返す。"""
    case = request.param
    return write_ini(tmp_path / f"{case.case_id}.ini", case.lines)


class TestSyntheticCorpusShape:
    """合成ケース自体が仕様どおりであることを先に固定する（生成器の自己検査）。"""

    def test_twelve_cases_are_defined(self):
        assert len(CASES) == 12

    def test_case_ids_are_unique(self):
        assert len(set(CASE_IDS)) == 12

    @pytest.mark.parametrize("case", CASES, ids=CASE_IDS)
    def test_each_case_has_both_sections_in_order(self, case):
        # R4: [Tester] → [TesterInputs] の順に各 1 回
        assert case.lines.count(TESTER_SECTION) == 1
        assert case.lines.count(TESTER_INPUTS_SECTION) == 1
        assert case.lines.index(TESTER_SECTION) < case.lines.index(TESTER_INPUTS_SECTION)

    @pytest.mark.parametrize("case", CASES, ids=CASE_IDS)
    def test_each_case_is_utf16le_with_bom_and_crlf(self, case):
        # R1・R2
        payload = encode_ini(case.lines)
        assert payload[:2] == b"\xff\xfe"
        assert payload.endswith(CRLF.encode(UTF16LE))

    def test_cases_cover_both_subject_kinds(self):
        joined = ["\n".join(case.lines) for case in CASES]
        assert any("Expert=" in text for text in joined)
        assert any("Indicator=" in text for text in joined)

    def test_cases_cover_all_three_forward_modes(self):
        joined = "\n".join("\n".join(case.lines) for case in CASES)
        for value in ("ForwardMode=0", "ForwardMode=3", "ForwardMode=4"):
            assert value in joined

    def test_cases_cover_both_date_range_forms(self):
        joined = "\n".join("\n".join(case.lines) for case in CASES)
        assert "Dates=0" in joined
        assert "FromDate=" in joined and "ToDate=" in joined

    def test_cases_cover_visual_presence_and_absence(self):
        with_visual = [case for case in CASES if any(l.startswith("Visual=") for l in case.lines)]
        without_visual = [
            case for case in CASES if not any(l.startswith("Visual=") for l in case.lines)
        ]
        assert with_visual and without_visual

    def test_cases_cover_empty_and_populated_inputs(self):
        empty = [
            case
            for case in CASES
            if case.lines[-1] == TESTER_INPUTS_SECTION
        ]
        five_field = [case for case in CASES if any("||" in line for line in case.lines)]
        assert empty and five_field


class TestRoundTripBytes:
    """R9: `read_document` → `serialize` がバイト列一致する。"""

    @pytest.mark.parametrize("ini_path", CASES, ids=CASE_IDS, indirect=True)
    def test_document_round_trip_is_byte_identical(self, ini_path):
        # Arrange
        original = ini_path.read_bytes()
        # Act
        restored = ini_codec.serialize(ini_codec.read_document(ini_path))
        # Assert
        assert restored == original

    @pytest.mark.parametrize("ini_path", CASES, ids=CASE_IDS, indirect=True)
    def test_settings_round_trip_via_dump_is_byte_identical(self, ini_path, tmp_path):
        # Arrange: API-01 → API-02 の経路（`source` を保持して復元する）
        settings = load_tester_settings(ini_path)
        target = tmp_path / "dumped.ini"
        # Act
        dump_tester_settings(settings, target)
        # Assert
        assert target.read_bytes() == ini_path.read_bytes()

    @pytest.mark.parametrize("ini_path", CASES, ids=CASE_IDS, indirect=True)
    def test_source_document_is_retained_on_load(self, ini_path):
        settings = load_tester_settings(ini_path)
        assert settings.source is not None
        assert settings.source.has_bom is True
        assert settings.source.newline == CRLF

    @pytest.mark.parametrize("ini_path", CASES, ids=CASE_IDS, indirect=True)
    def test_load_is_deterministic(self, ini_path):
        # T-07 の合成版（常時実行）
        assert load_tester_settings(ini_path) == load_tester_settings(ini_path)

    @pytest.mark.parametrize("ini_path", CASES, ids=CASE_IDS, indirect=True)
    def test_key_order_is_preserved(self, ini_path):
        # R6: 読込時のキー出現順を保持する
        settings = load_tester_settings(ini_path)
        expected = tuple(
            line.split("=", 1)[0]
            for line in ini_path.read_bytes().decode("utf-16").split(CRLF)
            if "=" in line and not line.startswith(";")
        )
        actual = settings.source.key_order(TESTER_SECTION) + settings.source.key_order(
            TESTER_INPUTS_SECTION
        )
        assert actual == expected

    @pytest.mark.parametrize("ini_path", CASES, ids=CASE_IDS, indirect=True)
    def test_header_comment_is_preserved(self, ini_path):
        settings = load_tester_settings(ini_path)
        assert settings.header_comment == ";synthetic tester settings"


class TestRoundTripVariants:
    """改行・末尾改行の変種でも往復が成立する（R2）。"""

    def test_round_trip_without_trailing_newline(self, tmp_path):
        # Arrange
        path = write_ini(tmp_path / "no_tail.ini", CASES[0].lines, trailing_newline=False)
        # Act / Assert
        assert ini_codec.serialize(ini_codec.read_document(path)) == path.read_bytes()

    def test_round_trip_with_lf_newlines(self, tmp_path):
        path = write_ini(tmp_path / "lf.ini", CASES[0].lines, newline="\n")
        assert ini_codec.serialize(ini_codec.read_document(path)) == path.read_bytes()

    def test_dump_refuses_to_overwrite_an_existing_file(self, tmp_path):
        # K-15
        path = write_ini(tmp_path / "a.ini", CASES[0].lines)
        settings = load_tester_settings(path)
        with pytest.raises(FileExistsError):
            dump_tester_settings(settings, path)


class TestNonIntegerDepositIsTotal:
    """是正 1（レビュー指摘 🟡-1）: 小数 `Deposit` が全 4 経路で成立する。

    `Deposit=139500.50` は検証層が受理する（`_strict_decimal`）。読める値は
    load / dump（バイト一致）/ API-04 / API-03↔04 恒等 のすべてで扱えなければ
    「読めるが写像できない」値が残る（受理集合 ⊄ 出力集合）。
    """

    DEPOSIT_TOKEN = "139500.50"

    @pytest.fixture()
    def decimal_ini(self, tmp_path):
        lines = synthetic_ini_lines("expert", Deposit=self.DEPOSIT_TOKEN, inputs=RANGE5_INPUT_LINES)
        return write_ini(tmp_path / "decimal_deposit.ini", lines)

    def test_load_accepts_the_decimal_deposit(self, decimal_ini):
        assert load_tester_settings(decimal_ini).deposit == 139500.5

    def test_dump_restores_the_file_byte_identically(self, decimal_ini, tmp_path):
        settings = load_tester_settings(decimal_ini)
        target = tmp_path / "dumped.ini"
        dump_tester_settings(settings, target)
        assert target.read_bytes() == decimal_ini.read_bytes()

    def test_to_mapping_returns_the_original_token(self, decimal_ini):
        settings = load_tester_settings(decimal_ini)
        assert tester_settings_to_mapping(settings)["Deposit"] == self.DEPOSIT_TOKEN

    def test_from_mapping_to_mapping_is_identity(self, decimal_ini):
        mapping = tester_settings_to_mapping(load_tester_settings(decimal_ini))
        assert tester_settings_to_mapping(tester_settings_from_mapping(mapping)) == mapping


class TestBigEndianRoundTrip:
    """是正 2（レビュー指摘 🟡-2）: BE 入力の load → dump がバイト一致する。"""

    @pytest.fixture()
    def big_endian_ini(self, tmp_path):
        return write_ini(
            tmp_path / "be.ini",
            synthetic_ini_lines("expert", inputs=RANGE5_INPUT_LINES),
            encoding=UTF16BE,
        )

    def test_the_input_file_starts_with_the_big_endian_bom(self, big_endian_ini):
        # Arrange の自己検査（BE で書けていることを先に固定する）
        assert big_endian_ini.read_bytes()[:2] == b"\xfe\xff"

    def test_dump_restores_the_big_endian_file_byte_identically(self, big_endian_ini, tmp_path):
        settings = load_tester_settings(big_endian_ini)
        target = tmp_path / "dumped.ini"
        dump_tester_settings(settings, target)
        assert target.read_bytes() == big_endian_ini.read_bytes()

    def test_the_dumped_file_keeps_the_big_endian_bom(self, big_endian_ini, tmp_path):
        target = tmp_path / "dumped.ini"
        dump_tester_settings(load_tester_settings(big_endian_ini), target)
        assert target.read_bytes()[:2] == b"\xfe\xff"

    def test_a_settings_built_from_a_mapping_is_dumped_as_little_endian(self, tmp_path):
        # 新規生成経路（読込元なし）は LE 固定
        settings = tester_settings_from_mapping(
            tester_settings_to_mapping(load_tester_settings(
                write_ini(tmp_path / "src.ini", synthetic_ini_lines("expert", inputs=()))
            ))
        )
        target = tmp_path / "new.ini"
        dump_tester_settings(settings, target)
        assert target.read_bytes()[:2] == b"\xff\xfe"


class TestDumpDoesNotLeakEncodingFailures:
    """是正 D の境界: 符号化の不正が facade からも `SettingsError` 体系で出る。

    是正前の実測（``dataclasses.replace(doc, encoding=X)`` を経た ``dump``）:
        "utf-8"       → 書出しに成功するが読み戻し不能（BOM が EF BB BF）
        "cp932"       → ``UnicodeEncodeError`` が未翻訳で ``dump`` から漏れる
        "bogus-codec" → ``LookupError`` が未翻訳で ``dump`` から漏れる
    許容集合は字句層の ``WRITE_ENCODINGS``（``_BOM_ENCODINGS`` から導出）である。
    """

    #: 是正前に「沈黙で壊れる」「例外が漏れる」のいずれかだった代表 3 種。
    REJECTED_ENCODINGS = ("utf-8", "cp932", "bogus-codec")

    @pytest.fixture()
    def loaded(self, tmp_path):
        return load_tester_settings(write_ini(tmp_path / "src.ini", synthetic_ini_lines("expert")))

    @pytest.mark.parametrize("encoding", REJECTED_ENCODINGS)
    def test_dump_reports_a_settings_error(self, encoding, loaded, tmp_path):
        # Arrange: 読込元の符号化だけを許容外へ差し替える
        broken = replace(loaded, source=replace(loaded.source, encoding=encoding))
        # Act / Assert
        with pytest.raises(IniFormatError) as excinfo:
            dump_tester_settings(broken, tmp_path / f"{encoding}.ini")
        assert excinfo.value.context["rule_id"] == "R1"

    @pytest.mark.parametrize("encoding", REJECTED_ENCODINGS)
    def test_dump_leaves_no_file_behind(self, encoding, loaded, tmp_path):
        target = tmp_path / f"{encoding}.ini"
        with pytest.raises(IniFormatError):
            dump_tester_settings(replace(loaded, source=replace(loaded.source, encoding=encoding)), target)
        assert not target.exists()

    def test_the_unmodified_document_still_round_trips(self, loaded, tmp_path):
        # 受理集合を狭めていない（読込元の符号化はそのまま書ける）
        target = tmp_path / "ok.ini"
        dump_tester_settings(loaded, target)
        assert target.read_bytes()[:2] == b"\xff\xfe"
