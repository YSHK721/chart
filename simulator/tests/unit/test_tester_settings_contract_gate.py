"""契約不変ガード（内部設計 §9.2 T-12・§8.2 D-09 の通過条件・§6 API-06）。

⚠️ 本モジュールは**実装より先に書いたテスト**である（フェーズ 3 = Red）。
`simulator.main.tester_settings.kwargs_mapper` は未実装のため、現時点では
**収集エラー（ImportError）** になる（アサート失敗ではない）。

固定する契約:
    1. `TICK_MODEL_IDS` が 4 値・順序不変（§8.2「既存 4 モード bit-exact 不変」の通過条件）。
    2. `required_backtest_keys()` に `data_path` が含まれる（＝`build_interactor` 経由の
       通常モードはデータ供給が必須。`MATH_CALCULATIONS` が別経路になる理由そのもの）。
    3. `to_interactor_kwargs` の出力キー集合が
       ⊆ `allowed_backtest_keys()` かつ ⊇ `required_backtest_keys()`（API-06 事後条件）。
    4. `ea_params` が必要キーを供給しないとき E-08（`SettingsKeyMissingError`）で
       Fail-Stop する（推測値を発明しない）。
    5. `TICK_MODEL_ENGINE_IDS`（§4.2.2）の値が `TICK_MODEL_IDS` の部分集合である。

キー表を手書きしない（§6 「`composition_root_jobs.py` と同じ単一ソース＝シグネチャから
導出。手書きのキー表を持たない」）。本テストは `allowed_backtest_keys` /
`required_backtest_keys` を import して集合演算で判定する。
"""
from __future__ import annotations

import pytest

from simulator.adapter.execution.tick_model_registry import TICK_MODEL_IDS
from simulator.domain.tester_settings_exceptions import SettingsKeyMissingError
from simulator.main.tester_settings.kwargs_mapper import to_interactor_kwargs
from simulator.sim_ui.main.composition_root_jobs import (
    allowed_backtest_keys,
    required_backtest_keys,
)
from simulator.tests.tester_settings_engine_fixtures import engine_binding, runnable_settings
from simulator.usecase.tester_settings.enums import TICK_MODEL_ENGINE_IDS

#: 通常モード（非 `MATH_CALCULATIONS`）は規則 S によりデータ供給が必須。
#: ファイル実体は不要（本モジュールは写像だけを検査し、エンジンを起動しない）。
_DATA_PATH = "/nonexistent/synthetic/jp225.csv"


def _kwargs(**binding_overrides):
    return to_interactor_kwargs(
        runnable_settings(),
        engine_binding(data_path=_DATA_PATH, **binding_overrides),
    )


class TestTickModelRegistryContract:
    """D-09 通過条件: 既存レジストリを 1 文字も変えずに `MATH_CALCULATIONS` を足す。"""

    def test_tick_model_ids_are_the_four_known_models_in_order(self):
        assert TICK_MODEL_IDS == ("every_tick", "ohlc_expand", "open_only", "real_ticks")

    def test_settings_engine_ids_are_a_subset_of_the_registry(self):
        # §4.2.2: `MATH_CALCULATIONS` はレジストリに id を持たない（別経路＝§8.2）
        assert set(TICK_MODEL_ENGINE_IDS.values()) <= set(TICK_MODEL_IDS)

    def test_math_calculations_has_no_engine_id(self):
        from simulator.usecase.tester_settings.enums import TickModel

        assert TickModel.MATH_CALCULATIONS not in TICK_MODEL_ENGINE_IDS


class TestBuildInteractorKeyContract:
    """§6 API-06 の事後条件。キー表は `composition_root_jobs` が単一ソース。"""

    def test_data_path_is_a_required_backtest_key(self):
        # `MATH_CALCULATIONS` を `build_interactor` 経由にできない根拠（§8.2.2 代替案 A）
        assert "data_path" in required_backtest_keys()

    def test_required_keys_are_a_subset_of_allowed_keys(self):
        # 単一ソース（同一シグネチャ）から導出されている前提の健全性
        assert required_backtest_keys() <= allowed_backtest_keys()

    def test_output_keys_are_within_the_allowed_set(self):
        assert set(_kwargs()) <= allowed_backtest_keys()

    def test_output_keys_cover_every_required_argument(self):
        # 必須引数の欠けた kwargs を返すと `build_interactor` が TypeError で遅く落ちる
        assert set(_kwargs()) >= required_backtest_keys()

    def test_output_never_contains_injection_only_keys(self):
        # `strategy_decorator` / `strategy_override` は Port 実体であり設定層は供給しない
        assert {"strategy_decorator", "strategy_override"} & set(_kwargs()) == set()


class TestEaParamsAreRequiredNotInvented:
    """`ea_params` の不足は Fail-Stop（E-08）。既定値を発明しない。

    `required_backtest_keys()` のうち `ma_period` / `ma_method` / `lot_size` /
    `stop_loss_points` / `take_profit_points` は `EffectiveSettings` にも
    `EA_INPUT_BINDINGS`（初期空・§4.4.1）にも供給源が無い。ここで既定値を捏造すると
    「設定に書かれていない値」で口座計算が走る（§4.5.5 規則 A が塞いだ経路と同種の穴）。
    """

    def test_missing_ea_params_raise_key_missing_error(self):
        with pytest.raises(SettingsKeyMissingError) as excinfo:
            _kwargs(ea_params={})
        assert excinfo.value.context["error_id"] == "E-08"

    @pytest.mark.parametrize(
        "dropped",
        ["ma_period", "ma_method", "lot_size", "stop_loss_points", "take_profit_points"],
    )
    def test_each_missing_ea_param_is_reported(self, dropped):
        from simulator.tests.tester_settings_engine_fixtures import DEFAULT_EA_PARAMS

        partial = {k: v for k, v in DEFAULT_EA_PARAMS.items() if k != dropped}
        with pytest.raises(SettingsKeyMissingError) as excinfo:
            _kwargs(ea_params=partial)
        # 不足キーが診断に載ること（沈黙補完でないことの証拠）
        assert dropped in excinfo.value.context["keys"]
