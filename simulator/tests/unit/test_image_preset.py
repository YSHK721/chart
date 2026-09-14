"""画像 1 の再現プリセット（基本設計 §4.7・T-02）の 16 行写像表を固定する。

⚠️ 本モジュールは**実装より先に書いたテスト**である（フェーズ 3 = Red）。
`simulator.framework.tester_settings.loader` は未実装のため、現時点では
**収集エラー（ImportError）** になる。

暫定 4 件（`Model=3` / `Period=M1` / `ExecutionMode=0` / `Visual=0`）は
`xfail` を使わず**通常アサート＋暫定コメント**で固定する（値が変わったら落ちて
気付ける＝内部設計 §9.2 T-02 の指定）。
"""
from __future__ import annotations

from datetime import date

import pytest

from simulator.framework.tester_settings.loader import (
    tester_settings_from_mapping,
    tester_settings_to_mapping,
)
from simulator.usecase.tester_settings.enums import (
    DateRangeKind,
    ForwardMode,
    OptimizationCriterion,
    OptimizationMode,
    SubjectKind,
    TickModel,
    Timeframe,
)
from simulator.usecase.tester_settings.models import INERT_FIELDS

#: 基本設計 §4.7 の `.ini` プリセット（標準キー順・Expert テスト）。
#: `.ini` 上のキーは 15 個（写像表 16 行のうち #8 Forward 日付はキーを出力しない）。
IMAGE_PRESET_INI: dict[str, str] = {
    "Expert": "260620-01_limit_stop.ex5",
    "Symbol": "JP225",
    "Period": "M1",
    "Optimization": "0",
    "Model": "3",
    "FromDate": "2026.04.01",
    "ToDate": "2026.04.30",
    "ForwardMode": "0",
    "Deposit": "10000",
    "Currency": "JPY",
    "ProfitInPips": "0",
    "Leverage": "10",
    "ExecutionMode": "0",
    "OptimizationCriterion": "0",
    "Visual": "0",
}

#: §4.7 の写像表 16 行（画面 # → 期待するフィールド名と値）。
#: 値が `None` の行（#8）は「キーを出力せず、フィールドは `None`」を表す。
CONTROL_ROWS: tuple[tuple[int, str, str, object], ...] = (
    (1, "Expert", "subject_path", "260620-01_limit_stop.ex5"),
    (2, "Symbol", "symbol", "JP225"),
    # 暫定（TBD-10）: `.ini` のラベル表記が `M1` である直接実測はない
    (3, "Period", "timeframe", Timeframe.M1),
    (4, "", "date_range_kind", DateRangeKind.CUSTOM),
    (5, "FromDate", "from_date", date(2026, 4, 1)),
    (6, "ToDate", "to_date", date(2026, 4, 30)),
    (7, "ForwardMode", "forward_mode", ForwardMode.DISABLED),
    # F-10: `ForwardMode != 4` では `ForwardDate` キーを出力しない（UI に日付があっても）
    (8, "ForwardDate", "forward_date", None),
    # 暫定（TBD-08）: 値 `0` が `Zero latency, ideal execution` に対応する根拠は未取得
    (9, "ExecutionMode", "execution_delay", 0),
    # 暫定（TBD-01）: corpus 未出現。0 / 1 / 2 / 4 からの消去法
    (10, "Model", "tick_model", TickModel.MATH_CALCULATIONS),
    (11, "ProfitInPips", "profit_in_pips", False),
    (12, "Deposit", "deposit", 10000.0),
    (13, "Currency", "currency", "JPY"),
    # 暫定（TBD-12）: UI 表記 `1:N` の N を保存する解釈
    (14, "Leverage", "leverage", 10),
    (15, "Optimization", "optimization", OptimizationMode.DISABLED),
    # 暫定（TBD-13）: `Visual=0` は corpus 未出現（実測は `1` のみ）
    (16, "Visual", "visual", False),
)


