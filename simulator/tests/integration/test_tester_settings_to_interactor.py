"""`EffectiveSettings` → `build_interactor` kwargs の写像（内部設計 §8.1・API-06）。

⚠️ 本モジュールは**実装より先に書いたテスト**である（フェーズ 3 = Red）。
`simulator.main.tester_settings.kwargs_mapper` は未実装のため、現時点では
**収集エラー（ImportError）** になる（アサート失敗ではない）。

固定する仕様:
    1. §8.1 写像表（symbol / 銘柄仕様 / leverage / deposit / period / ea_name /
       tick_model / entry_price_basis / stop_out_level / execution_delay）。
    2. 沈黙上書きの禁止: `symbol` / `leverage` が束縛と食い違えば `ConfigError`。
       現行 `build_interactor` は与えられた値をそのまま使うため、ここで黙って
       束縛側へ寄せると「設定に書いた値と違う条件で走った結果」が出る。
    3. N-01（未登録 EA の沈黙フォールバック遮断）。現行 `main/__init__.py` は
       `_EA_FACTORIES.get(ea_name, _factory_tc24051901)` で未登録名を既定 TC 経路へ
       落とす（実測）。設定層は**上流で**拒否してこの沈黙誤実行を塞ぐ。
    4. 非対象 N-02 / N-03 / N-05 / N-07 / N-09 / N-11 が `UnsupportedSettingError`
       になり、`context["unsupported_id"]` が正しいこと（§4.6）。
    5. 規則 R（非 inert フィールドの `None`）が E-08、規則 S（データ有無の不整合）が E-03。
"""
from __future__ import annotations

from datetime import date

import pytest

from simulator.domain.exceptions import ConfigError
from simulator.domain.tester_settings_exceptions import (
    SettingsActivationError,
    SettingsKeyMissingError,
    UnsupportedSettingError,
)
from simulator.framework.tester_settings import tester_settings_from_mapping
from simulator.main.tester_settings.kwargs_mapper import to_interactor_kwargs
from simulator.tests.tester_settings_engine_fixtures import (
    DEFAULT_EA_PARAMS,
    SETTLEMENT_CURRENCY,
    custom_range_settings,
    engine_binding,
    jp225_leverage,
    jp225_symbol_spec,
    runnable_settings,
)
from simulator.tests.unit.tester_settings_synthetic import OMIT, synthetic_tester_map
from simulator.usecase.tester_settings.enums import TIMEFRAME_INI_LABELS, Timeframe

#: 通常モード（非 `MATH_CALCULATIONS`）は規則 S によりデータ供給が必須。
#: 写像は I/O を行わないためファイル実体は不要。
DATA_PATH = "/nonexistent/synthetic/jp225.csv"


def _kwargs(settings=None, **binding_overrides):
    return to_interactor_kwargs(
        settings if settings is not None else runnable_settings(),
        engine_binding(data_path=DATA_PATH, **binding_overrides),
    )


def _indicator_settings(**overrides):
    """Indicator テストの設定（Expert 専用 8 キーが存在しない＝F-12）。

    `Indicator` の語幹を `known_ea_names` に載る名前にし、`Period` を束縛と一致させ、
    `Model` を通常モードにして、N-01 / period 不一致 / N-05 を先に踏まないようにする。
    残るのは規則 R（非 inert フィールドの `None`）だけになる。
    """
    base = {"Indicator": "TC24051901.ex5", "Period": "Daily", "Model": "1", "Visual": OMIT}
    base.update(overrides)
    return tester_settings_from_mapping(synthetic_tester_map("indicator", **base), ())


