"""A-1: `MATH_CALCULATIONS` をレジストリへ統合し実行経路を一本化する（ISSUE-397）。

⚠️ 本モジュールは**実装より先に書いたテスト**である（Red）。

固定する仕様（既知の限界 L-1 / L-5 の解消）:
    L-1: `POST /sim/jobs` の投入経路（`allowed_backtest_keys` / `required_backtest_keys`
         を満たす `backtest` kwargs → 子プロセスの `run_backtest(**meta)`）から
         `Math calculations` を投入でき、終了コード 0・`trades == 0` で完走する。
    L-5: math 実行時の `BacktestConfig.tick_model` が Settings 層の語彙
         （``"math_calculations"``）と一致する（従来は既定 ``"every_tick"`` のまま）。

規則として表現されていること（`if math` の分岐を増やさない・OCP）:
    規則 1（データ供給要否）: バー系列を供給するか否かは `TickModelSpec.requires_market_data`
        が宣言し、`build_interactor` は EA ファクトリの選択でそれを実現する。
    規則 2（inert）: `.ini` フィールドが inert のとき、対応する引数は `EngineBinding` の
        値を採る（ISSUE-397 裁定）。
    規則 3（窓）: `date_range` が inert のとき `resolve_data_window` は空窓を返す（例外にしない）。
    規則 4（注入由来の ``None``）: 注入束由来の引数の ``None`` は「供給しない」という
        呼出側の宣言であり、規則 R（必須値の充足）の欠落として扱わない。
"""
from __future__ import annotations

import pytest

from simulator.adapter.execution.null_tick_model import NullTickModel
from simulator.adapter.execution.tick_model_registry import (
    TICK_MODEL_IDS,
    TICK_MODEL_REGISTRY,
)
from simulator.main.tester_settings.kwargs_mapper import to_interactor_kwargs
from simulator.tests.tester_settings_engine_fixtures import engine_binding, runnable_settings
from simulator.usecase.tester_settings.enums import TICK_MODEL_ENGINE_IDS, TickModel

#: `Model=3`（`MATH_CALCULATIONS`）。
_MATH_MODEL = "3"

#: 既存 4 モードの id（記載順）。A-1 で増える id は**末尾**に付く。
_KNOWN_FOUR = ("every_tick", "ohlc_expand", "open_only", "real_ticks")


def _math_settings():
    return runnable_settings(Model=_MATH_MODEL)


def _math_kwargs(**binding_overrides):
    return to_interactor_kwargs(_math_settings(), engine_binding(data_path=None, **binding_overrides))


class TestRegistryEntry:
    """レジストリへの 5 件目の追加（既存 4 件は 1 文字も変えない）。"""

    def test_math_calculations_is_registered(self):
        assert "math_calculations" in TICK_MODEL_REGISTRY

    def test_known_four_keep_their_order_at_the_head(self):
        # 既存 4 値の記載順は byte 不変・追加は末尾（config_loader の Literal 記載順）。
        assert TICK_MODEL_IDS[:4] == _KNOWN_FOUR

    def test_known_four_all_require_market_data(self):
        for key in _KNOWN_FOUR:
            assert TICK_MODEL_REGISTRY[key].requires_market_data is True

    def test_math_calculations_requires_no_market_data(self):
        spec = TICK_MODEL_REGISTRY["math_calculations"]
        assert spec.requires_market_data is False
        # 実ティック分岐へは行かない（既存 else がそのまま合成器を構築する）。
        assert spec.requires_real_ticks is False

    def test_math_calculations_builds_the_null_tick_model(self):
        spec = TICK_MODEL_REGISTRY["math_calculations"]
        assert isinstance(spec.synthetic_builder("ohlc"), NullTickModel)

    def test_config_loader_accepts_the_math_id(self):
        from simulator.framework.config_loader import load_config

        assert load_config({"tick_model": "math_calculations"}).tick_model == "math_calculations"


class TestSettingsVocabularyIsTotal:
    """§4.2.2: Settings 層の `Model` は全値がエンジン id を持つ（別経路が消えたため）。"""

    def test_math_calculations_has_an_engine_id(self):
        assert TICK_MODEL_ENGINE_IDS[TickModel.MATH_CALCULATIONS] == "math_calculations"

    def test_every_tick_model_value_maps_to_a_registered_id(self):
        assert set(TICK_MODEL_ENGINE_IDS) == set(TickModel)
        assert set(TICK_MODEL_ENGINE_IDS.values()) <= set(TICK_MODEL_IDS)


