"""終了コード翻訳の 3 箇所目が既存 2 箇所と一致することの突合（🟡-2 の是正）。

背景（実測）:
    `simulator.main.tester_settings.exit_codes` は、終了コードを翻訳する **3 箇所目**
    である。既存の 2 箇所は
        1. `simulator.adapter.controller.BacktestController.run`
        2. `simulator.main.run_backtest`（`build_interactor` 段階の翻訳）
    であり、いずれも本移植より前から存在する（＝改変できない）。
    `exit_codes` の docstring は「既存 2 箇所との一致はテストで固定する」と書くが、
    その突合テストは存在しなかった（`grep -rn "EXIT_CODES\\|exit_code_for" simulator/tests/`
    が 0 件）。本モジュールがその欠落を埋める。

固定する仕様:
    `ConfigError` → 2 / `BacktestError`（`ConfigError` 以外）→ 1 / 成功 → 0 の 3 点が、
    既存 2 箇所と `exit_codes` で一致する。

なぜ「実行して採取する」のか:
    既存 2 箇所の値をテストへ書き写すと、突合しているのは「テストの写し」と
    「新表」であって既存実装ではない。既存側が変わってもテストは気付かない。
    そこで既存の**翻訳経路を実際に走らせ**、戻り値を採取して新表と比較する。
    採取した値は本モジュールのどこにもリテラルとして書かない。
"""
from __future__ import annotations

import pytest

import simulator.main as simulator_main
from simulator.adapter.controller import BacktestController
from simulator.usecase.models import AccountSpec
from simulator.domain.exceptions import BacktestError, ConfigError, DataError
from simulator.main.tester_settings.exit_codes import SUCCESS_EXIT_CODE, exit_code_for

#: 突合する例外の見本。`DataError` は「`ConfigError` ではない `BacktestError`」の実例で
#: あり、サブクラス順序（`ConfigError` を先に評価する）が効いていることを測る。
CONFIG_ERROR = ConfigError("突合用（設定不正）")
BACKTEST_ERROR = DataError("突合用（実行時）")


#: `BacktestController.run` が組む `RunBacktestRequest.account`（ISSUE-445 段階 3-D3 で
#: 口座の契約 1 型に束ね、既定値をどのフィールドにも置かない）。本モジュールのスタブ
#: Interactor は request を解釈しないため、これらの値は観測される結果に一切影響しない
#: （証拠金計算を通らない）。
_UNUSED_ACCOUNT = AccountSpec(initial_deposit=0.0, leverage=1.0, stop_out_level=0.0)

class _RaisingMarketData:
    """`BacktestController.run` の翻訳を起動するための最小の `MarketDataPort` 代役。"""

    def __init__(self, error: "BaseException | None") -> None:
        self._error = error

    def load(self, source_ref, timeframe=None, period=None):
        if self._error is not None:
            raise self._error
        return []


class _NoopInteractor:
    def execute(self, request):
        return None


def _harvest_from_controller(error: "BaseException | None") -> int:
    """既存箇所 1（`BacktestController.run`）を**実行して**終了コードを採取する。"""
    controller = BacktestController(
        market_data=_RaisingMarketData(error), interactor=_NoopInteractor()
    )
    return controller.run(config=None, source_ref="-", account=_UNUSED_ACCOUNT)


def _harvest_from_run_backtest(error: "BaseException | None", monkeypatch) -> int:
    """既存箇所 2（`simulator.main.run_backtest`）を**実行して**終了コードを採取する。

    `run_backtest` は `build_interactor(**meta)` をモジュール大域から引くため、そこを
    差し替えることで「`build_interactor` 段階で例外が出た」経路をそのまま走らせられる。
    成功経路は `controller.run` の戻り値をそのまま返すので、同じ差し替えで採取できる。
    """

    def fake_build_interactor(**meta):
        if error is not None:
            raise error
        return (
            BacktestController(
                market_data=_RaisingMarketData(None), interactor=_NoopInteractor()
            ),
            _SuccessRequest(),
        )

    monkeypatch.setattr(simulator_main, "build_interactor", fake_build_interactor)
    exit_code, _result = simulator_main.run_backtest(data_path="-", output_dir=None)
    return exit_code


class _SuccessRequest:
    """`run_backtest` が成功経路で参照する 4 属性だけを持つ最小の代役。"""

    config = None
    symbol_spec = None
    initial_deposit = 0.0
    stop_out_level = 0.0


class TestParityWithTheExistingTranslations:
    """3 箇所目（`exit_codes`）が既存 2 箇所と同じ値を返すこと。"""

    @pytest.mark.parametrize(
        "error", [CONFIG_ERROR, BACKTEST_ERROR], ids=["ConfigError", "BacktestError"]
    )
    def test_the_controller_translation_matches_the_settings_table(self, error):
        # Arrange / Act: 既存実装を実行して採取する（値を書き写さない）
        harvested = _harvest_from_controller(error)
        # Assert
        assert exit_code_for(error) == harvested

    @pytest.mark.parametrize(
        "error", [CONFIG_ERROR, BACKTEST_ERROR], ids=["ConfigError", "BacktestError"]
    )
    def test_the_run_backtest_translation_matches_the_settings_table(self, error, monkeypatch):
        harvested = _harvest_from_run_backtest(error, monkeypatch)
        assert exit_code_for(error) == harvested

    def test_the_success_code_matches_the_controller(self):
        assert SUCCESS_EXIT_CODE == _harvest_from_controller(None)

    def test_the_success_code_matches_run_backtest(self, monkeypatch):
        assert SUCCESS_EXIT_CODE == _harvest_from_run_backtest(None, monkeypatch)


class TestHarvestedValuesAreDistinguishable:
    """採取値が 3 通りに割れていること（＝突合に検出力があること）。

    `ConfigError` と `BacktestError` の採取値が同じなら、上の突合は「順序が壊れても
    通る」テストになる。3 値が相異なることを先に固定して、その退化を防ぐ。
    """

    def test_the_controller_yields_three_distinct_codes(self):
        codes = (
            _harvest_from_controller(None),
            _harvest_from_controller(CONFIG_ERROR),
            _harvest_from_controller(BACKTEST_ERROR),
        )
        assert len(set(codes)) == 3

    def test_config_error_is_not_translated_as_a_plain_backtest_error(self):
        # `ConfigError` は `BacktestError` のサブクラス。順序を誤ると両者が同値になる。
        assert isinstance(CONFIG_ERROR, BacktestError)
        assert exit_code_for(CONFIG_ERROR) != exit_code_for(BACKTEST_ERROR)
