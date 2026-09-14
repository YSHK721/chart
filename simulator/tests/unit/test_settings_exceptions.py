"""Settings 層の例外階層 8 種の単体テスト（内部設計 §4.5・T-13）。

固定する仕様:
    1. 8 クラスすべてが `ConfigError` 派生であること（既存 CLI の
       `except ConfigError` → 終了コード 2 の翻訳に載る＝内部設計 §4.5.1）。
    2. `error_id` の自動付与（`ERROR_ID` を context へ setdefault）。
    3. `context` 語彙外キーで `KeyError`（呼出側のタイポを沈黙させない）。
    4. 雛形形式で必須 context キーが欠けたとき `KeyError`。
    5. `line` の 200 文字切り詰め（§4.5.2 規約 3）。
    6. 集合的な値（tuple / set / frozenset）のソート済み `list` 化（同規約 2）。
    7. 親互換シグネチャ（message + context=）での構築。
"""
from __future__ import annotations

import pytest

from simulator.domain.exceptions import BacktestError, ConfigError
from simulator.domain.tester_settings_exceptions import (
    BASE_CONTEXT,
    IniFormatError,
    SettingsActivationError,
    SettingsError,
    SettingsKeyConflictError,
    SettingsKeyMissingError,
    SettingsValueError,
    UnknownSettingKeyError,
    UnknownSettingValueError,
    UnsupportedSettingError,
)

#: 内部設計 §4.5.1 の 8 クラスと §4.5.2 の失敗 ID の対応（1:1 で固定する）。
ERROR_CLASSES: tuple[tuple[type[SettingsError], str], ...] = (
    (IniFormatError, "E-01"),
    (SettingsKeyConflictError, "E-02"),
    (SettingsActivationError, "E-03"),
    (SettingsValueError, "E-04"),
    (UnknownSettingValueError, "E-05"),
    (UnknownSettingKeyError, "E-06"),
    (UnsupportedSettingError, "E-07"),
    (SettingsKeyMissingError, "E-08"),
)

#: 各クラスを雛形形式で構築するための必須 context の最小セット。
MINIMAL_KWARGS: dict[type[SettingsError], dict[str, object]] = {
    IniFormatError: {"reason": "BOM がありません", "rule_id": "R1"},
    SettingsKeyConflictError: {"keys": ("Indicator", "Expert"), "rule_id": "D"},
    SettingsActivationError: {"field": "data", "rule_id": "S"},
    SettingsValueError: {"key": "Deposit", "value": "0", "rule_id": "I"},
    UnknownSettingValueError: {"key": "Model", "value": "9", "rule_id": "O"},
    UnknownSettingKeyError: {"key": "Report", "rule_id": "P"},
    UnsupportedSettingError: {
        "unsupported_id": "N-02",
        "field": "optimization",
        "value": "1",
        "reason": "最適化実行は対象外",
    },
    SettingsKeyMissingError: {"keys": ("Deposit", "Currency"), "rule_id": "H"},
}


class TestExceptionHierarchy:
    """T-13: 既存の終了コード翻訳（`except ConfigError` → 2）に載ることを固定する。"""

    @pytest.mark.parametrize(
        "error_class", [cls for cls, _ in ERROR_CLASSES], ids=[cls.__name__ for cls, _ in ERROR_CLASSES]
    )
    def test_each_error_class_is_subclass_of_config_error(self, error_class):
        # Arrange / Act / Assert
        assert issubclass(error_class, ConfigError)

    @pytest.mark.parametrize(
        "error_class", [cls for cls, _ in ERROR_CLASSES], ids=[cls.__name__ for cls, _ in ERROR_CLASSES]
    )
    def test_each_error_class_derives_from_settings_error(self, error_class):
        assert issubclass(error_class, SettingsError)

    def test_settings_error_base_derives_from_config_error(self):
        assert issubclass(SettingsError, ConfigError)
        assert issubclass(SettingsError, BacktestError)

    @pytest.mark.parametrize(
        ("error_class", "kwargs"),
        [(cls, MINIMAL_KWARGS[cls]) for cls, _ in ERROR_CLASSES],
        ids=[cls.__name__ for cls, _ in ERROR_CLASSES],
    )
    def test_instance_is_catchable_as_config_error(self, error_class, kwargs):
        # Arrange / Act
        with pytest.raises(ConfigError) as excinfo:
            raise error_class(**kwargs)
        # Assert: 捕捉した型が本系統であること（親型で捕まる＝終了コード 2 経路）
        assert isinstance(excinfo.value, error_class)


