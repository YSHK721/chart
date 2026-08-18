"""Settings の DTO 群の単体テスト（内部設計 §4.2・基本設計 §4.5.5 規則 A・C）。

固定する仕様:
    1. `TesterSettings` と `EffectiveSettings` のフィールド集合が**完全一致**すること
       （`dataclasses.fields` による機械比較。19 行を 2 度書けば必ず片方が腐る）。
    2. `effective()` が `MATH_CALCULATIONS` のとき inert 11 フィールドを `None` 化し、
       それ以外のフィールドを素通しすること（純関数）。
    3. `EffectiveSettings` が `TesterSettings` の**派生でない**こと（LSP。派生ビューが
       往復経路へ流れ込めないようにする＝内部設計 §4.2.1）。
    4. `IniDocument` の `key_order` / `entries` / `entry` / `header_comment` の振る舞い。
"""
from __future__ import annotations

import dataclasses
from datetime import date

import pytest

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
    INERT_FIELDS,
    RAW_REPRESENTATION_FIELDS,
    DateRange,
    EffectiveSettings,
    IniDocument,
    IniLine,
    IniLineKind,
    SettingsPayload,
    TesterInput as InputDto,
    TesterSettings as SettingsDto,
)

# 別名の理由: pytest は `Test` で始まるモジュール属性をテストクラス候補として収集しようとし、
# `PytestCollectionWarning` を出す。DTO をテスト対象名で束縛せず、別名で参照する。

#: 規則 A（10 フィールド）＋規則 C（`visual`）＝ inert 11 フィールド（基本設計 §4.5.5）。
EXPECTED_INERT_FIELDS: frozenset[str] = frozenset(
    {
        "symbol",
        "timeframe",
        "date_range",
        "forward_mode",
        "forward_date",
        "execution_delay",
        "profit_in_pips",
        "deposit",
        "currency",
        "leverage",
        "visual",
    }
)


def _full_settings(**overrides) -> SettingsDto:
    """全フィールドに非 None を詰めた `TesterSettings`（素通し検証の土台）。"""
    values = dict(
        subject_kind=SubjectKind.EXPERT,
        subject_path="TC24051903.ex5",
        tick_model=TickModel.ONE_MINUTE_OHLC,
        symbol="JP225",
        timeframe=Timeframe.D1,
        date_range=DateRange(kind=DateRangeKind.PRESET, preset=DatesPreset.ENTIRE_HISTORY),
        forward_mode=ForwardMode.CUSTOM_DATE,
        forward_date=date(2023, 5, 22),
        deposit=139500.0,
        currency="JPY",
        profit_in_pips=True,
        leverage=10,
        execution_delay=50,
        optimization=OptimizationMode.DISABLED,
        optimization_criterion=OptimizationCriterion.CRITERION_0,
        visual=True,
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
        ),
        header_comment=";Expert Advisor visual test: TC24051903, JP225 Daily, m1 ohlc, entire history",
        source=None,
    )
    values.update(overrides)
    return SettingsDto(**values)


def _document(*lines: IniLine, **kwargs) -> IniDocument:
    defaults = dict(encoding="utf-16-le", newline="\r\n", has_bom=True, trailing_newline=True)
    defaults.update(kwargs)
    return IniDocument(lines=lines, **defaults)


