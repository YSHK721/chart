"""実行 facade `run_from_settings` の契約（内部設計 §6 実行 A・§8.4.3・§8.5）。

⚠️ 本モジュールは**実装より先に書いたテスト**である（フェーズ 3 = Red）。
`simulator.main.tester_settings.run_from_settings` は未実装のため、現時点では
**収集エラー（ImportError）** になる（アサート失敗ではない）。

固定する仕様:
    1. 戻り値は `(exit_code, BacktestResult | None, TesterRunMetadata)` の 3 要素。
       §8.5 のメタ情報は「結果と併せて呼出側へ返す」ことが定められており、返らなければ
       到達不能な宣言になる（`execution_delay` は `build_interactor` に対応引数が無く、
       メタが唯一の伝達経路＝§8.1 最終行）。
    2. `build_interactor` が返した request を検証し、**その同一 request を実行する**。
       実測: `BacktestController.run` は `market_data.load` を再実行して
       `RunBacktestRequest` を組み直し、`trading_start` を渡さない
       （`simulator/adapter/controller.py`）。controller 経由にすると §8.4 で解決した
       `trading_start` が実行時に消える。
    3. `trading_start` は実行時まで生き、かつ `bar.time` と比較可能な型であること
       （`run_backtest` は `bar.time < trading_start` を評価する）。型が食い違えば
       TypeError で落ちるため、正常終了そのものが検証になる。
"""
from __future__ import annotations

import inspect
from datetime import date
from importlib import import_module

import pytest

from simulator.main.tester_settings.run_from_settings import run_from_settings
from simulator.main.tester_settings.window import resolve_data_window

#: `simulator.main.tester_settings.__init__` が関数 `run_from_settings` を再エクスポート
#: しており、`from ... import run_from_settings` では**関数**が返る（属性が影になる）。
#: monkeypatch 対象はモジュールなので `import_module` で実体を取る。
run_module = import_module("simulator.main.tester_settings.run_from_settings")
from simulator.tests.tester_settings_engine_fixtures import (
    custom_range_settings,
    daily_epochs,
    engine_binding,
    runnable_settings,
    write_comma_csv,
)
from simulator.usecase.run_backtest import RunBacktestInteractor

FIRST_DAY = date(2024, 1, 1)
BAR_DAYS = 5
FROM_DATE = date(2024, 1, 2)
TO_DATE = date(2024, 1, 3)

#: 合成入力の `ExecutionMode`（corpus 実測値・golden fixture の delays_ms=50 と一致）。
DELAY_50MS = 50


@pytest.fixture()
def csv_path(tmp_path):
    return write_comma_csv(tmp_path / "jp225_daily.csv", daily_epochs(FIRST_DAY, BAR_DAYS))


@pytest.fixture()
def executed_requests(monkeypatch):
    """`RunBacktestInteractor.execute` が受け取った request を記録する。"""
    captured: list = []
    original = RunBacktestInteractor.execute

    def spy(self, request):
        captured.append(request)
        return original(self, request)

    monkeypatch.setattr(RunBacktestInteractor, "execute", spy)
    return captured


@pytest.fixture()
def verified_requests(monkeypatch):
    """`verify_window_applied` が受け取った request を記録する（§8.4.3 の検証点）。"""
    captured: list = []
    original = run_module.verify_window_applied

    def spy(request, window, *args, **kwargs):
        captured.append(request)
        return original(request, window, *args, **kwargs)

    monkeypatch.setattr(run_module, "verify_window_applied", spy)
    return captured


def _binding(csv_path, **overrides):
    return engine_binding(data_path=str(csv_path), **overrides)


class TestSignature:
    """§6 実行 A の確定シグネチャ。"""

    def test_output_dir_argument_is_absent(self):
        # 消費者が存在しない引数を facade に残さない（YAGNI）
        assert "output_dir" not in inspect.signature(run_from_settings).parameters

    def test_returns_three_elements(self, csv_path):
        returned = run_from_settings(runnable_settings(Dates="0"), _binding(csv_path))
        assert len(returned) == 3


