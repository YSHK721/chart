"""BacktestController テスト（cycle B / B5）。

構築済 BacktestConfig + データソース参照を受け、MarketDataPort 経由でロード→
RunBacktestInputBoundary へ委譲→例外を終了コードへ翻訳する（DESIGN §9.4）:
    成功 → 0 / BacktestError → 1 / ConfigError → 2

config.yaml→BacktestConfig の pydantic 検証は framework 層責務（Section 4）のため、
controller は構築済 BacktestConfig を受け取る（yaml パースは申し送り）。
MarketDataPort / RunBacktestInputBoundary はスタブする。
"""
from __future__ import annotations

from simulator.domain.exceptions import BacktestError, ConfigError, DataError
from simulator.usecase.ports import MarketDataPort, RunBacktestInputBoundary


class _StubMarketData(MarketDataPort):
    def __init__(self, *, bars=None, raises=None):
        self._bars = bars or []
        self._raises = raises
        self.loaded = []

    def load(self, source_ref, timeframe=None, period=None):
        self.loaded.append(source_ref)
        if self._raises is not None:
            raise self._raises
        return self._bars


class _StubInteractor(RunBacktestInputBoundary):
    def __init__(self, *, result=None, raises=None):
        self._result = result
        self._raises = raises
        self.executed = []

    def execute(self, request):
        self.executed.append(request)
        if self._raises is not None:
            raise self._raises
        return self._result


def _controller(market_data, interactor):
    from simulator.adapter.controller import BacktestController

    return BacktestController(market_data=market_data, interactor=interactor)


_CONFIG = object()  # 構築済 BacktestConfig のスタンドイン（controller は検証しない）


def test_run_returns_zero_on_success():
    md = _StubMarketData(bars=["bar0", "bar1"])
    ic = _StubInteractor(result=object())
    ctrl = _controller(md, ic)

    code = ctrl.run(_CONFIG, "data.csv")

    assert code == 0
    assert ic.executed  # Interactor へ委譲した
    assert md.loaded == ["data.csv"]  # MarketDataPort 経由でロードした


def test_run_returns_two_on_config_error():
    md = _StubMarketData(bars=[])
    ic = _StubInteractor(raises=ConfigError("bad config"))
    ctrl = _controller(md, ic)

    code = ctrl.run(_CONFIG, "data.csv")

    assert code == 2


def test_run_returns_one_on_backtest_error():
    md = _StubMarketData(bars=[])
    ic = _StubInteractor(raises=BacktestError("run failed"))
    ctrl = _controller(md, ic)

    code = ctrl.run(_CONFIG, "data.csv")

    assert code == 1


def test_run_returns_one_on_data_error_during_load():
    # DataError は BacktestError 配下 → 1（load 段階の例外も翻訳される）
    md = _StubMarketData(raises=DataError("load failed"))
    ic = _StubInteractor(result=object())
    ctrl = _controller(md, ic)

    code = ctrl.run(_CONFIG, "data.csv")

    assert code == 1


def test_config_error_caught_before_backtest_error():
    # ConfigError は BacktestError サブクラスだが先に捕捉され 2 を返す（順序依存）
    md = _StubMarketData(bars=[])
    ic = _StubInteractor(raises=ConfigError("init invalid"))
    ctrl = _controller(md, ic)

    code = ctrl.run(_CONFIG, "data.csv")

    assert code == 2  # 1 ではない（ConfigError 専用コード）