@pytest.fixture()
def preset():
    """§4.7 の写像表から構築した `TesterSettings`。"""
    return tester_settings_from_mapping(IMAGE_PRESET_INI, inputs=())


class TestPresetShape:
    """プリセット `.ini` 自体の形（§4.7 の記述との一致）。"""

    def test_preset_has_fifteen_keys(self):
        assert len(IMAGE_PRESET_INI) == 15

    def test_mapping_table_has_sixteen_rows(self):
        assert len(CONTROL_ROWS) == 16

    def test_forward_date_key_is_absent(self):
        # #8: UI に日付が表示されていても `.ini` には書かれない
        assert "ForwardDate" not in IMAGE_PRESET_INI

    def test_dates_key_is_absent_because_the_range_is_custom(self):
        # #4: Custom period は `FromDate` / `ToDate` を出力する（キーなし）
        assert "Dates" not in IMAGE_PRESET_INI


class TestPresetFields:
    """写像表 16 行を 1 行ずつフィールド値と突合する。"""

    def test_subject_kind_is_expert(self, preset):
        assert preset.subject_kind is SubjectKind.EXPERT

    @pytest.mark.parametrize(
        ("row", "field_name", "expected"),
        [(row, field_name, expected) for row, _, field_name, expected in CONTROL_ROWS],
        ids=[f"row{row:02d}_{field_name}" for row, _, field_name, _ in CONTROL_ROWS],
    )
    def test_control_row_maps_to_the_expected_field_value(self, preset, row, field_name, expected):
        # Arrange: 期間の 3 行は入れ子 DTO `DateRange` を参照する
        if field_name == "date_range_kind":
            actual = preset.date_range.kind
        elif field_name in ("from_date", "to_date"):
            actual = getattr(preset.date_range, field_name)
        else:
            actual = getattr(preset, field_name)
        # Act / Assert
        assert actual == expected, f"§4.7 写像表 #{row}"

    def test_no_header_comment_is_produced(self):
        # §4.7: 1 行目のコメント行は含めない（`Math calculations` の語が未実測）
        assert tester_settings_from_mapping(IMAGE_PRESET_INI).header_comment is None

    def test_inputs_are_empty(self, preset):
        # §4.7: EA の `input` 宣言が不明なため空セクション（F-19）
        assert preset.inputs == ()

    def test_source_holds_the_raw_preset_tokens(self, preset):
        # 是正 1: API-03 は生トークンを `source` に保持する（写像不能を消す）
        assert dict(preset.source.entries("[Tester]")) == IMAGE_PRESET_INI

    def test_round_trip_to_mapping_reproduces_the_preset(self, preset):
        # 標準キー順を含めて一致する（R6）
        assert tester_settings_to_mapping(preset) == IMAGE_PRESET_INI
        assert list(tester_settings_to_mapping(preset)) == list(IMAGE_PRESET_INI)


class TestPresetRuntimeSemantics:
    """§4.7「本プリセットの実行時挙動」: `MATH_CALCULATIONS` により 11 フィールドが inert。"""

    def test_effective_view_nulls_out_every_inert_field(self, preset):
        # Arrange / Act
        effective = preset.effective()
        # Assert
        for name in INERT_FIELDS:
            assert getattr(effective, name) is None, name

    def test_effective_view_reports_math_calculations(self, preset):
        assert preset.effective().is_math_calculations is True

    def test_non_inert_fields_survive_the_effective_view(self, preset):
        effective = preset.effective()
        assert effective.subject_kind is SubjectKind.EXPERT
        assert effective.subject_path == "260620-01_limit_stop.ex5"
        assert effective.tick_model is TickModel.MATH_CALCULATIONS
        assert effective.optimization is OptimizationMode.DISABLED
        assert effective.optimization_criterion is OptimizationCriterion.CRITERION_0

    def test_settings_retain_the_inert_values_for_round_trip(self, preset):
        # inert 化は派生ビュー側のみ。往復のため元 DTO は値を保持する
        assert preset.deposit == 10000.0
        assert preset.leverage == 10
        assert preset.visual is False