class TestFieldSetIdentity:
    """`TesterSettings` と `EffectiveSettings` は同一フィールド集合を持つ（内部設計 §4.2.1）。"""

    def test_field_names_are_identical(self):
        # Arrange
        tester = [f.name for f in dataclasses.fields(SettingsDto)]
        effective = [f.name for f in dataclasses.fields(EffectiveSettings)]
        # Act / Assert: 順序も含めて一致（宣言が 1 箇所であることの帰結）
        assert tester == effective

    def test_field_types_are_identical(self):
        tester = {(f.name, f.type) for f in dataclasses.fields(SettingsDto)}
        effective = {(f.name, f.type) for f in dataclasses.fields(EffectiveSettings)}
        assert tester == effective

    def test_field_count_is_nineteen(self):
        # 基本設計 §4.2 の 19 フィールド
        assert len(dataclasses.fields(SettingsDto)) == 19

    def test_both_share_the_single_payload_declaration(self):
        assert issubclass(SettingsDto, SettingsPayload)
        assert issubclass(EffectiveSettings, SettingsPayload)

    def test_effective_settings_is_not_a_subclass_of_tester_settings(self):
        # LSP: 派生ビューが `TesterSettings` を要求する往復経路へ流れ込めないこと
        assert not issubclass(EffectiveSettings, SettingsDto)

    def test_tester_settings_is_not_a_subclass_of_effective_settings(self):
        assert not issubclass(SettingsDto, EffectiveSettings)

    def test_effective_instance_is_not_an_instance_of_tester_settings(self):
        assert not isinstance(_full_settings().effective(), SettingsDto)

    @pytest.mark.parametrize("dto", [SettingsDto, EffectiveSettings, DateRange, InputDto, IniLine, IniDocument])
    def test_dtos_are_frozen(self, dto):
        assert dataclasses.fields(dto) is not None
        assert getattr(dto, "__dataclass_params__").frozen is True


class TestInertFields:
    """inert 対象は規則 A の 10 フィールド＋規則 C の `visual` の 11 件。"""

    def test_inert_fields_match_the_design_table(self):
        assert set(INERT_FIELDS) == EXPECTED_INERT_FIELDS

    def test_inert_field_count_is_eleven(self):
        assert len(INERT_FIELDS) == 11
        assert len(set(INERT_FIELDS)) == 11

    def test_inert_fields_are_declared_fields(self):
        declared = {f.name for f in dataclasses.fields(SettingsDto)}
        assert set(INERT_FIELDS) <= declared


