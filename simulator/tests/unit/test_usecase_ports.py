"""usecase/ports.py の抽象 IF 構造テスト（CLEAN_ARCH §5）。

各 Port は abc.ABC の抽象メソッドを持ち、未実装サブクラスのインスタンス化は
TypeError になる（abstractmethod 契約）。戻り値にフレームワーク型を漏らさない方針
（StrategyPort.on_new_bar は list[Order] を返す）はシグネチャで表明する。
"""
from __future__ import annotations

import abc
import inspect

import pytest

PORT_NAMES = [
    "MarketDataPort",
    "ResultSinkPort",
    "StrategyPort",
    "IndicatorPort",
    "TickModelPort",
    "ReportPresenterPort",
    "RunBacktestInputBoundary",
    "CompareStatsInputBoundary",
]


@pytest.mark.parametrize("name", PORT_NAMES)
def test_port_is_abstract_base_class(name):
    import simulator.usecase.ports as ports

    port = getattr(ports, name)
    assert isinstance(port, type)
    assert issubclass(port, abc.ABC)
    # 抽象メソッドを 1 つ以上持つ
    assert len(port.__abstractmethods__) >= 1


@pytest.mark.parametrize("name", PORT_NAMES)
def test_port_cannot_be_instantiated_without_implementation(name):
    import simulator.usecase.ports as ports

    port = getattr(ports, name)
    with pytest.raises(TypeError):
        port()  # 抽象メソッド未実装ではインスタンス化不可


def test_market_data_port_has_load_signature():
    from simulator.usecase.ports import MarketDataPort

    sig = inspect.signature(MarketDataPort.load)
    assert list(sig.parameters) == ["self", "source_ref", "timeframe", "period"]


def test_strategy_port_has_three_lifecycle_methods():
    from simulator.usecase.ports import StrategyPort

    for method in ("on_init", "on_new_bar", "on_position_check"):
        assert callable(getattr(StrategyPort, method))
    sig = inspect.signature(StrategyPort.on_new_bar)
    assert list(sig.parameters) == ["self", "bar_index", "indicators", "account"]


def test_indicator_port_has_get_and_update():
    from simulator.usecase.ports import IndicatorPort

    assert "get" in IndicatorPort.__abstractmethods__
    assert "update" in IndicatorPort.__abstractmethods__


def test_tick_model_port_has_ticks_of():
    from simulator.usecase.ports import TickModelPort

    sig = inspect.signature(TickModelPort.ticks_of)
    assert list(sig.parameters) == ["self", "bar", "prev_close"]


def test_result_sink_port_has_save_methods():
    from simulator.usecase.ports import ResultSinkPort

    for method in ("save_trades", "save_stats", "save_report"):
        assert method in ResultSinkPort.__abstractmethods__


def test_report_ports_are_split_into_single_method_ports():
    # ISSUE-098 🔴-1 / ISSUE-099 🟡-1: 旧「3 メソッド 1 Port」の壊れた契約を、形式別
    # 1 メソッド Port の契約へ再設計した。各形式 Port は自形式 1 メソッドのみを abstract に持つ。
    from simulator.usecase.ports import (
        HtmlReportPort,
        JsonReportPort,
        MarkdownReportPort,
    )

    assert MarkdownReportPort.__abstractmethods__ == frozenset({"present_markdown"})
    assert HtmlReportPort.__abstractmethods__ == frozenset({"present_html"})
    assert JsonReportPort.__abstractmethods__ == frozenset({"present_json"})


def test_run_backtest_input_boundary_has_execute():
    from simulator.usecase.ports import RunBacktestInputBoundary

    assert "execute" in RunBacktestInputBoundary.__abstractmethods__


def test_compare_stats_input_boundary_has_execute():
    from simulator.usecase.ports import CompareStatsInputBoundary

    sig = inspect.signature(CompareStatsInputBoundary.execute)
    assert list(sig.parameters) == ["self", "py_stats", "mt5_stats", "tolerances"]


def test_concrete_implementation_can_be_instantiated():
    # 全抽象メソッドを実装したサブクラスは生成できる（LSP 健全性）
    from simulator.usecase.ports import ReportPresenterPort

    class _Stub(ReportPresenterPort):
        def present_markdown(self, result):
            return "ok"

        def present_html(self, result, path):
            return None

        def present_json(self, result, path):
            return None

    stub = _Stub()
    assert stub.present_markdown(None) == "ok"


def test_ports_module_does_not_import_outer_layers():
    import ast

    import simulator.usecase.ports as ports

    with open(ports.__file__, encoding="utf-8") as f:
        tree = ast.parse(f.read())
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module)
    # adapter / framework / main / pandas / pydantic を import しない
    forbidden = ("simulator.adapter", "simulator.framework", "simulator.main", "pydantic")
    for name in imported:
        assert not name.startswith(forbidden), name