class TestErrorIdAssignment:
    """`error_id` は各クラスが自分で持ち、context へ自動付与される。"""

    @pytest.mark.parametrize(
        ("error_class", "error_id"), ERROR_CLASSES, ids=[cls.__name__ for cls, _ in ERROR_CLASSES]
    )
    def test_class_declares_its_own_error_id(self, error_class, error_id):
        assert error_class.ERROR_ID == error_id

    @pytest.mark.parametrize(
        ("error_class", "error_id"), ERROR_CLASSES, ids=[cls.__name__ for cls, _ in ERROR_CLASSES]
    )
    def test_error_id_is_added_to_context_automatically(self, error_class, error_id):
        # Arrange / Act
        err = error_class(**MINIMAL_KWARGS[error_class])
        # Assert
        assert err.context["error_id"] == error_id

    def test_error_ids_are_unique_across_the_eight_classes(self):
        ids = [error_id for _, error_id in ERROR_CLASSES]
        assert sorted(ids) == sorted(set(ids))

    def test_explicitly_supplied_error_id_is_not_overwritten(self):
        # Arrange / Act: setdefault であるため呼出側指定を優先する
        err = SettingsValueError(key="Deposit", value="0", rule_id="I", error_id="E-99")
        # Assert
        assert err.context["error_id"] == "E-99"

    def test_error_id_is_absent_when_base_class_is_constructed_directly(self):
        # Arrange / Act: 基底の ERROR_ID は空文字であり付与されない
        err = SettingsError("そのままのメッセージ")
        # Assert
        assert "error_id" not in err.context


class TestContextVocabulary:
    """`context` の語彙は**失敗種別ごとにその種別が所有する**（BASE_CONTEXT | EXTRA_CONTEXT）。"""

    #: 内部設計 §4.5.2 が列挙する context 語彙（20 語）。
    DESIGN_VOCABULARY_452: frozenset[str] = frozenset(
        {
            "path", "lineno", "line", "section", "key", "keys", "value", "expected",
            "allowed", "rule_id", "error_id", "unsupported_id", "reason", "field",
            "fields", "subject_kind", "tick_model", "has_data", "validation_errors", "tbd",
        }
    )
    #: 内部設計 §8.4.4（N-15）が要求する診断値 3 語。
    DESIGN_VOCABULARY_844: frozenset[str] = frozenset(
        {"ea_name", "requested_window", "actual_range"}
    )

    def test_union_of_all_vocabularies_matches_the_design_documents_vocabulary(self):
        """語彙の**総和**が設計文書の語彙と一致する。

        `BASE_CONTEXT` / `EXTRA_CONTEXT` への分割は設計文書に無い実装上の設計判断
        であるため、分割の内訳（実装値）ではなく総和を設計文書に照らして固定する。
        """
        # Arrange
        union = set(BASE_CONTEXT)
        for error_class, _ in ERROR_CLASSES:
            union |= set(error_class.EXTRA_CONTEXT)
        # Act / Assert
        assert union == self.DESIGN_VOCABULARY_452 | self.DESIGN_VOCABULARY_844

    def test_base_context_is_shared_by_every_error_class(self):
        # 共通語彙は分割の影響を受けない（どの種別でも使える）
        for error_class, _ in ERROR_CLASSES:
            assert BASE_CONTEXT <= error_class.allowed_context(), error_class.__name__

    def test_allowed_context_is_base_plus_the_classes_own_extras(self):
        for error_class, _ in ERROR_CLASSES:
            assert error_class.allowed_context() == BASE_CONTEXT | error_class.EXTRA_CONTEXT

    @pytest.mark.parametrize(
        ("error_class", "required_keys"),
        [
            # 内部設計 §4.5.2 の表「context 必須キー」列（設計文書由来の期待値）
            (IniFormatError, {"path", "lineno", "line", "rule_id", "error_id"}),
            (SettingsKeyConflictError, {"path", "keys", "rule_id", "error_id"}),
            (SettingsActivationError, {"field", "tick_model", "has_data", "rule_id", "error_id"}),
            (SettingsValueError, {"path", "key", "value", "expected", "rule_id", "error_id"}),
            (
                UnknownSettingValueError,
                {"path", "key", "value", "allowed", "rule_id", "error_id", "tbd"},
            ),
            (UnknownSettingKeyError, {"path", "key", "allowed", "lineno", "rule_id", "error_id"}),
            (UnsupportedSettingError, {"unsupported_id", "field", "value", "reason", "error_id"}),
            (SettingsKeyMissingError, {"path", "keys", "subject_kind", "rule_id", "error_id"}),
        ],
        ids=[cls.__name__ for cls, _ in ERROR_CLASSES],
    )
    def test_design_tables_context_keys_are_accepted_by_their_error_class(
        self, error_class, required_keys
    ):
        # Arrange / Act / Assert: 設計文書の表が求めるキーを当該種別が受理できること
        assert required_keys <= error_class.allowed_context()
        err = error_class("メッセージ", context={key: "値" for key in required_keys})
        for key in required_keys:
            assert key in err.context

    def test_unknown_context_key_raises_key_error(self):
        # Arrange / Act / Assert
        with pytest.raises(KeyError) as excinfo:
            SettingsValueError(key="Deposit", value="0", rule_id="I", typo_key="x")
        assert "typo_key" in str(excinfo.value)

    def test_unknown_context_key_is_rejected_in_parent_compatible_form_as_well(self):
        with pytest.raises(KeyError):
            SettingsValueError("メッセージ", context={"not_in_vocabulary": 1})

    def test_outer_layer_vocabulary_cannot_leak_into_a_lexer_failure(self):
        # 変換層専用語（`unsupported_id`）は字句層の失敗（E-01）では使えない
        with pytest.raises(KeyError) as excinfo:
            IniFormatError(reason="BOM がありません", rule_id="R1", unsupported_id="N-01")
        assert "unsupported_id" in str(excinfo.value)

    @pytest.mark.parametrize(
        ("error_class", "borrowed_key"),
        [
            (IniFormatError, "tick_model"),
            (SettingsKeyConflictError, "has_data"),
            (SettingsValueError, "unsupported_id"),
            (UnknownSettingKeyError, "tbd"),
            (SettingsKeyMissingError, "ea_name"),
        ],
    )
    def test_class_specific_keys_are_not_shared_with_other_classes(self, error_class, borrowed_key):
        # 他種別が所有する語を借用できないこと（語彙の所有者分離が実効であること）
        assert borrowed_key not in error_class.allowed_context()
        with pytest.raises(KeyError):
            error_class("メッセージ", context={borrowed_key: "x"})

    @pytest.mark.parametrize("vocab_key", sorted(BASE_CONTEXT))
    def test_each_base_vocabulary_key_is_accepted_by_every_class(self, vocab_key):
        # Arrange / Act: 共通語彙は全種別で受理される
        err = SettingsError("メッセージ", context={vocab_key: "値"})
        # Assert
        assert vocab_key in err.context

    @pytest.mark.parametrize(
        ("error_class", "kwargs"),
        [
            (
                SettingsActivationError,
                {"field": "data", "rule_id": "S", "tick_model": "every_tick", "has_data": False},
            ),
            (
                UnknownSettingValueError,
                {"key": "Period", "value": "M1", "rule_id": "O", "tbd": "TBD-10"},
            ),
            (
                UnsupportedSettingError,
                {
                    "unsupported_id": "N-15",
                    "field": "date_range",
                    "value": "2026.04.01",
                    "reason": "窓が適用されていない",
                    "requested_window": ["2026-04-01", "2026-05-01"],
                    "actual_range": ["2020-01-01", "2026-08-17"],
                    "ea_name": "TC24051901",
                },
            ),
        ],
        ids=["E-03", "E-05", "E-07"],
    )
    def test_owned_extra_keys_are_accepted_by_their_owner(self, error_class, kwargs):
        err = error_class(**kwargs)
        for key in kwargs:
            assert key in err.context