class TestNormalRun:
    """通常モード（`Model=1`＝`ohlc_expand`）の 1 パス実行。"""

    def test_exit_code_is_zero_on_success(self, csv_path):
        exit_code, result, _ = run_from_settings(
            custom_range_settings(FROM_DATE, TO_DATE), _binding(csv_path)
        )
        assert exit_code == 0
        assert result is not None

    def test_metadata_carries_the_execution_delay(self, csv_path):
        # §8.1 最終行: `execution_delay` は `build_interactor` に渡さない。
        # メタに載らなければ、設定した遅延がどこにも現れない（沈黙）。
        _, _, metadata = run_from_settings(runnable_settings(Dates="0"), _binding(csv_path))
        assert metadata.execution_delay == DELAY_50MS

    def test_measured_delay_is_not_reported_as_approximate(self, csv_path):
        # §4.5.3（ISSUE-387 裁定）: 50 ms は MT5 実走を bit-exact 再現済みの実測組
        _, _, metadata = run_from_settings(runnable_settings(Dates="0"), _binding(csv_path))
        assert metadata.approximate is False

    def test_every_tick_is_reported_as_approximate_with_its_reason(self, csv_path):
        # N-06: MT5 の内挿仕様は非公開。実行は可だが近似であることを隠さない。
        _, _, metadata = run_from_settings(
            runnable_settings(Dates="0", Model="0"), _binding(csv_path)
        )
        assert metadata.approximate is True
        assert "N-06" in metadata.approximation_reasons


class TestExecutedRequestIsTheVerifiedOne:
    """§8.4.3: 検証した request をそのまま実行する（controller で組み直さない）。"""

    def test_the_verified_request_object_is_the_executed_one(
        self, csv_path, executed_requests, verified_requests
    ):
        run_from_settings(custom_range_settings(FROM_DATE, TO_DATE), _binding(csv_path))
        assert len(verified_requests) == 1
        assert len(executed_requests) == 1
        assert executed_requests[0] is verified_requests[0]

    def test_trading_start_survives_until_execution(self, csv_path, executed_requests):
        settings = custom_range_settings(FROM_DATE, TO_DATE)
        run_from_settings(settings, _binding(csv_path))
        # `BacktestController.run` 経由だと trading_start は落ちる（実測）。
        # 期待値は §8.4 の解決結果そのもの（値の当て推量をしない）。
        expected = resolve_data_window(settings.effective()).trading_start
        assert executed_requests[0].trading_start == expected

    def test_market_data_is_not_loaded_twice(self, csv_path, executed_requests):
        # 再ロードすると窓解決後の request が捨てられる。実行は 1 回だけ。
        run_from_settings(custom_range_settings(FROM_DATE, TO_DATE), _binding(csv_path))
        assert len(executed_requests) == 1

    def test_controller_run_is_not_the_execution_path(self, csv_path, monkeypatch):
        from simulator.adapter.controller import BacktestController

        def forbidden(*args, **kwargs):  # pragma: no cover - 呼ばれたら失敗させる
            raise RuntimeError("BacktestController.run は trading_start を捨てるため使わない")

        monkeypatch.setattr(BacktestController, "run", forbidden)
        exit_code, _, _ = run_from_settings(
            custom_range_settings(FROM_DATE, TO_DATE), _binding(csv_path)
        )
        assert exit_code == 0


class TestMathCalculationsBranch:
    """§8.2.1: `MATH_CALCULATIONS` は `build_interactor` を経由しない別経路へ分岐する。"""

    def test_math_model_runs_without_data(self):
        exit_code, result, metadata = run_from_settings(
            runnable_settings(Model="3"), engine_binding(data_path=None)
        )
        assert exit_code == 0
        assert result is not None
        assert len(result.trades) == 0
        assert metadata.tick_model == "math_calculations"


class TestFailStopBeforeRun:
    """§6 実行 A の事後条件「終了コードは既存規約（成功 0 / ConfigError 2 /
    BacktestError 1）」。非対象は run を始めずに 2 を返す。

    ⚠️ §6 の同じ行は送出例外欄に「E-03,07,08 / `ConfigError`」も掲げており、
    「返す」と「送出する」が両立していない（実装フェーズへの申し送り事項）。
    本テストは数値を明示している事後条件側（0 / 2 / 1）を採り、
    `adapter/controller.py:59-63` の既存規約と同型であることを固定する。
    """

    def test_unsupported_setting_returns_exit_code_two(self, csv_path, executed_requests):
        # N-09（visual mode）。Fail-Stop であり run を始めない。
        exit_code, result, _ = run_from_settings(
            runnable_settings(Dates="0", Visual="1"), _binding(csv_path)
        )
        assert exit_code == 2
        assert result is None
        assert executed_requests == []

    def test_metadata_is_returned_even_when_the_run_is_refused(self, csv_path):
        # 3 要素契約は失敗経路でも崩れない（呼出側が unpack で落ちない）
        returned = run_from_settings(runnable_settings(Dates="0", Visual="1"), _binding(csv_path))
        assert len(returned) == 3
        assert returned[2] is not None