class TestEffective:
    """`TesterSettings.effective()`（規則 A・C。純関数）。"""

    def test_math_calculations_nulls_out_every_inert_field(self):
        # Arrange
        settings = _full_settings(tick_model=TickModel.MATH_CALCULATIONS)
        # Act
        effective = settings.effective()
        # Assert
        for name in INERT_FIELDS:
            assert getattr(effective, name) is None, name

    @pytest.mark.parametrize(
        "name",
        [
            "subject_kind",
            "subject_path",
            "tick_model",
            "optimization",
            "optimization_criterion",
            "inputs",
        ],
    )
    def test_math_calculations_passes_through_non_inert_fields(self, name):
        # Arrange
        settings = _full_settings(tick_model=TickModel.MATH_CALCULATIONS)
        # Act
        effective = settings.effective()
        # Assert
        assert getattr(effective, name) == getattr(settings, name)

    @pytest.mark.parametrize(
        "tick_model",
        [
            TickModel.EVERY_TICK,
            TickModel.ONE_MINUTE_OHLC,
            TickModel.OPEN_PRICES_ONLY,
            TickModel.REAL_TICKS,
        ],
    )
    def test_non_math_models_copy_every_field_verbatim(self, tick_model):
        # Arrange
        settings = _full_settings(tick_model=tick_model)
        # Act
        effective = settings.effective()
        # Assert: 生表現 2 フィールドを除く全フィールドが素通し
        for field in dataclasses.fields(SettingsDto):
            if field.name in RAW_REPRESENTATION_FIELDS:
                continue
            assert getattr(effective, field.name) == getattr(settings, field.name), field.name

    @pytest.mark.parametrize("tick_model", list(TickModel))
    def test_effective_always_drops_the_raw_representation(self, tick_model):
        # 規則 A の遮断が `eff.source.entry(...)` で破られないよう、生表現は常に落とす
        settings = _full_settings(tick_model=tick_model, source=_document())
        effective = settings.effective()
        assert effective.source is None
        assert effective.header_comment is None

    def test_raw_representation_fields_are_source_and_header_comment(self):
        # 基本設計 §4.2 の #18 `header_comment`（1 行目 `;` 行の原文保持）と
        # #19 `source`（生表現・往復用）が「生表現」の全件。両者だけが実行時ビューに
        # 引き写されない（期待値は設計文書のフィールド表由来であり実装値の写しではない）
        assert set(RAW_REPRESENTATION_FIELDS) == {"source", "header_comment"}

    def test_raw_representation_fields_are_declared_fields(self):
        declared = {f.name for f in dataclasses.fields(SettingsDto)}
        assert set(RAW_REPRESENTATION_FIELDS) <= declared

    def test_raw_representation_fields_do_not_overlap_inert_fields(self):
        assert set(RAW_REPRESENTATION_FIELDS).isdisjoint(INERT_FIELDS)

    def test_round_trip_source_remains_available_on_the_settings_side(self):
        # 往復（dump）は `TesterSettings` の責務。派生ビューを渡す経路は型で塞がれている
        doc = _document()
        settings = _full_settings(source=doc)
        assert settings.source is doc
        assert settings.effective().source is None

    def test_effective_returns_effective_settings_type(self):
        assert isinstance(_full_settings().effective(), EffectiveSettings)

    def test_effective_does_not_mutate_the_source_settings(self):
        # Arrange
        settings = _full_settings(tick_model=TickModel.MATH_CALCULATIONS)
        # Act
        settings.effective()
        # Assert: 元の DTO は値を保持する（往復のため破棄しない＝基本設計 §4.5.5）
        assert settings.deposit == 139500.0
        assert settings.symbol == "JP225"
        assert settings.visual is True

    def test_effective_is_deterministic(self):
        settings = _full_settings(tick_model=TickModel.MATH_CALCULATIONS)
        assert settings.effective() == settings.effective()

    def test_is_math_calculations_flag(self):
        assert _full_settings(tick_model=TickModel.MATH_CALCULATIONS).effective().is_math_calculations is True
        assert _full_settings(tick_model=TickModel.REAL_TICKS).effective().is_math_calculations is False

    def test_inert_fields_property_is_empty_for_non_math_models(self):
        assert _full_settings(tick_model=TickModel.REAL_TICKS).effective().inert_fields == ()

    def test_inert_fields_property_lists_the_eleven_fields_for_math(self):
        effective = _full_settings(tick_model=TickModel.MATH_CALCULATIONS).effective()
        assert effective.inert_fields == INERT_FIELDS