class TestRequiredContext:
    """雛形形式（message 省略）では必須 context キーの欠落を `KeyError` にする。"""

    @pytest.mark.parametrize(
        ("error_class", "_error_id"), ERROR_CLASSES, ids=[cls.__name__ for cls, _ in ERROR_CLASSES]
    )
    def test_missing_required_context_raises_key_error(self, error_class, _error_id):
        # Arrange: 必須キーから 1 個だけ落とす
        kwargs = dict(MINIMAL_KWARGS[error_class])
        dropped = sorted(error_class.REQUIRED_CONTEXT)[0]
        kwargs.pop(dropped)
        # Act / Assert
        with pytest.raises(KeyError) as excinfo:
            error_class(**kwargs)
        assert dropped in str(excinfo.value)

    @pytest.mark.parametrize(
        ("error_class", "_error_id"), ERROR_CLASSES, ids=[cls.__name__ for cls, _ in ERROR_CLASSES]
    )
    def test_required_context_is_declared_within_the_vocabulary(self, error_class, _error_id):
        assert error_class.REQUIRED_CONTEXT <= error_class.allowed_context()


class TestContextNormalization:
    """`context` の正規化（切り詰め・ソート済み list 化）。"""

    def test_line_is_truncated_to_200_chars(self):
        # Arrange
        long_line = "A" * 500
        # Act
        err = IniFormatError(reason="行が長すぎます", rule_id="R5", line=long_line)
        # Assert
        assert err.context["line"] == "A" * 200
        assert len(err.context["line"]) == 200

    def test_line_shorter_than_the_limit_is_kept_verbatim(self):
        # Arrange: 境界値（200 文字ちょうど）は切り詰めても同一
        line = "B" * 200
        err = IniFormatError(reason="r", rule_id="R5", line=line)
        assert err.context["line"] == line

    def test_line_at_201_chars_loses_exactly_one_char(self):
        line = "C" * 201
        err = IniFormatError(reason="r", rule_id="R5", line=line)
        assert err.context["line"] == "C" * 200

    @pytest.mark.parametrize(
        "collection",
        [("Indicator", "Expert"), {"Indicator", "Expert"}, frozenset({"Indicator", "Expert"})],
        ids=["tuple", "set", "frozenset"],
    )
    def test_collection_values_become_sorted_lists(self, collection):
        # Arrange / Act
        err = SettingsKeyConflictError(keys=collection, rule_id="D")
        # Assert: 決定論のためソート済み list（§4.5.2 規約 2）
        assert err.context["keys"] == ["Expert", "Indicator"]
        assert isinstance(err.context["keys"], list)

    def test_scalar_values_are_left_untouched(self):
        err = SettingsValueError(key="Leverage", value=0, rule_id="J", lineno=12)
        assert err.context["key"] == "Leverage"
        assert err.context["value"] == 0
        assert err.context["lineno"] == 12


