"""検証層（規則 B〜Q）の単体テスト（内部設計 §4.3・基本設計 §4.5.5）。

⚠️ 本モジュールは**実装より先に書いたテスト**である（フェーズ 3 = Red）。
`simulator.framework.tester_settings.loader` は未実装のため、現時点では
**収集エラー（ImportError）** になる。

固定する仕様:
    1. 規則 B〜Q の各違反が内部設計 §4.3.2 の割付表どおりの例外になること
       （E-01 / E-02 / E-03 / E-04 / E-05 / E-06 / E-08）。
       規則 B は ISSUE-391 の裁定により E-03（基本設計 §4.5.5 が上位）。
    2. `ValidationError` → `SettingsError` の翻訳が**優先順位表**で決まり、
       pydantic のフィールド定義順・マッピングの挿入順に依存しないこと（§4.3.3）。
    3. `context["validation_errors"]` に違反が全件載ること。
    4. 正常系（corpus 相当の合成データ）が `TesterSettings` になること。
"""
from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pytest

import simulator.domain.tester_settings_exceptions as exceptions_mod
import simulator.usecase.tester_settings as usecase_pkg
from simulator.adapter.tester_settings import ini_codec
from simulator.adapter.tester_settings.ini_codec import MAX_INPUT_LINES
from simulator.domain.tester_settings_exceptions import (
    IniFormatError,
    SettingsActivationError,
    SettingsKeyConflictError,
    SettingsKeyMissingError,
    SettingsValueError,
    UnknownSettingKeyError,
    UnknownSettingValueError,
)
from simulator.framework.tester_settings import loader, validation
from simulator.framework.tester_settings.loader import (
    tester_settings_from_mapping,
    tester_settings_to_mapping,
)
from simulator.tests.unit.tester_settings_synthetic import (
    EXPERT_ONLY_KEYS,
    OMIT,
    RANGE5_INPUT_LINES,
    SCALAR_INPUT_LINES,
    TESTER_SECTION,
    UTF16LE,
    expert_mapping,
    indicator_mapping,
    reordered,
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
from simulator.usecase.tester_settings.models import TesterSettings as SettingsDto


def settings_source_files() -> tuple[Path, ...]:
    """Settings 機能のソースファイル一覧（テストは含まない）。

    宣言（docstring の断定・値の表記規則）を機械的に検査するテストが共通で使う。
    列挙を各テストクラスに書き写すと、モジュールが増えたとき片方だけが古くなる。
    """
    packages = (
        Path(ini_codec.__file__).parent,
        Path(loader.__file__).parent,
        Path(usecase_pkg.__file__).parent,
    )
    files = [path for package in packages for path in sorted(package.glob("*.py"))]
    files.append(Path(exceptions_mod.__file__))
    return tuple(files)


def _raises(mapping, error_type, *, inputs=(), rule_id=None):
    """`tester_settings_from_mapping` が期待例外を送出することを検証する共通手続き。"""
    with pytest.raises(error_type) as excinfo:
        tester_settings_from_mapping(mapping, inputs=inputs)
    if rule_id is not None:
        assert excinfo.value.context["rule_id"] == rule_id
    return excinfo.value


class TestHappyPath:
    """正常系: corpus 相当の合成データが `TesterSettings` になる。"""

    def test_expert_mapping_builds_settings(self):
        # Arrange / Act
        settings = tester_settings_from_mapping(expert_mapping(), inputs=RANGE5_INPUT_LINES)
        # Assert
        assert isinstance(settings, SettingsDto)
        assert settings.subject_kind is SubjectKind.EXPERT
        assert settings.subject_path == "TC24051903.ex5"
        assert settings.symbol == "JP225"
        assert settings.timeframe is Timeframe.D1
        assert settings.tick_model is TickModel.ONE_MINUTE_OHLC
        assert settings.date_range.kind is DateRangeKind.PRESET
        assert settings.date_range.preset is DatesPreset.ENTIRE_HISTORY
        assert settings.forward_mode is ForwardMode.DISABLED
        assert settings.forward_date is None
        assert settings.deposit == 139500.0
        assert settings.currency == "JPY"
        assert settings.profit_in_pips is True
        assert settings.leverage == 10
        assert settings.execution_delay == 50
        assert settings.optimization is OptimizationMode.DISABLED
        assert settings.optimization_criterion is OptimizationCriterion.CRITERION_0
        assert settings.visual is True

    def test_from_mapping_stores_the_received_raw_tokens_as_source(self):
        # API-03 事後条件（是正 1 で改定）: 受け取った生トークンから `IniDocument` を
        # 構築して `source` に格納する。捨てると写像不能（受理集合 ⊄ 出力集合）が生じる。
        mapping = expert_mapping()
        settings = tester_settings_from_mapping(mapping, inputs=RANGE5_INPUT_LINES)
        assert settings.source is not None
        assert dict(settings.source.entries(TESTER_SECTION)) == mapping
        assert settings.source.key_order(TESTER_SECTION) == tuple(mapping)

    def test_from_mapping_source_is_a_newly_generated_little_endian_document(self):
        # 新規生成の符号化は LE 固定（R1 の趣旨）
        settings = tester_settings_from_mapping(expert_mapping())
        assert settings.source.encoding == UTF16LE
        assert settings.source.has_bom is True

    def test_the_effective_view_still_drops_the_raw_representation(self):
        # `RAW_REPRESENTATION_FIELDS` の不変条件は維持する（規則 A の遮断を破らない）
        settings = tester_settings_from_mapping(expert_mapping())
        assert settings.effective().source is None
        assert settings.effective().header_comment is None

    def test_indicator_mapping_builds_settings_without_expert_only_fields(self):
        # F-12
        settings = tester_settings_from_mapping(indicator_mapping(), inputs=SCALAR_INPUT_LINES)
        assert settings.subject_kind is SubjectKind.INDICATOR
        assert settings.tick_model is TickModel.REAL_TICKS
        assert settings.deposit is None
        assert settings.currency is None
        assert settings.leverage is None
        assert settings.optimization is None
        assert settings.forward_mode is None

    def test_custom_date_range_is_parsed(self):
        settings = tester_settings_from_mapping(
            expert_mapping(Dates=OMIT, FromDate="2020.03.30", ToDate="2024.05.18")
        )
        assert settings.date_range.kind is DateRangeKind.CUSTOM
        assert settings.date_range.from_date == date(2020, 3, 30)
        assert settings.date_range.to_date == date(2024, 5, 18)
        assert settings.date_range.preset is None

    def test_forward_custom_date_is_parsed(self):
        settings = tester_settings_from_mapping(
            expert_mapping(ForwardMode="4", ForwardDate="2023.05.22")
        )
        assert settings.forward_mode is ForwardMode.CUSTOM_DATE
        assert settings.forward_date == date(2023, 5, 22)

    def test_epoch_forward_date_is_accepted_as_a_degenerate_value(self):
        # F-17 / R10: `1970.01.01` は受容し意味付けしない
        settings = tester_settings_from_mapping(
            expert_mapping(ForwardMode="4", ForwardDate="1970.01.01")
        )
        assert settings.forward_date == date(1970, 1, 1)

    def test_visual_key_absent_yields_none(self):
        settings = tester_settings_from_mapping(expert_mapping(Visual=OMIT))
        assert settings.visual is None

    def test_scalar_input_with_empty_value(self):
        # F-14
        settings = tester_settings_from_mapping(indicator_mapping(), inputs=("inpSymbol=",))
        assert settings.inputs[0].name == "inpSymbol"
        assert settings.inputs[0].form is InputForm.SCALAR
        assert settings.inputs[0].current == ""
        assert settings.inputs[0].raw == "inpSymbol="

    def test_range5_input_is_decomposed(self):
        # F-13 / R8
        settings = tester_settings_from_mapping(
            expert_mapping(), inputs=("MAPeriod=3||2||1||22||Y",)
        )
        item = settings.inputs[0]
        assert item.form is InputForm.RANGE_5
        assert (item.name, item.current, item.start, item.step, item.stop) == (
            "MAPeriod",
            "3",
            "2",
            "1",
            "22",
        )
        assert item.optimize is True
        assert item.raw == "MAPeriod=3||2||1||22||Y"

    def test_range5_flag_n_maps_to_false(self):
        settings = tester_settings_from_mapping(
            expert_mapping(), inputs=("MAMethod=1||0||0||3||N",)
        )
        assert settings.inputs[0].optimize is False

    def test_input_values_are_kept_as_strings(self):
        # 基本設計 §4.2.2: 型推定を行わない
        settings = tester_settings_from_mapping(
            expert_mapping(), inputs=("CheckMarketHours=true||false||0||true||N",)
        )
        assert settings.inputs[0].current == "true"

    def test_to_mapping_returns_the_standard_key_order(self):
        # API-04 事後条件
        settings = tester_settings_from_mapping(expert_mapping())
        assert list(tester_settings_to_mapping(settings)) == list(expert_mapping())

    def test_from_mapping_and_to_mapping_are_inverse(self):
        mapping = expert_mapping()
        assert tester_settings_to_mapping(tester_settings_from_mapping(mapping)) == mapping

    def test_from_mapping_is_deterministic(self):
        assert tester_settings_from_mapping(expert_mapping()) == tester_settings_from_mapping(
            expert_mapping()
        )


class TestAcceptedValuesAreAlsoWritable:
    """是正 1（レビュー指摘 🟡-1）: 受理集合と出力集合が一致する。

    API-04 は内部設計 §6 で「``source`` を持つ設定では送出例外なし」と規定されている
    （無限定の全域性ではない＝是正 A。射程外は `TestApi04ExceptionScopeIsStatedAsMeasured`）。
    検証層が受理した値を字句層が写像できない状態（`Deposit=139500.50`）は契約違反。
    """

    #: `_strict_decimal` が受理する `Deposit` の生トークン（`^[+-]?[0-9]+(\.[0-9]+)?$`）。
    ACCEPTED_DEPOSITS = ("139500", "139500.5", "139500.50", "0.01", "1000000.000001")

    @pytest.mark.parametrize("deposit", ACCEPTED_DEPOSITS)
    def test_to_mapping_does_not_raise_for_any_accepted_deposit(self, deposit):
        # API-04 は API-03 の像（`source` あり）に対して送出例外なし
        settings = tester_settings_from_mapping(expert_mapping(Deposit=deposit))
        assert tester_settings_to_mapping(settings)["Deposit"] == deposit

    @pytest.mark.parametrize("deposit", ACCEPTED_DEPOSITS)
    def test_from_mapping_to_mapping_is_identity_for_any_accepted_deposit(self, deposit):
        mapping = expert_mapping(Deposit=deposit)
        assert tester_settings_to_mapping(tester_settings_from_mapping(mapping)) == mapping

    def test_to_mapping_returns_the_input_token_verbatim(self):
        # 無言の書換えをしない（`139500.50` を `139500` にも `139500.5` にもしない）
        settings = tester_settings_from_mapping(expert_mapping(Deposit="139500.50"))
        assert tester_settings_to_mapping(settings)["Deposit"] == "139500.50"

    def test_to_mapping_uses_the_standard_formatting_when_source_is_absent(self):
        # `source` を持たない（プログラムから直接構築した）設定は `build_document` 経由
        settings = SettingsDto(
            subject_kind=SubjectKind.INDICATOR,
            subject_path="PRO!fit_Band.ex5",
            symbol="JP225",
            timeframe=Timeframe.H8,
            tick_model=TickModel.REAL_TICKS,
        )
        assert settings.source is None
        assert tester_settings_to_mapping(settings) == {
            "Indicator": "PRO!fit_Band.ex5",
            "Symbol": "JP225",
            "Period": "H8",
            "Model": "4",
        }


class TestApi04ExceptionScopeIsStatedAsMeasured:
    """是正 A: 「API-04 は全域関数」という**無限定の断定**を実挙動へ合わせる。

    実測（本クラスの pin テストが固定する）: ``source`` を持たない ``TesterSettings``
    を直接構築し、非整数 `Deposit` を与えると API-04 は E-04（`rule_id="R7"`）を送出
    する。したがって「送出例外: なし（全域関数）」は誤りであり、限定（``source`` を
    持つ設定＝API-01 / API-03 の像に限る）を伴わなければならない。

    docstring を**機械的に**検査する理由: 呼出側が読む契約は docstring であり、
    宣言と実挙動の乖離は宣言側でしか検出できない（内部設計 §6 本文 L736 は既に
    限定済みで、乖離していたのは docstring だけであった）。順序表・キー集合が
    ``validation`` の import 時検査で守られているのと同じ扱いにする。
    """

    #: 実挙動と一致しない無限定の断定。限定を書けばこの語を使う必要はない。
    FORBIDDEN_TOTALITY_CLAIM: str = "全域関数"

    def test_no_settings_module_claims_unconditional_totality(self):
        # Arrange: 断定は宣言サイトを移動し得るため Settings 全ソースを見る
        # Act
        claimants = [
            path.name
            for path in settings_source_files()
            if self.FORBIDDEN_TOTALITY_CLAIM in path.read_text(encoding="utf-8")
        ]
        # Assert
        assert claimants == []

    def test_the_api04_docstring_states_the_condition_under_which_it_raises(self):
        # Arrange / Act
        doc = tester_settings_to_mapping.__doc__
        # Assert: 限定（source の有無）と送出例外（E-04）が契約として書かれている
        assert "送出例外: なし" not in doc
        assert "source" in doc
        assert "E-04" in doc

    def test_to_mapping_fail_stops_on_a_non_integer_deposit_without_source(self):
        # Arrange: 検証層を通さない直接構築物（`source` なし）
        settings = SettingsDto(
            subject_kind=SubjectKind.EXPERT,
            subject_path="TC24051903.ex5",
            symbol="JP225",
            timeframe=Timeframe.H8,
            tick_model=TickModel.REAL_TICKS,
            deposit=139500.5,
        )
        assert settings.source is None
        # Act
        with pytest.raises(SettingsValueError) as excinfo:
            tester_settings_to_mapping(settings)
        # Assert: E-04 / R7（新規生成経路の Fail-Stop）
        assert excinfo.value.context["error_id"] == "E-04"
        assert excinfo.value.context["rule_id"] == "R7"
        assert excinfo.value.context["key"] == "Deposit"

    def test_to_mapping_does_not_raise_for_the_same_deposit_when_source_exists(self):
        # 限定の反対側: API-03 の像（`source` あり）は同じ値でも送出しない
        settings = tester_settings_from_mapping(expert_mapping(Deposit="139500.5"))
        assert tester_settings_to_mapping(settings)["Deposit"] == "139500.5"


class TestApi03DiagnosesTheCallersOwnInput:
    """是正 C: API-03 の E-01 診断が**呼出側が渡した対**を指す。

    呼出側は ``Mapping`` を渡すだけで「行」も「改行」も供給しない。是正前の実測は
    `IniFormatError`（`rule_id="R2"`・「改行が CRLF と LF で混在しています
    （CRLF 16 行 / 全 17 行）」）で、``key`` も ``value`` も載らず 18 キーのどれが
    原因か特定できなかった。事前検査（字句層 ``_require_single_line_tokens``）に
    より、違反キーを名指しする E-01（`rule_id="R5"`）になる。
    """

    def test_a_newline_in_a_value_names_the_offending_key_and_value(self):
        # Arrange / Act
        with pytest.raises(IniFormatError) as excinfo:
            tester_settings_from_mapping(expert_mapping(Symbol="A\nB"))
        # Assert
        context = excinfo.value.context
        assert context["rule_id"] == "R5"
        assert context["key"] == "Symbol"
        assert context["value"] == "A\nB"
        assert "混在" not in context["reason"]

    def test_a_newline_in_an_input_line_is_rejected(self):
        with pytest.raises(IniFormatError) as excinfo:
            tester_settings_from_mapping(expert_mapping(), inputs=("a=1\r\nb=2",))
        assert excinfo.value.context["rule_id"] == "R5"


class TestValueNotationHasASingleDeclaration:
    """是正 B: 値の表記規則（R10 の日付書式）の宣言は字句層の**1 箇所**だけである。

    ``validation`` のモジュール docstring は「値の表記規則は字句層が唯一の宣言を持つ。
    本モジュールはそれを import して使い、書き直さない」と宣言している。宣言だけでは
    複製を防げない（実際に `f"{value.year:04d}.{value.month:02d}.{value.day:02d}"` が
    字句層と検証層の 2 箇所に完全一致で存在した）ため、宣言と同じ強度で**機械的に**
    検査する（順序表・キー集合が ``validation`` の import 時検査で守られているのと
    同じ扱い）。

    検査対象は Settings 機能の全ソース（adapter / framework / usecase / domain）で
    あり、複製が別モジュールへ移動しても検出できる。
    """

    #: 日付表記の字面そのもの（ゼロ埋め 4 桁年）。複製はこの字面の再出現として現れる。
    DATE_NOTATION_PROBE: str = "year:04d"

    def test_the_date_notation_is_declared_in_exactly_one_file(self):
        # Arrange
        files = settings_source_files()
        # Act
        holders = {
            path.name: path.read_text(encoding="utf-8").count(self.DATE_NOTATION_PROBE)
            for path in files
            if self.DATE_NOTATION_PROBE in path.read_text(encoding="utf-8")
        }
        # Assert: 字句層の 1 ファイルに 1 回だけ
        assert holders == {Path(ini_codec.__file__).name: 1}

    def test_the_validation_layer_uses_the_lexical_declaration_itself(self):
        # 同じ規則の再実装ではなく**同一オブジェクト**を使う（書き直さない）
        assert validation.format_date_token is ini_codec.format_date_token

    def test_the_shared_declaration_produces_the_r10_notation(self):
        # 表記規則そのものの固定（R10: ゼロ埋め 2 桁）
        assert ini_codec.format_date_token(date(2012, 1, 1)) == "2012.01.01"
        assert ini_codec.format_date_token(date(2024, 5, 19)) == "2024.05.19"

    def test_the_diagnostic_message_uses_the_shared_notation(self):
        # 規則 K の診断（`ToDate（YYYY.MM.DD）以下`）が同じ表記で出る
        error = _raises(
            expert_mapping(Dates=OMIT, FromDate="2012.12.31", ToDate="2012.01.01"),
            SettingsValueError,
            rule_id="K",
        )
        assert "2012.01.01" in error.context["expected"]


class TestRuleB:
    """規則 B: `optimization != DISABLED` のとき `Visual` キーは存在してはならない（F-11）。

    例外型は **E-03 `SettingsActivationError`**（基本設計 §4.5.5 の表・ISSUE-391 裁定）。
    規則 B は UI 活性依存に由来する制約でありキー衝突（E-02）ではない。
    """

    @pytest.mark.parametrize("optimization", ["1", "2"])
    def test_visual_with_optimization_enabled_is_rejected(self, optimization):
        # Arrange / Act / Assert
        error = _raises(
            expert_mapping(Optimization=optimization, Visual="1"),
            SettingsActivationError,
            rule_id="B",
        )
        # E-03 の REQUIRED_CONTEXT は {field, rule_id}
        assert error.context["field"] == "visual"

    @pytest.mark.parametrize("optimization", ["1", "2"])
    def test_visual_zero_with_optimization_enabled_is_also_rejected(self, optimization):
        # 値ではなく「キーが存在すること」自体が違反（F-11）
        _raises(
            expert_mapping(Optimization=optimization, Visual="0"),
            SettingsActivationError,
            rule_id="B",
        )

    def test_visual_with_optimization_disabled_is_accepted(self):
        assert tester_settings_from_mapping(expert_mapping(Optimization="0", Visual="1")).visual is True

    @pytest.mark.parametrize("optimization", ["1", "2"])
    def test_optimization_without_visual_is_accepted(self, optimization):
        settings = tester_settings_from_mapping(
            expert_mapping(Optimization=optimization, Visual=OMIT)
        )
        assert settings.visual is None


class TestRuleD:
    """規則 D: `Expert` と `Indicator` は排他かつ、いずれか必須（F-1）。"""

    def test_both_subject_keys_present_is_rejected(self):
        error = _raises(
            expert_mapping(Indicator="Band.ex5"), SettingsKeyConflictError, rule_id="D"
        )
        assert sorted(error.context["keys"]) == ["Expert", "Indicator"]

    def test_neither_subject_key_present_is_rejected(self):
        error = _raises(expert_mapping(Expert=OMIT), SettingsKeyMissingError, rule_id="D")
        assert sorted(error.context["keys"]) == ["Expert", "Indicator"]


class TestRuleE:
    """規則 E: `Dates` と `FromDate`/`ToDate` は排他かつ、いずれか必須（F-2）。"""

    def test_both_date_forms_present_is_rejected(self):
        error = _raises(
            expert_mapping(FromDate="2020.03.30", ToDate="2024.05.18"),
            SettingsKeyConflictError,
            rule_id="E",
        )
        assert "Dates" in error.context["keys"]

    def test_neither_date_form_present_is_rejected(self):
        _raises(expert_mapping(Dates=OMIT), SettingsKeyMissingError, rule_id="E")

    def test_from_date_without_to_date_is_rejected(self):
        _raises(
            expert_mapping(Dates=OMIT, FromDate="2020.03.30"), SettingsKeyMissingError, rule_id="E"
        )

    def test_to_date_without_from_date_is_rejected(self):
        _raises(
            expert_mapping(Dates=OMIT, ToDate="2024.05.18"), SettingsKeyMissingError, rule_id="E"
        )


class TestRuleF:
    """規則 F: `ForwardMode==4` ⇔ `ForwardDate` 併記（F-10）。"""

    def test_custom_date_mode_without_forward_date_is_rejected(self):
        error = _raises(expert_mapping(ForwardMode="4"), SettingsKeyMissingError, rule_id="F")
        assert "ForwardDate" in error.context["keys"]

    @pytest.mark.parametrize("forward_mode", ["0", "3"])
    def test_forward_date_without_custom_date_mode_is_rejected(self, forward_mode):
        error = _raises(
            expert_mapping(ForwardMode=forward_mode, ForwardDate="2023.05.22"),
            SettingsKeyConflictError,
            rule_id="F",
        )
        assert "ForwardDate" in error.context["keys"]


class TestRuleG:
    """規則 G: Indicator テストは Expert 専用 8 キーを持たない（F-12）。"""

    @pytest.mark.parametrize("key", EXPERT_ONLY_KEYS)
    def test_expert_only_key_on_an_indicator_test_is_rejected(self, key):
        # Arrange: 有効な値を与えても「存在すること自体」が違反
        value = {
            "Optimization": "0",
            "ForwardMode": "0",
            "Deposit": "10000",
            "Currency": "JPY",
            "ProfitInPips": "0",
            "Leverage": "10",
            "ExecutionMode": "0",
            "OptimizationCriterion": "0",
        }[key]
        # Act / Assert
        error = _raises(indicator_mapping(**{key: value}), SettingsKeyConflictError, rule_id="G")
        assert key in error.context["keys"]


class TestRuleH:
    """規則 H: Expert テストは Expert 専用 8 キーがすべて必須（F-12）。"""

    @pytest.mark.parametrize("key", EXPERT_ONLY_KEYS)
    def test_missing_expert_only_key_is_rejected(self, key):
        error = _raises(expert_mapping(**{key: OMIT}), SettingsKeyMissingError, rule_id="H")
        assert key in error.context["keys"]

    def test_all_missing_expert_only_keys_are_reported_together(self):
        overrides = {key: OMIT for key in EXPERT_ONLY_KEYS}
        error = _raises(expert_mapping(**overrides), SettingsKeyMissingError, rule_id="H")
        assert sorted(error.context["keys"]) == sorted(EXPERT_ONLY_KEYS)


class TestRuleIValueRanges:
    """規則 I / J / K / L / M / N: 値域・書式（すべて E-04）。"""

    @pytest.mark.parametrize("deposit", ["0", "-1", "-0.01"])
    def test_non_positive_deposit_is_rejected(self, deposit):
        _raises(expert_mapping(Deposit=deposit), SettingsValueError, rule_id="I")

    def test_deposit_above_the_upper_bound_is_rejected(self):
        _raises(expert_mapping(Deposit="1000000000001"), SettingsValueError, rule_id="I")

    @pytest.mark.parametrize("deposit", ["0.01", "10000", "1000000000000"])
    def test_deposit_within_range_is_accepted(self, deposit):
        # 境界値: 上限 1e12 ちょうどは受容
        assert tester_settings_from_mapping(expert_mapping(Deposit=deposit)).deposit == float(
            deposit
        )

    @pytest.mark.parametrize("leverage", ["0", "-1", "1001"])
    def test_leverage_outside_range_is_rejected(self, leverage):
        _raises(expert_mapping(Leverage=leverage), SettingsValueError, rule_id="J")

    @pytest.mark.parametrize("leverage", ["1", "100", "1000"])
    def test_leverage_boundaries_are_accepted(self, leverage):
        assert tester_settings_from_mapping(expert_mapping(Leverage=leverage)).leverage == int(
            leverage
        )

    def test_from_date_after_to_date_is_rejected(self):
        _raises(
            expert_mapping(Dates=OMIT, FromDate="2024.05.18", ToDate="2020.03.30"),
            SettingsValueError,
            rule_id="K",
        )

    def test_equal_from_and_to_dates_are_accepted(self):
        # 境界値: `from == to` は許容（`from <= to`）
        settings = tester_settings_from_mapping(
            expert_mapping(Dates=OMIT, FromDate="2024.05.18", ToDate="2024.05.18")
        )
        assert settings.date_range.from_date == settings.date_range.to_date

    @pytest.mark.parametrize("currency", ["jpy", "JP", "JPYY", "JP1", "", "日本円"])
    def test_invalid_currency_is_rejected(self, currency):
        _raises(expert_mapping(Currency=currency), SettingsValueError, rule_id="L")

    @pytest.mark.parametrize("symbol", ["", "X" * 32])
    def test_symbol_outside_length_range_is_rejected(self, symbol):
        _raises(expert_mapping(Symbol=symbol), SettingsValueError, rule_id="M")

    @pytest.mark.parametrize("symbol", ["X", "X" * 31, "JP225_ver24051601"])
    def test_symbol_length_boundaries_are_accepted(self, symbol):
        assert tester_settings_from_mapping(expert_mapping(Symbol=symbol)).symbol == symbol

    @pytest.mark.parametrize("path", ["ea.mq5", "ea", "ea.ex4", "", "x" * 252 + ".ex5"])
    def test_invalid_subject_path_is_rejected(self, path):
        _raises(expert_mapping(Expert=path), SettingsValueError, rule_id="N")

    @pytest.mark.parametrize(
        "path",
        ["a.ex5", "Examples\\Moving Average\\Moving Average.ex5", "x" * 251 + ".ex5"],
    )
    def test_valid_subject_path_is_accepted(self, path):
        # 境界値: 255 文字ちょうどは受容
        assert tester_settings_from_mapping(expert_mapping(Expert=path)).subject_path == path


class TestStrictScalarFormats:
    """書式バリデータ（§4.3.1）: pydantic の緩い強制を使わない（すべて E-04）。"""

    @pytest.mark.parametrize(
        ("key", "value"),
        [
            ("Model", "1.0"),
            ("Model", " 1"),
            ("Model", "0x1"),
            ("Model", "1_0"),
            ("Model", ""),
            ("Leverage", "10.0"),
            ("ExecutionMode", "5e1"),
            ("ProfitInPips", "true"),
            ("Visual", "yes"),
        ],
    )
    def test_non_strict_integer_tokens_are_rejected(self, key, value):
        _raises(expert_mapping(**{key: value}), SettingsValueError)

    @pytest.mark.parametrize("deposit", ["1e5", "inf", "nan", "1,000", "1.0.0", " 100"])
    def test_non_strict_decimal_tokens_are_rejected(self, deposit):
        _raises(expert_mapping(Deposit=deposit), SettingsValueError)

    @pytest.mark.parametrize(
        "value",
        ["2020.1.1", "2020-03-30", "20200330", "2020.13.01", "2020.02.30", "2020.03.30 00:00"],
    )
    def test_non_strict_date_tokens_are_rejected(self, value):
        # R10: `YYYY.MM.DD` ゼロ埋め 2 桁・実在する日付のみ
        _raises(
            expert_mapping(Dates=OMIT, FromDate=value, ToDate="2024.05.18"), SettingsValueError
        )

    @pytest.mark.parametrize(("key", "value"), [("ProfitInPips", "2"), ("Visual", "2")])
    def test_boolean_keys_accept_only_zero_or_one(self, key, value):
        # R11
        _raises(expert_mapping(**{key: value}), SettingsValueError)

    @pytest.mark.parametrize("execution_mode", ["-1", "0", "21", "50"])
    def test_execution_mode_keeps_the_raw_integer(self, execution_mode):
        # 基本設計 §4.3.5: 生 int を保持し意味付けしない
        settings = tester_settings_from_mapping(expert_mapping(ExecutionMode=execution_mode))
        assert settings.execution_delay == int(execution_mode)


class TestRuleOUnknownValues:
    """規則 O / R13: 列挙にない値は E-05（`UnknownSettingValueError`）。"""

    @pytest.mark.parametrize(
        ("key", "value"),
        [
            ("Model", "5"),
            ("Model", "9"),
            ("Model", "-1"),
            ("Dates", "1"),
            ("Dates", "3"),
            ("ForwardMode", "1"),
            ("ForwardMode", "2"),
            ("ForwardMode", "5"),
            ("Optimization", "3"),
            ("OptimizationCriterion", "2"),
        ],
    )
    def test_out_of_enum_values_are_rejected(self, key, value):
        error = _raises(expert_mapping(**{key: value}), UnknownSettingValueError, rule_id="O")
        assert error.context["key"] == key
        assert error.context["value"] == value

    @pytest.mark.parametrize("label", ["Q1", "1h", "PERIOD_H1", "daily", "", "16385"])
    def test_unknown_timeframe_label_is_rejected(self, label):
        # D-03: 未知ラベルは沈黙受容せず E-05（誤りが結果に混入しない方向へ倒す）
        _raises(expert_mapping(Period=label), UnknownSettingValueError, rule_id="O")

    @pytest.mark.parametrize("label", ["Daily", "H1", "H8"])
    def test_measured_timeframe_labels_are_accepted(self, label):
        # corpus 実測ラベル 3 件
        assert tester_settings_from_mapping(expert_mapping(Period=label)).timeframe is not None

    @pytest.mark.parametrize("model", ["0", "1", "2", "3", "4"])
    def test_all_five_tick_models_are_accepted(self, model):
        # `3`（MATH_CALCULATIONS）は暫定（TBD-01）だが列挙に存在する
        settings = tester_settings_from_mapping(expert_mapping(Model=model))
        assert settings.tick_model == TickModel(int(model))


class TestRulePUnknownKeys:
    """規則 P / R12: `[Tester]` は corpus 実測の 18 キーに限る（E-06）。

    基本設計 §2.2.3 本文の「17 キー」は誤記（ISSUE-389）。許容集合は
    `ini_codec.TESTER_KEYS`（順序表から導出）が単一ソース。
    """

    @pytest.mark.parametrize("key", ["Report", "ReplaceReport", "ShutdownTerminal", "UseLocal"])
    def test_out_of_scope_key_is_rejected(self, key):
        # N-12: 実測されていないキーの意味を推定しない
        error = _raises(expert_mapping(**{key: "1"}), UnknownSettingKeyError, rule_id="P")
        assert error.context["key"] == key

    def test_lowercase_variant_of_a_known_key_is_rejected(self):
        # R5: キーは大小区別あり
        _raises(expert_mapping(Expert=OMIT, expert="a.ex5"), UnknownSettingKeyError, rule_id="P")


class TestRuleQInputs:
    """規則 Q: `[TesterInputs]` の 2 形式（E-01）。"""

    @pytest.mark.parametrize(
        "line",
        ["MAPeriod=3||2", "MAPeriod=3||2||1", "MAPeriod=3||2||1||22", "MAPeriod=3||2||1||22||Y||Z"],
    )
    def test_invalid_field_count_is_rejected(self, line):
        _raises(expert_mapping(), IniFormatError, inputs=(line,), rule_id="R8")

    @pytest.mark.parametrize("flag", ["y", "n", "Yes", "1"])
    def test_invalid_optimize_flag_is_rejected(self, flag):
        _raises(expert_mapping(), IniFormatError, inputs=(f"MAPeriod=3||2||1||22||{flag}",))

    def test_duplicated_input_name_is_rejected(self):
        _raises(expert_mapping(), IniFormatError, inputs=("A=1", "A=2"))

    def test_input_line_without_equal_sign_is_rejected(self):
        _raises(expert_mapping(), IniFormatError, inputs=("MAPeriod",))

    def test_input_line_count_above_the_limit_is_rejected(self):
        # 基本設計 §4.2 #17 / 内部設計 §4.3.2 規則 Q: 上限超過は検証層で E-01（rule_id=R8）
        # 上限値は字句層の定数を参照する（テスト側にリテラルを書かない）
        _raises(
            expert_mapping(),
            IniFormatError,
            inputs=tuple(f"P{i}=1" for i in range(MAX_INPUT_LINES + 1)),
            rule_id="R8",
        )

    def test_input_line_count_at_the_limit_is_accepted(self):
        # 境界値: 上限ちょうどは超過ではない
        settings = tester_settings_from_mapping(
            expert_mapping(), inputs=tuple(f"P{i}=1" for i in range(MAX_INPUT_LINES))
        )
        assert len(settings.inputs) == MAX_INPUT_LINES

    def test_empty_inputs_are_accepted(self):
        assert tester_settings_from_mapping(expert_mapping(), inputs=()).inputs == ()


class TestTranslationPriority:
    """§4.3.3: 翻訳は優先順位表で決まり、フィールド定義順・挿入順に依存しない。"""

    @staticmethod
    def _multi_violation_mapping():
        """4 種の違反（未知キー / 未知値 / 構造矛盾 / 欠落）を同時に含むマッピング。"""
        return expert_mapping(
            Report="1",  # extra_forbidden → E-06（最優先）
            Model="9",  # 未知値 → E-05
            Indicator="Band.ex5",  # 構造矛盾 → E-02
            Deposit=OMIT,  # 欠落 → E-08
            Leverage="0",  # 値域 → E-04（最下位）
        )

    def test_unknown_key_wins_over_every_other_violation(self):
        # 優先順位 1: extra_forbidden（ファイル自体が対象外の可能性）
        _raises(self._multi_violation_mapping(), UnknownSettingKeyError)

    def test_unknown_value_wins_over_conflict_missing_and_value_error(self):
        # 優先順位 2: 未知値（MT5 バージョン差の検出シグナル）
        mapping = self._multi_violation_mapping()
        del mapping["Report"]
        _raises(mapping, UnknownSettingValueError)

    def test_conflict_wins_over_missing_and_value_error(self):
        # 優先順位 3: 構造の矛盾
        mapping = self._multi_violation_mapping()
        del mapping["Report"]
        mapping["Model"] = "1"
        _raises(mapping, SettingsKeyConflictError)

    def test_missing_wins_over_value_error(self):
        # 優先順位 4: 欠落
        mapping = self._multi_violation_mapping()
        del mapping["Report"]
        del mapping["Indicator"]
        mapping["Model"] = "1"
        _raises(mapping, SettingsKeyMissingError)

    def test_value_error_is_the_default(self):
        # 優先順位 5: 残り全部
        _raises(expert_mapping(Leverage="0", Deposit="0"), SettingsValueError)

    @pytest.mark.parametrize("transform", [lambda m: m, reordered], ids=["as_is", "reversed"])
    def test_selection_is_independent_of_the_mapping_insertion_order(self, transform):
        # 決定論: マッピングの挿入順（＝pydantic の検証順に影響し得る）を変えても同じ例外
        _raises(transform(self._multi_violation_mapping()), UnknownSettingKeyError)

    @pytest.mark.parametrize("transform", [lambda m: m, reordered], ids=["as_is", "reversed"])
    def test_missing_selection_is_order_independent(self, transform):
        mapping = self._multi_violation_mapping()
        del mapping["Report"]
        del mapping["Indicator"]
        mapping["Model"] = "1"
        _raises(transform(mapping), SettingsKeyMissingError)

    def test_only_one_exception_is_raised(self):
        # 代替案 B（ExceptionGroup）を採らない: 既存 `except ConfigError` に載せる
        with pytest.raises(UnknownSettingKeyError) as excinfo:
            tester_settings_from_mapping(self._multi_violation_mapping())
        assert not isinstance(excinfo.value, BaseExceptionGroup)


class TestValidationErrorsContext:
    """`context["validation_errors"]` に全件が載る（原因究明の容易性）。"""

    def test_all_violations_are_recorded(self):
        # Arrange
        mapping = expert_mapping(Leverage="0", Deposit="0", Currency="jpy")
        # Act
        error = _raises(mapping, SettingsValueError)
        # Assert: 選ばれた 1 件だけでなく全件が残る
        recorded = error.context["validation_errors"]
        assert isinstance(recorded, list)
        assert len(recorded) >= 3

    def test_validation_errors_are_json_serialisable(self):
        # §4.5.2 規約 1: context の値は JSON 直列化可能
        error = _raises(expert_mapping(Leverage="0", Deposit="0"), SettingsValueError)
        assert json.dumps(error.context["validation_errors"]) is not None

    def test_selected_error_details_are_present(self):
        error = _raises(expert_mapping(Leverage="0"), SettingsValueError, rule_id="J")
        assert error.context["key"] == "Leverage"
        assert error.context["value"] == "0"

    def test_pydantic_validation_error_is_kept_as_cause(self):
        # 原 `ValidationError` は `__cause__` に保持し pydantic 型を上位へ漏らさない
        error = _raises(expert_mapping(Leverage="0"), SettingsValueError)
        assert error.__cause__ is not None
        assert type(error.__cause__).__name__ == "ValidationError"

    def test_error_is_catchable_as_config_error(self):
        from simulator.domain.exceptions import ConfigError

        with pytest.raises(ConfigError):
            tester_settings_from_mapping(expert_mapping(Leverage="0"))


class TestNonAsciiDigitsAreRejected:
    """是正 3（レビュー指摘 🟡-3）: 数値・日付バリデータは ASCII 数字のみ受理する。

    Python の ``re`` の ``\\d`` と ``int`` / ``float`` / ``date`` は Unicode 数字を
    受理するため、全角・アラビア数字が通り、値が無言で書き換わる（`Model="１"` が
    `1` になり `to_mapping` 出力が入力トークンと一致しなくなる）。MT5 が生成し得ない
    字形は Fail-Stop する（`^[+-]?[0-9]+$` 等の ASCII 限定へ揃える）。
    """

    #: 非 ASCII 数字（全角 / アラビア・インド数字）。いずれも MT5 の出力に実測がない。
    FULLWIDTH_ONE = "１"
    ARABIC_INDIC_TEN = "١٠"

    @pytest.mark.parametrize(
        ("key", "value"),
        [
            ("Model", FULLWIDTH_ONE),
            ("Model", "١"),
            ("Leverage", "１０"),
            ("Leverage", ARABIC_INDIC_TEN),
            ("ExecutionMode", "５０"),
            ("Optimization", "０"),
            ("Dates", "０"),
            ("ForwardMode", "０"),
            ("OptimizationCriterion", "０"),
            ("ProfitInPips", FULLWIDTH_ONE),
            ("Visual", FULLWIDTH_ONE),
        ],
        ids=lambda item: repr(item),
    )
    def test_integer_fields_reject_non_ascii_digits(self, key, value):
        # E-04（`_strict_int` の書式違反）
        _raises(expert_mapping(**{key: value}), SettingsValueError)

    @pytest.mark.parametrize(
        "value",
        ["１３９５００", "139500.５0", "１39500", "139500.5０"],
        ids=lambda item: repr(item),
    )
    def test_deposit_rejects_non_ascii_digits(self, value):
        # E-04（`_strict_decimal` の書式違反）
        _raises(expert_mapping(Deposit=value), SettingsValueError)

    @pytest.mark.parametrize(
        "value",
        ["２０２０.０３.３０", "２０２０.03.30", "2020.０3.30", "2020.03.３0"],
        ids=lambda item: repr(item),
    )
    def test_date_fields_reject_non_ascii_digits(self, value):
        # E-04（`_strict_date` の書式違反・R10）
        _raises(
            expert_mapping(Dates=OMIT, FromDate=value, ToDate="2024.05.18"), SettingsValueError
        )

    def test_forward_date_rejects_non_ascii_digits(self):
        _raises(
            expert_mapping(ForwardMode="4", ForwardDate="２０２３.０５.２２"), SettingsValueError
        )

    @pytest.mark.parametrize(
        ("key", "value"),
        [("Deposit", "139500．50"), ("FromDate", "2020．03．30")],
        ids=lambda item: repr(item),
    )
    def test_fullwidth_period_stays_rejected(self, key, value):
        # 不変条件の固定（是正前から拒否されている。是正で緩まないことを保証する）
        mapping = (
            expert_mapping(Deposit=value)
            if key == "Deposit"
            else expert_mapping(Dates=OMIT, FromDate=value, ToDate="2024.05.18")
        )
        _raises(mapping, SettingsValueError)

    @pytest.mark.parametrize(
        ("key", "value"),
        [
            ("Model", "1"),
            ("Leverage", "10"),
            ("ExecutionMode", "-50"),
            ("Deposit", "139500"),
            ("Deposit", "139500.50"),
        ],
        ids=lambda item: repr(item),
    )
    def test_ascii_tokens_are_still_accepted(self, key, value):
        # 不変条件の固定（正常系を壊さない）
        assert tester_settings_from_mapping(expert_mapping(**{key: value})) is not None

    def test_ascii_dates_are_still_accepted(self):
        settings = tester_settings_from_mapping(
            expert_mapping(Dates=OMIT, FromDate="2020.03.30", ToDate="2024.05.18")
        )
        assert settings.date_range.from_date == date(2020, 3, 30)