class TestSymbolSpecMapping:
    """§8.1: 銘柄仕様は束縛（`SymbolSpec` 8 フィールド）から展開する。"""

    @pytest.mark.parametrize(
        "field",
        ["contract_size", "volume_min", "volume_max", "volume_step", "stops_level", "digits", "point_size"],
    )
    def test_symbol_spec_fields_are_expanded_verbatim(self, field):
        spec = jp225_symbol_spec()
        assert _kwargs()[field] == getattr(spec, field)

    def test_symbol_comes_from_the_binding_and_matches_the_settings(self):
        assert _kwargs()["symbol"] == "JP225"

    def test_symbol_mismatch_is_rejected_instead_of_silently_overwritten(self):
        with pytest.raises(ConfigError):
            _kwargs(symbol="EURUSD")

    def test_leverage_is_float_and_matches_the_binding(self):
        kwargs = _kwargs()
        # corpus 実測の `Leverage=10`（基本設計 §4.7 #14）と `EngineBinding.leverage`
        # （ISSUE-445 段階 3-D2 以降、権威は口座属性の注入であって `SymbolSpec` ではない）。
        assert kwargs["leverage"] == pytest.approx(jp225_leverage())
        assert isinstance(kwargs["leverage"], float)

    def test_leverage_mismatch_is_rejected(self):
        # `.ini` の `Leverage` と食い違う値を注入すれば ConfigError（沈黙上書きしない）。
        with pytest.raises(ConfigError):
            _kwargs(leverage=jp225_leverage() * 10.0)


class TestAccountAndPeriodMapping:
    """§8.1: `deposit` → `initial_deposit`、`timeframe` → `period`。"""

    def test_deposit_is_mapped_to_initial_deposit_as_float(self):
        kwargs = _kwargs(settings=runnable_settings(Deposit="139500"))
        assert kwargs["initial_deposit"] == pytest.approx(139500.0)
        assert isinstance(kwargs["initial_deposit"], float)

    def test_period_uses_the_ini_label_table(self):
        # `Daily` は corpus 実測ラベル（D-03 の「実証」行）
        assert _kwargs()["period"] == TIMEFRAME_INI_LABELS[Timeframe.D1] == "Daily"

    def test_execution_delay_is_not_passed_to_build_interactor(self):
        # §8.1: 対応する引数が無い。値は実行メタ情報（§8.5）へ記録する。
        assert "execution_delay" not in _kwargs()

    def test_data_path_comes_from_the_binding(self):
        assert _kwargs()["data_path"] == DATA_PATH

    def test_stop_out_level_defaults_to_the_current_engine_default(self):
        # 現行既定（`build_interactor` の `stop_out_level: float = 0.0`）
        assert _kwargs()["stop_out_level"] == pytest.approx(0.0)

    def test_ea_params_are_passed_through(self):
        kwargs = _kwargs()
        assert {key: kwargs[key] for key in DEFAULT_EA_PARAMS} == DEFAULT_EA_PARAMS


class TestEaNameMapping:
    """N-01: 未登録 EA の沈黙フォールバックを上流で遮断する（§4.6）。"""

    def test_ea_name_is_the_stem_of_the_subject_path(self):
        assert _kwargs()["ea_name"] == "TC24051901"

    def test_unregistered_ea_name_is_rejected(self):
        # corpus 実測の EA（`TC24051903`）は `_EA_FACTORIES` に登録が無い
        settings = runnable_settings(Expert="TC24051903.ex5")
        with pytest.raises(ConfigError):
            _kwargs(settings=settings)

    def test_traversal_style_subject_path_is_rejected_as_unregistered(self):
        # T-15 / K-18: 語幹化しても登録集合に無いので実行されない
        settings = runnable_settings(Expert="..\\..\\etc\\passwd.ex5")
        with pytest.raises(ConfigError):
            _kwargs(settings=settings)