class TestMappingRules:
    """`if math` を作らずに写像が成立すること（規則 2・3・4）。"""

    def test_math_kwargs_satisfy_the_submission_key_contract(self):
        from simulator.sim_ui.main.composition_root_jobs import (
            allowed_backtest_keys,
            required_backtest_keys,
        )

        keys = set(_math_kwargs())
        assert keys <= allowed_backtest_keys()
        assert keys >= required_backtest_keys()

    def test_engine_identifiers_come_from_the_binding(self):
        kwargs = _math_kwargs()
        binding = engine_binding(data_path=None)
        assert kwargs["symbol"] == binding.symbol
        assert kwargs["period"] == binding.period
        assert kwargs["leverage"] == binding.leverage

    def test_data_path_is_supplied_as_none(self):
        # 規則 4: 注入束由来の ``None`` は欠落ではない（規則 S が整合を担保済み）。
        assert _math_kwargs()["data_path"] is None

    def test_initial_deposit_is_the_inert_value_not_an_estimate(self):
        from simulator.main.tester_settings.math_calculations import INERT_DEPOSIT

        assert _math_kwargs()["initial_deposit"] == INERT_DEPOSIT

    def test_window_is_empty_instead_of_raising(self):
        from simulator.main.tester_settings.window import resolve_data_window

        window = resolve_data_window(_math_settings().effective())
        assert window.marketdata_window is None
        assert window.tick_start is None and window.tick_end is None

    def test_config_overrides_carry_the_settings_vocabulary(self):
        # L-5: エンジンの tick_model が Settings の語彙と一致する。
        assert _math_kwargs()["config_overrides"]["tick_model"] == "math_calculations"


class TestSubmissionPathRunsMath:
    """L-1: 投入経路（`run_backtest(**meta)`）で math が完走する。"""

    @pytest.fixture()
    def run(self):
        from simulator.main import run_backtest

        return run_backtest(**_math_kwargs())

    def test_exit_code_is_zero(self, run):
        assert run[0] == 0

    def test_no_trades_are_produced(self, run):
        assert run[1].stats.trades == 0
        assert len(run[1].trades) == 0

    def test_no_bars_are_loaded(self):
        from simulator.main import build_interactor

        _controller, request = build_interactor(**_math_kwargs())
        assert list(request.bars) == []

    def test_engine_config_tick_model_matches_the_settings_word(self):
        from simulator.main import build_interactor

        _controller, request = build_interactor(**_math_kwargs())
        assert request.config.tick_model == "math_calculations"


class TestSubmissionGateAcceptsMath:
    """受付（`POST /sim/jobs`）の必須キー検査を math の kwargs が満たすこと。"""

    def test_required_keys_are_all_present_in_the_submission(self):
        from simulator.sim_ui.main.composition_root_jobs import required_backtest_keys

        assert required_backtest_keys() - set(_math_kwargs()) == set()

    def test_data_path_stays_a_required_key(self):
        # 通常モードの「必須キー欠落」検査を弱めない（math は明示的に ``None`` を渡す）。
        from simulator.sim_ui.main.composition_root_jobs import required_backtest_keys

        assert "data_path" in required_backtest_keys()


class TestSingleExecutionPath:
    """経路の一本化: 実行 A / 実行 B が同一の実行段を通ること。"""

    def test_run_from_settings_runs_math_without_a_dedicated_branch(self):
        from simulator.main.tester_settings.run_from_settings import run_from_settings

        exit_code, result, metadata = run_from_settings(
            _math_settings(), engine_binding(data_path=None)
        )
        assert exit_code == 0
        assert result.stats.trades == 0
        assert metadata.tick_model == "math_calculations"

    def test_math_facade_delegates_to_the_shared_stage(self):
        from simulator.main.tester_settings import math_calculations

        source = math_calculations.__file__
        with open(source, encoding="utf-8") as handle:
            text = handle.read()
        # 追加専用経路の実体（Interactor の直接組立）が残っていないこと。
        assert "RunBacktestInteractor(" not in text
        assert "RunBacktestRequest(" not in text
