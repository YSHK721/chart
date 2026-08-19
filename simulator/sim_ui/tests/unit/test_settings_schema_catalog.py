"""TesterSettingsSchemaCatalog の単体検定（Phase 8 スライス 1）.

固定する不変条件（基本設計 §18.3「選択肢は enums からの反復導出のみ」）:
    1. `Period` の選択肢は `TIMEFRAME_INI_LABELS` と**集合として一致**する。
    2. `Model` のトークンは `TickModel` の生値表記と一致する。
    3. `Expert` の候補は「注入された EA 名 ＋ 注入された対象接尾辞」である。
    4. `ExecutionMode` の仕様は `PROVEN_EXECUTION_DELAYS` を写す（実証状態の宣言は enums 1 箇所）。
    5. 非対象の告知は**注入された宣言表**（`unsupported.RULES`）を過不足なく写す。
    6. キー順・必須キー・Expert 専用の別は**注入された外側事実**をそのまま写す。

期待値をこの検定に**リテラルで書かない**のが要点である。リテラルで書けば、それは
「単一ソースの複製」であり、enums を変えたときにこの検定だけが古いまま緑になる。

注入値について: ``subject_suffix`` には実体（`.ex5`）と**異なる**値を渡す。カタログが
注入値を使わず自前のリテラルを持っていた場合、この検定が落ちる（複製の検出）。
"""
from __future__ import annotations

import pytest

from simulator.adapter.tester_settings.ini_codec import STANDARD_KEY_ORDER
from simulator.framework.tester_settings.validation import EXPERT_ONLY_KEYS
from simulator.main.tester_settings.unsupported import RULES
# 別名で受けるのは pytest の収集規則（`Test*` 接頭辞）を避けるため。既存
# `test_tester_settings_validation.py:68`（`TesterSettings as SettingsDto`）と同じ流儀。
from simulator.sim_ui.adapter.tester_settings_schema_catalog import (
    TesterSettingsSchemaCatalog as SchemaCatalog,
)
from simulator.usecase.tester_settings.enums import (
    PROVEN_EXECUTION_DELAYS,
    PROVISIONAL_EXECUTION_DELAYS,
    TIMEFRAME_INI_LABELS,
    DatesPreset,
    ForwardMode,
    OptimizationCriterion,
    OptimizationMode,
    TickModel,
)

#: 注入する EA 名（権威は `simulator.main.known_ea_names`・ここでは注入の素通しだけ見る）。
_EA_NAMES = ("Alpha_EA", "Beta_EA")
#: 実体（`.ex5`）と異なる接尾辞。カタログ内リテラルの混入を検出するための注入値。
_SUFFIX = ".probe-suffix"
#: 注入する必須キー（権威は検証層のモデル・ここでは素通しだけ見る）。
_REQUIRED = ("Symbol",)


@pytest.fixture
def catalog() -> SchemaCatalog:
    return SchemaCatalog(
        key_order=STANDARD_KEY_ORDER,
        required_keys=_REQUIRED,
        expert_only_keys=EXPERT_ONLY_KEYS,
        known_ea_names=lambda: _EA_NAMES,
        subject_suffix=_SUFFIX,
        unsupported_rules=RULES,
    )


def test_period_options_are_derived_from_the_timeframe_label_map(catalog) -> None:
    # Arrange / Act
    options = catalog.enum_options()["Period"]
    # Assert
    assert {o.token for o in options} == set(TIMEFRAME_INI_LABELS.values())
    assert len(options) == len(TIMEFRAME_INI_LABELS)  # 取りこぼし・重複なし
    assert {o.label for o in options} == {tf.name for tf in TIMEFRAME_INI_LABELS}


def test_model_options_are_derived_from_the_tick_model_enum(catalog) -> None:
    # Arrange / Act
    options = catalog.enum_options()["Model"]
    # Assert
    assert {o.token for o in options} == {str(int(m)) for m in TickModel}
    assert {o.label for o in options} == {m.name for m in TickModel}


@pytest.mark.parametrize(
    ("key", "members"),
    [
        ("Dates", DatesPreset),
        ("ForwardMode", ForwardMode),
        ("Optimization", OptimizationMode),
        ("OptimizationCriterion", OptimizationCriterion),
    ],
)
def test_int_enum_options_are_derived_from_their_enum(catalog, key, members) -> None:
    # Arrange / Act
    options = catalog.enum_options()[key]
    # Assert
    assert {o.token for o in options} == {str(int(m)) for m in members}
    assert {o.label for o in options} == {m.name for m in members}


def test_every_enum_key_is_a_key_of_the_injected_key_order(catalog) -> None:
    """列挙キーが `.ini` の標準キー順から外れていないこと（語彙の食い違い検出）。

    空の写像でも部分集合条件は成立するため、**非空**を併せて固定する（空振り防止）。
    """
    keys = set(catalog.enum_options())
    assert keys  # 空の写像で条件が空振りしない
    assert keys <= set(STANDARD_KEY_ORDER)


def test_expert_options_are_known_ea_names_with_the_injected_suffix(catalog) -> None:
    # Arrange / Act
    options = catalog.expert_options()
    # Assert
    assert all(o.token.endswith(_SUFFIX) for o in options)
    assert {o.token.removesuffix(_SUFFIX) for o in options} == set(_EA_NAMES)
    assert {o.label for o in options} == set(_EA_NAMES)