class TestConfigOverrides:
    """§8.1: `tick_model` と `entry_price_basis` は `config_overrides` 経由。"""

    @pytest.mark.parametrize(
        ("model", "engine_id"),
        [("0", "every_tick"), ("1", "ohlc_expand"), ("2", "open_only")],
    )
    def test_tick_model_is_translated_to_the_engine_id(self, model, engine_id):
        kwargs = _kwargs(settings=runnable_settings(Model=model))
        assert kwargs["config_overrides"]["tick_model"] == engine_id

    def test_entry_price_basis_is_stated_explicitly(self):
        # §4.5.1: 建値基準を暗黙の既定に委ねない
        assert _kwargs()["config_overrides"]["entry_price_basis"] == "current_open"

    def test_binding_config_overrides_take_priority(self):
        # 銘柄仕様の権威（カタログ）が値を持つときはそれを優先する（§8.1）
        kwargs = _kwargs(config_overrides={"entry_price_basis": "current_close"})
        assert kwargs["config_overrides"]["entry_price_basis"] == "current_close"

    def test_real_ticks_requires_a_tick_store_root(self):
        # N-05: 実ティックを合成で代替しない
        settings = runnable_settings(Model="4")
        with pytest.raises(UnsupportedSettingError) as excinfo:
            _kwargs(settings=settings, tick_store_root=None)
        assert excinfo.value.context["unsupported_id"] == "N-05"

    def test_real_ticks_passes_when_the_tick_store_is_supplied(self):
        settings = runnable_settings(Model="4")
        kwargs = _kwargs(settings=settings, tick_store_root="/nonexistent/ticks")
        assert kwargs["config_overrides"]["tick_model"] == "real_ticks"
        assert kwargs["tick_store_root"] == "/nonexistent/ticks"


class TestUnsupportedSettings:
    """§4.6: 非対象は沈黙スキップせず E-07 で run を中止する。"""

    @pytest.mark.parametrize(
        ("unsupported_id", "overrides"),
        [
            # N-02: 最適化実行（値 3 は意味未確定＝TBD-04）。F-11 により Visual キーは消える。
            ("N-02", {"Optimization": "1", "Visual": OMIT, "OptimizationCriterion": "1"}),
            # N-03: forward の期間分割（分割位置が未確定＝TBD-03）
            ("N-03", {"ForwardMode": "3"}),
            # N-07: pips 建て損益（集計式が METRICS に存在しない）
            ("N-07", {"ProfitInPips": "1"}),
            # N-09: visual mode の描画（レポート責務）
            ("N-09", {"Visual": "1"}),
        ],
    )
    def test_unsupported_setting_is_rejected_with_its_id(self, unsupported_id, overrides):
        with pytest.raises(UnsupportedSettingError) as excinfo:
            _kwargs(settings=runnable_settings(**overrides))
        assert excinfo.value.context["unsupported_id"] == unsupported_id

    def test_currency_other_than_the_settlement_currency_is_rejected(self):
        # N-11: 口座通貨 = シンボル決済通貨の前提（換算レートを持たない）
        with pytest.raises(UnsupportedSettingError) as excinfo:
            _kwargs(settings=runnable_settings(Currency="USD"))
        assert excinfo.value.context["unsupported_id"] == "N-11"

    def test_matching_currency_is_accepted(self):
        # 判定源は束縛の `settlement_currency`（Settings 層に暫定表を持たない＝D-10）
        assert _kwargs(settings=runnable_settings(Currency="JPY"))["symbol"] == "JP225"

    def test_unsupported_error_is_catchable_as_config_error(self):
        # T-13: 終了コード 2 経路に載る
        with pytest.raises(ConfigError):
            _kwargs(settings=runnable_settings(Visual="1"))


