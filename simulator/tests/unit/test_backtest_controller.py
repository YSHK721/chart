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
from simulator.usecase.models import AccountSpec
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

#: `BacktestController.run` が組む `RunBacktestRequest.account`（ISSUE-445 段階 3-D3 で
#: 口座の契約 1 型に束ね、既定値をどのフィールドにも置かない）。本モジュールのスタブ
#: Interactor は request を解釈しないため、これらの値は観測される結果に一切影響しない
#: （証拠金計算を通らない）。
_UNUSED_ACCOUNT = AccountSpec(initial_deposit=0.0, leverage=1.0, stop_out_level=0.0)


def test_run_returns_zero_on_success():
    md = _StubMarketData(bars=["bar0", "bar1"])
    ic = _StubInteractor(result=object())
    ctrl = _controller(md, ic)

    code = ctrl.run(_CONFIG, "data.csv", symbol_spec=None, account=_UNUSED_ACCOUNT)

    assert code == 0
    assert ic.executed  # Interactor へ委譲した
    assert md.loaded == ["data.csv"]  # MarketDataPort 経由でロードした


def test_run_returns_two_on_config_error():
    md = _StubMarketData(bars=[])
    ic = _StubInteractor(raises=ConfigError("bad config"))
    ctrl = _controller(md, ic)

    code = ctrl.run(_CONFIG, "data.csv", symbol_spec=None, account=_UNUSED_ACCOUNT)

    assert code == 2


def test_run_returns_one_on_backtest_error():
    md = _StubMarketData(bars=[])
    ic = _StubInteractor(raises=BacktestError("run failed"))
    ctrl = _controller(md, ic)

    code = ctrl.run(_CONFIG, "data.csv", symbol_spec=None, account=_UNUSED_ACCOUNT)

    assert code == 1


def test_run_returns_one_on_data_error_during_load():
    # DataError は BacktestError 配下 → 1（load 段階の例外も翻訳される）
    md = _StubMarketData(raises=DataError("load failed"))
    ic = _StubInteractor(result=object())
    ctrl = _controller(md, ic)

    code = ctrl.run(_CONFIG, "data.csv", symbol_spec=None, account=_UNUSED_ACCOUNT)

    assert code == 1


def test_config_error_caught_before_backtest_error():
    # ConfigError は BacktestError サブクラスだが先に捕捉され 2 を返す（順序依存）
    md = _StubMarketData(bars=[])
    ic = _StubInteractor(raises=ConfigError("init invalid"))
    ctrl = _controller(md, ic)

    code = ctrl.run(_CONFIG, "data.csv", symbol_spec=None, account=_UNUSED_ACCOUNT)

    assert code == 2  # 1 ではない（ConfigError 専用コード）


# ---- 契約引数の既定値禁止（ISSUE-445 段階 3-D3・RC-1 残渣の撤去を機械で固定する）----


def contract_parameters_carrying_a_default(function, request_type) -> "frozenset[str]":
    """``function`` の引数のうち「契約引数かつ既定値を持つもの」を返す（純関数）。

    契約引数の同定にリテラルの一覧を置かない: ``request_type``（``RunBacktestRequest``）の
    フィールド名と**同名の引数**が契約引数である。`run()` はそれらをそのまま
    `RunBacktestRequest` へ載せるため、既定値はそのまま「誰も指定していない契約」になる。
    取得パラメータ（`timeframe` / `period`）は当該 DTO のフィールドではないため対象外で
    あり、`None` が `MarketDataPort.load` の契約上の意味を持つ。
    """
    import dataclasses
    import inspect

    fields = {f.name for f in dataclasses.fields(request_type)}
    params = inspect.signature(function).parameters
    return frozenset(
        name
        for name, param in params.items()
        if name in fields and param.default is not inspect.Parameter.empty
    )


def _contract_parameters(function, request_type) -> "frozenset[str]":
    import dataclasses
    import inspect

    fields = {f.name for f in dataclasses.fields(request_type)}
    return frozenset(set(inspect.signature(function).parameters) & fields)


class TestTheContractArgumentGateDetectsAndOnlyDetects:
    """負の対照（落ちないゲートは無価値であるため恒久テストとして置く）。"""

    def test_it_flags_a_defaulted_contract_parameter(self):
        from simulator.usecase.run_backtest import RunBacktestRequest

        def probe(config, source_ref, *, timeframe=None, symbol_spec=None, account):
            """`symbol_spec` に既定を戻した形（段階 3-D2 までの `run()`）。"""

        found = contract_parameters_carrying_a_default(probe, RunBacktestRequest)
        assert found == {"symbol_spec"}

    def test_it_stays_silent_on_the_corrected_shape(self):
        from simulator.usecase.run_backtest import RunBacktestRequest

        def probe(config, source_ref, *, timeframe=None, symbol_spec, account):
            """是正後の形。取得パラメータの既定は偽陽性にしない。"""

        assert contract_parameters_carrying_a_default(probe, RunBacktestRequest) == frozenset()


def test_run_places_no_default_on_the_contract_arguments():
    """`BacktestController.run` は契約引数に既定値を持たない（RC-1 残渣の再発防止）。

    段階 3-D2 までの `symbol_spec=None` / `initial_deposit=0.0` / `stop_out_level=0.0` は
    「本メソッドが人の書いた値・実行不能な契約を黙って供給する」形だった。口座属性は
    段階 3-D3 で `AccountSpec` へ束ねた際に構造的に消え、`symbol_spec` の既定も撤去した。
    """
    from simulator.adapter.controller import BacktestController
    from simulator.usecase.run_backtest import RunBacktestRequest

    covered = _contract_parameters(BacktestController.run, RunBacktestRequest)
    assert len(covered) > 1, (
        f"契約引数の同定が空振りしている（走査対象: {sorted(covered)}）"
    )
    offenders = contract_parameters_carrying_a_default(
        BacktestController.run, RunBacktestRequest
    )
    assert not offenders, (
        f"run() の契約引数が既定値を持っている: {sorted(offenders)}。"
        "呼出側が契約を指定しないまま run が通る経路になる（ISSUE-445 RC-1 と同型）。"
    )