def test_execution_mode_spec_carries_the_proven_delays(catalog) -> None:
    # Arrange / Act
    spec = catalog.scalar_specs()["ExecutionMode"]
    # Assert
    assert spec["proven"] == sorted(PROVEN_EXECUTION_DELAYS)
    assert spec["provisional"] == {
        str(delay): tbd for delay, tbd in sorted(PROVISIONAL_EXECUTION_DELAYS.items())
    }


def test_scalar_specs_mark_expert_only_keys_from_the_injection(catalog) -> None:
    # Arrange / Act
    specs = catalog.scalar_specs()
    # Assert
    assert {key for key, spec in specs.items() if spec["expert_only"]} == (
        set(EXPERT_ONLY_KEYS) - set(catalog.enum_options())
    )
    assert set(specs) == set(STANDARD_KEY_ORDER) - set(catalog.enum_options())


def test_unsupported_notices_cover_every_injected_rule(catalog) -> None:
    # Arrange / Act
    notices = catalog.unsupported()
    # Assert
    assert {n.unsupported_id for n in notices} == set(RULES)
    by_id = {n.unsupported_id: n for n in notices}
    assert all(by_id[rid].reason == rule.reason for rid, rule in RULES.items())
    assert all(by_id[rid].field == rule.field for rid, rule in RULES.items())
    assert all(by_id[rid].tbd == rule.tbd for rid, rule in RULES.items())


# --- 非対象の UI 束縛（R-9・宣言駆動）------------------------------------------
# front は「どの選択が非対象に当たるか」をキー名の正規表現や既定値スナップショットから
# **推測してはならない**（推測は宣言と食い違っても静かに 0 件になる）。束縛は宣言側
# （`UnsupportedRule.ui`）が所有し、カタログはそれを解決して配るだけである。


def test_every_notice_binds_to_a_non_empty_subset_of_the_key_order(catalog) -> None:
    """空紐付け（どのキーにも当たらない告知）を禁じる。

    空を許すと「宣言はあるのに UI では絶対に出ない」告知が生まれ、沈黙で保証境界の
    外へ出られる。全件が **非空** かつ **キー順の部分集合** であることを固定する。
    """
    # Arrange / Act
    notices = catalog.unsupported()
    # Assert
    assert notices, "告知が 0 件（宣言表の注入が届いていない）"
    for notice in notices:
        assert notice.keys, f"{notice.unsupported_id} がどの `.ini` キーにも紐づいていません"
        unknown = set(notice.keys) - set(STANDARD_KEY_ORDER)
        assert not unknown, f"{notice.unsupported_id} が未知のキーへ紐づいています: {sorted(unknown)}"


def test_every_notice_declares_a_trigger_the_ui_can_evaluate(catalog) -> None:
    """発火条件が宣言されており、トークン列挙型なら token が空でないこと。"""
    # Arrange
    from simulator.main.tester_settings.unsupported import UI_TRIGGER_MODES, UI_TRIGGERS_WITH_TOKENS

    # Act
    notices = catalog.unsupported()
    # Assert
    for notice in notices:
        assert notice.trigger in UI_TRIGGER_MODES, (
            f"{notice.unsupported_id} の発火条件が未知です: {notice.trigger!r}"
        )
        if notice.trigger in UI_TRIGGERS_WITH_TOKENS:
            assert notice.tokens, f"{notice.unsupported_id} のトークン集合が空です"


def test_notice_binding_is_the_rule_declaration_verbatim(catalog) -> None:
    """カタログは宣言を**写すだけ**（キー・条件・トークンを自前で導出しない）。"""
    # Arrange / Act
    by_id = {n.unsupported_id: n for n in catalog.unsupported()}
    # Assert
    for rule_id, rule in RULES.items():
        assert by_id[rule_id].keys == tuple(rule.ui.keys)
        assert by_id[rule_id].trigger == rule.ui.mode
        assert by_id[rule_id].tokens == tuple(rule.ui.tokens)


def test_a_rule_without_a_ui_binding_is_rejected_at_construction() -> None:
    """宣言を欠いた rule を黙って配らない（沈黙の縮退を作らない・構築時 Fail-Stop）。"""
    # Arrange: `ui` を持たない宣言（将来 rule を足したときの取り違えの再現）
    from dataclasses import replace

    broken = dict(RULES)
    victim = next(iter(broken))
    broken[victim] = replace(broken[victim], ui=None)
    # Act / Assert
    with pytest.raises(ValueError, match=victim):
        SchemaCatalog(
            key_order=STANDARD_KEY_ORDER,
            required_keys=_REQUIRED,
            expert_only_keys=EXPERT_ONLY_KEYS,
            known_ea_names=lambda: _EA_NAMES,
            subject_suffix=_SUFFIX,
            unsupported_rules=broken,
        ).unsupported()


def test_key_order_and_required_keys_pass_the_injection_through(catalog) -> None:
    # Arrange / Act / Assert
    assert catalog.key_order() == tuple(STANDARD_KEY_ORDER)
    assert catalog.required_keys() == _REQUIRED