class TestUnsupportedRulesAreDeclarative:
    """OCP: 非対象判定は宣言表であり、追加が分岐の書き換えを要求しない。"""

    @staticmethod
    def _declared_ids() -> set[str]:
        from simulator.main.tester_settings.unsupported import UNSUPPORTED_RULES

        if isinstance(UNSUPPORTED_RULES, dict):
            return set(UNSUPPORTED_RULES)
        return {rule.unsupported_id for rule in UNSUPPORTED_RULES}

    def test_every_enforced_id_is_declared_in_one_table(self):
        assert {"N-02", "N-03", "N-05", "N-07", "N-09", "N-11"} <= self._declared_ids()

    def test_withdrawn_n04_is_not_declared(self):
        # N-04 は v1.1 で撤回・欠番（ISSUE-387 裁定）。表に残っていたら拒否が復活する。
        assert "N-04" not in self._declared_ids()

    # --- UI 束縛の宣言（R-9）: 宣言と判定式が同じものを指すこと -----------------
    # UI は「どの選択が非対象に当たるか」を宣言（`UnsupportedRule.ui`）だけから決める。
    # 宣言が判定式（`detect`）とずれると、画面は「非対象です」と言うのに実行は通る
    # （またはその逆）。宣言を写しで確かめず、**実行段の実測**で結ぶ。

    def test_every_rule_declares_which_ini_keys_it_binds_to(self):
        from simulator.adapter.tester_settings.ini_codec import STANDARD_KEY_ORDER
        from simulator.main.tester_settings.unsupported import RULES

        for rule_id, rule in RULES.items():
            assert rule.ui is not None, f"{rule_id} に UI 束縛の宣言がありません"
            assert rule.ui.keys, f"{rule_id} がどの `.ini` キーにも紐づいていません"
            assert set(rule.ui.keys) <= set(STANDARD_KEY_ORDER), rule_id

    def test_declared_firing_tokens_really_fire_their_rule(self):
        """`on_tokens` で宣言したトークンが、実行段で当該 rule を発火させること。"""
        from simulator.main.tester_settings.unsupported import RULES, UI_TRIGGER_ON_TOKENS

        checked = []
        for rule_id, rule in RULES.items():
            if rule.ui.mode != UI_TRIGGER_ON_TOKENS or rule.detect is None:
                continue
            for key in rule.ui.keys:
                for token in rule.ui.tokens:
                    with pytest.raises(UnsupportedSettingError) as excinfo:
                        _kwargs(settings=runnable_settings(**{key: token}))
                    assert excinfo.value.context["unsupported_id"] == rule_id, (key, token)
                    checked.append((rule_id, key, token))
        assert checked, "`on_tokens` の宣言が 1 件も無い（束縛が空＝UI から発火しない）"

    def test_declared_supported_tokens_do_not_fire_their_rule(self):
        """`except_tokens` で宣言した「対象の値」では発火しないこと。"""
        from simulator.main.tester_settings.unsupported import (
            RULES,
            UI_TRIGGER_EXCEPT_TOKENS,
        )

        checked = []
        for rule_id, rule in RULES.items():
            if rule.ui.mode != UI_TRIGGER_EXCEPT_TOKENS or rule.detect is None:
                continue
            for key in rule.ui.keys:
                for token in rule.ui.tokens:
                    # 例外が出ないこと自体が主張（出れば pytest が失敗させる）
                    _kwargs(settings=runnable_settings(**{key: token}))
                    checked.append((rule_id, key, token))
        assert checked, "`except_tokens` の宣言が 1 件も無い"

    # 残る 4 形（トークン列挙で表せないもの）も、**宣言のキーを使って**判定式と結ぶ。
    # 宣言だけ書き換えても気付かない穴を残さない。

    def test_off_candidates_binding_matches_the_ea_name_check(self):
        """`off_candidates`（N-01）: 宣言キーへ候補外の値を置くと当該 rule が出る。"""
        from simulator.main.tester_settings.unsupported import RULES, UI_TRIGGER_OFF_CANDIDATES

        rule = RULES["N-01"]
        assert rule.ui.mode == UI_TRIGGER_OFF_CANDIDATES
        key = rule.ui.keys[0]
        with pytest.raises(ConfigError) as excinfo:
            _kwargs(settings=runnable_settings(**{key: "Definitely_Not_Registered.ex5"}))
        assert excinfo.value.context["unsupported_id"] == "N-01"

    def test_off_profile_binding_matches_the_settlement_currency_check(self):
        """`off_profile`（N-11）: 宣言キーへ束縛の権威値と異なる値を置くと当該 rule が出る。"""
        from simulator.main.tester_settings.unsupported import RULES, UI_TRIGGER_OFF_PROFILE

        rule = RULES["N-11"]
        assert rule.ui.mode == UI_TRIGGER_OFF_PROFILE
        key = rule.ui.keys[0]
        other = f"{SETTLEMENT_CURRENCY[:2]}X"  # 決済通貨と必ず異なる 3 文字（規則 L の書式）
        with pytest.raises(UnsupportedSettingError) as excinfo:
            _kwargs(settings=runnable_settings(**{key: other}))
        assert excinfo.value.context["unsupported_id"] == "N-11"

    def test_n15_is_declared_unevaluable_from_raw_tokens(self):
        """N-15 は生トークンでは判定できない（`none`）。

        **仕様訂正の記録（R-10・2026-08-19）**: 当初は「窓を課すのは custom 指定のときだけ」
        という**必要条件**を `on_presence` の発火条件に用いていた。しかしそれは十分条件では
        ない——下の 2 つの assert が示すとおり、宣言キーの有無は「窓を要求したか」までしか
        決めず、「その窓がエンジンへ適用されたか」は決めない。適用の成否はエンジンが返した
        バー系列を要する（`window.verify_window_applied`）。必要条件を発火条件に使うと、
        正しく適用されて完走する run にも「適用されていません」という断定が点灯した（実測）。
        よって発火条件を `none` へ訂正した。**アサーションの弱体化ではなく仕様の訂正**であり、
        偽陽性を禁じる検定を E2E 側に新設してある
        （`test_settings_ui_end_to_end.test_正しく指定したカスタム期間に偽の非対象告知を出さない`）。
        """
        from simulator.main.tester_settings.unsupported import RULES, UI_TRIGGER_NONE
        from simulator.main.tester_settings.window import resolve_data_window

        rule = RULES["N-15"]
        assert rule.ui.mode == UI_TRIGGER_NONE
        assert rule.ui.keys, "束縛キーは残す（畳んだ全一覧での所在を失わせない）"
        # 宣言キーの有無が決めるのは「窓を**要求**したか」まで（＝必要条件でしかない）。
        custom = custom_range_settings(date(2024, 1, 2), date(2024, 1, 3)).effective()
        assert resolve_data_window(custom).marketdata_window is not None
        preset = runnable_settings(Dates="0").effective()
        assert resolve_data_window(preset).marketdata_window is None
        # 適用の成否は判定式を持たない（実行後にしか分からない）ことの実証。
        assert rule.detect is None

    def test_the_on_presence_form_stays_available_for_future_rules(self):
        """`on_presence` は使う rule が無くても語彙として残す（表現力の宣言）。"""
        from simulator.main.tester_settings.unsupported import (
            UI_TRIGGER_MODES,
            UI_TRIGGER_ON_PRESENCE,
        )

        assert UI_TRIGGER_ON_PRESENCE in UI_TRIGGER_MODES

    def test_none_binding_really_cannot_fire_from_a_raw_token(self):
        """`none`（N-10）: `.ini` の生トークン（常に文字列）では判定式が発火しない。"""
        from simulator.main.tester_settings.unsupported import (
            NOT_VIOLATED,
            RULES,
            UI_TRIGGER_NONE,
        )

        rule = RULES["N-10"]
        assert rule.ui.mode == UI_TRIGGER_NONE
        key = rule.ui.keys[0]
        for token in ("JP225", "JP225,USDJPY", ""):
            effective = runnable_settings(**{key: token}).effective() if token else None
            if effective is None:
                continue  # 空文字は書式（規則 M）で先に弾かれる＝UI からは到達しない
            assert rule.detect(effective, engine_binding(data_path=DATA_PATH)) is NOT_VIOLATED


class TestActivationRules:
    """規則 R（E-08）と規則 S（E-03）。実行要求時にのみ適用される（§4.5.5）。"""

    def test_none_in_a_non_inert_field_is_rejected(self):
        # Indicator テストは Expert 専用 8 キーを持たない（F-12）。非 math で実行できない。
        with pytest.raises(SettingsKeyMissingError) as excinfo:
            _kwargs(settings=_indicator_settings())
        assert excinfo.value.context["rule_id"] == "R"

    def test_missing_data_is_rejected_for_non_math_models(self):
        with pytest.raises(SettingsActivationError) as excinfo:
            to_interactor_kwargs(runnable_settings(), engine_binding(data_path=None))
        assert excinfo.value.context["rule_id"] == "S"
        assert excinfo.value.context["error_id"] == "E-03"

    def test_loading_an_unsupported_setting_does_not_raise(self):
        # §4.5.5「検証の実行時点」: ロードは往復・検査目的でも行うため成功させる
        settings = runnable_settings(Visual="1")
        assert settings.visual is True