class TestMessageTemplates:
    """メッセージ雛形は §4.5.2 の表と一致する（1 行・日本語）。"""

    @pytest.mark.parametrize(
        ("error_class", "kwargs", "expected"),
        [
            (IniFormatError, MINIMAL_KWARGS[IniFormatError], ".ini の書式が不正です: BOM がありません"),
            (
                SettingsKeyConflictError,
                MINIMAL_KWARGS[SettingsKeyConflictError],
                "同時に指定できないキーが存在します: Expert, Indicator",
            ),
            (
                SettingsActivationError,
                MINIMAL_KWARGS[SettingsActivationError],
                "設定の活性依存に反する実行要求です: data",
            ),
            (SettingsValueError, MINIMAL_KWARGS[SettingsValueError], "設定値が不正です: Deposit='0'"),
            (
                UnknownSettingValueError,
                MINIMAL_KWARGS[UnknownSettingValueError],
                "未知の設定値です: Model='9'",
            ),
            (UnknownSettingKeyError, MINIMAL_KWARGS[UnknownSettingKeyError], "未知の設定キーです: Report"),
            (
                UnsupportedSettingError,
                MINIMAL_KWARGS[UnsupportedSettingError],
                "本実装が対象としない設定です: N-02 (optimization='1')",
            ),
            (
                SettingsKeyMissingError,
                MINIMAL_KWARGS[SettingsKeyMissingError],
                "必須の設定キーが不足しています: Currency, Deposit",
            ),
        ],
        ids=[cls.__name__ for cls, _ in ERROR_CLASSES],
    )
    def test_template_message_matches_the_design_table(self, error_class, kwargs, expected):
        # Arrange / Act
        err = error_class(**kwargs)
        # Assert
        assert str(err) == expected

    def test_message_is_a_single_line(self):
        err = IniFormatError(**MINIMAL_KWARGS[IniFormatError])
        assert "\n" not in str(err)


class TestParentCompatibleSignature:
    """親（`ConfigError`）と同一シグネチャでの構築（既存呼出との置換可能性）。"""

    def test_message_positional_with_context_keyword(self):
        # Arrange / Act
        err = SettingsValueError("手書きのメッセージ", context={"key": "Deposit", "value": "0"})
        # Assert
        assert str(err) == "手書きのメッセージ"
        assert err.context["key"] == "Deposit"
        assert err.context["error_id"] == "E-04"

    def test_required_context_is_not_enforced_when_message_is_given(self):
        # Arrange / Act: message を与えた形式では雛形を使わないため必須検査は走らない
        err = SettingsKeyMissingError("必須キーが足りません")
        # Assert
        assert str(err) == "必須キーが足りません"
        assert err.context == {"error_id": "E-08"}

    def test_context_and_keyword_fields_are_merged(self):
        # Arrange / Act
        err = SettingsValueError("メッセージ", context={"key": "Deposit"}, value="0", rule_id="I")
        # Assert
        assert err.context["key"] == "Deposit"
        assert err.context["value"] == "0"
        assert err.context["rule_id"] == "I"

    def test_diagnostic_attributes_are_forwarded_to_the_parent(self):
        # Arrange / Act
        err = SettingsValueError(
            "メッセージ", context={"key": "Deposit"}, symbol="JP225", bar_index=7
        )
        # Assert: 既存 BacktestError の診断 4 属性に載る
        assert err.symbol == "JP225"
        assert err.bar_index == 7
        assert err.timestamp is None

    def test_context_defaults_to_error_id_only_when_nothing_supplied(self):
        err = IniFormatError("メッセージ")
        assert err.context == {"error_id": "E-01"}