class TestIniDocument:
    """生表現の参照 API（内部設計 §4.1.1）。"""

    @staticmethod
    def _sample() -> IniDocument:
        return _document(
            IniLine(kind=IniLineKind.COMMENT, text=";Expert Advisor visual test", lineno=1),
            IniLine(kind=IniLineKind.SECTION, text="[Tester]", lineno=2, section="[Tester]"),
            IniLine(
                kind=IniLineKind.ENTRY,
                text="Expert=TC24051903.ex5",
                lineno=3,
                section="[Tester]",
                key="Expert",
                value="TC24051903.ex5",
            ),
            IniLine(
                kind=IniLineKind.ENTRY,
                text="Symbol=JP225",
                lineno=4,
                section="[Tester]",
                key="Symbol",
                value="JP225",
            ),
            IniLine(
                kind=IniLineKind.SECTION,
                text="[TesterInputs]",
                lineno=5,
                section="[TesterInputs]",
            ),
            IniLine(
                kind=IniLineKind.ENTRY,
                text="inpSymbol=",
                lineno=6,
                section="[TesterInputs]",
                key="inpSymbol",
                value="",
            ),
        )

    def test_key_order_returns_keys_in_appearance_order(self):
        assert self._sample().key_order("[Tester]") == ("Expert", "Symbol")

    def test_key_order_is_scoped_to_the_requested_section(self):
        assert self._sample().key_order("[TesterInputs]") == ("inpSymbol",)

    def test_key_order_of_unknown_section_is_empty(self):
        assert self._sample().key_order("[Missing]") == ()

    def test_entries_returns_key_value_pairs_in_order(self):
        assert self._sample().entries("[Tester]") == (
            ("Expert", "TC24051903.ex5"),
            ("Symbol", "JP225"),
        )

    def test_entries_keeps_empty_values(self):
        # F-14: `名前=` の空値は捨てない
        assert self._sample().entries("[TesterInputs]") == (("inpSymbol", ""),)

    def test_entries_excludes_comment_and_section_lines(self):
        keys = [key for key, _ in self._sample().entries("[Tester]")]
        assert "[Tester]" not in keys

    def test_entry_returns_the_value_for_an_existing_key(self):
        assert self._sample().entry("[Tester]", "Symbol") == "JP225"

    def test_entry_returns_none_for_a_missing_key(self):
        assert self._sample().entry("[Tester]", "Deposit") is None

    def test_entry_returns_empty_string_for_an_empty_value(self):
        assert self._sample().entry("[TesterInputs]", "inpSymbol") == ""

    def test_entry_returns_the_first_occurrence_when_duplicated(self):
        # 重複キーは字句層が E-01 で拒否する契約だが、DTO 自体は先頭を返す
        doc = _document(
            IniLine(kind=IniLineKind.SECTION, text="[Tester]", lineno=1, section="[Tester]"),
            IniLine(
                kind=IniLineKind.ENTRY, text="Model=1", lineno=2, section="[Tester]", key="Model", value="1"
            ),
            IniLine(
                kind=IniLineKind.ENTRY, text="Model=2", lineno=3, section="[Tester]", key="Model", value="2"
            ),
        )
        assert doc.entry("[Tester]", "Model") == "1"
        assert doc.key_order("[Tester]") == ("Model", "Model")

    def test_header_comment_returns_the_first_line_when_it_is_a_comment(self):
        assert self._sample().header_comment() == ";Expert Advisor visual test"

    def test_header_comment_is_none_when_the_first_line_is_not_a_comment(self):
        doc = _document(
            IniLine(kind=IniLineKind.SECTION, text="[Tester]", lineno=1, section="[Tester]")
        )
        assert doc.header_comment() is None

    def test_header_comment_is_none_for_an_empty_document(self):
        assert _document().header_comment() is None

    def test_header_comment_ignores_a_comment_on_a_later_line(self):
        doc = _document(
            IniLine(kind=IniLineKind.SECTION, text="[Tester]", lineno=1, section="[Tester]"),
            IniLine(kind=IniLineKind.COMMENT, text=";later", lineno=2, section="[Tester]"),
        )
        assert doc.header_comment() is None


class TestNestedDtoDefaults:
    """入れ子 DTO の既定値（基本設計 §4.2.1・§4.2.2）。"""

    def test_date_range_preset_form(self):
        rng = DateRange(kind=DateRangeKind.PRESET, preset=DatesPreset.LAST_YEAR)
        assert (rng.from_date, rng.to_date) == (None, None)

    def test_date_range_custom_form(self):
        rng = DateRange(
            kind=DateRangeKind.CUSTOM, from_date=date(2020, 3, 30), to_date=date(2024, 5, 18)
        )
        assert rng.preset is None

    def test_tester_input_scalar_defaults(self):
        # F-14: `inpSymbol=` は current が空文字で 5 分割フィールドが None
        item = InputDto(name="inpSymbol", form=InputForm.SCALAR, current="", raw="inpSymbol=")
        assert (item.start, item.step, item.stop, item.optimize) == (None, None, None, None)

    def test_settings_defaults_do_not_invent_values(self):
        # 基本設計 §4.2: deposit / currency / leverage / symbol に既定値を与えない
        settings = SettingsDto(
            subject_kind=SubjectKind.INDICATOR,
            subject_path="PRO!fit_Band.ex5",
            tick_model=TickModel.REAL_TICKS,
        )
        assert settings.deposit is None
        assert settings.currency is None
        assert settings.leverage is None
        assert settings.symbol is None
        assert settings.inputs == ()
        assert settings.source is None
