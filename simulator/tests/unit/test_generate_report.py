"""UC-004 generate_report (Interactor): ReportPresenterPort へ委譲するのみ。

変換責務を持たず Presenter へ完全委譲することを、モック Presenter で検証する
（CLEAN_ARCH §8 違反②の解消・SRP）。
"""
from __future__ import annotations

import pytest

from simulator.usecase.ports import ReportPresenterPort


class _SpyPresenter(ReportPresenterPort):
    def __init__(self):
        self.markdown_calls = []
        self.html_calls = []
        self.json_calls = []

    def present_markdown(self, result):
        self.markdown_calls.append(result)
        return "MARKDOWN"

    def present_html(self, result, path):
        self.html_calls.append((result, path))
        return None

    def present_json(self, result, path):
        self.json_calls.append((result, path))
        return None


def _interactor(presenter):
    from simulator.usecase.generate_report import GenerateReportInteractor

    return GenerateReportInteractor(presenter)


def test_generate_markdown_delegates_to_presenter_and_returns_its_result():
    spy = _SpyPresenter()
    interactor = _interactor(spy)
    result = object()

    out = interactor.generate_markdown(result)

    assert out == "MARKDOWN"
    assert spy.markdown_calls == [result]  # そのまま委譲（変換しない）


def test_generate_html_delegates_with_path():
    spy = _SpyPresenter()
    interactor = _interactor(spy)
    result = object()
    path = "/tmp/report.html"

    interactor.generate_html(result, path)

    assert spy.html_calls == [(result, path)]


def test_generate_json_delegates_with_path():
    spy = _SpyPresenter()
    interactor = _interactor(spy)
    result = object()
    path = "/tmp/stats.json"

    interactor.generate_json(result, path)

    assert spy.json_calls == [(result, path)]


def test_interactor_does_not_transform_result_itself():
    # Interactor が result を加工せず素通しすることを確認（同一オブジェクト性）
    spy = _SpyPresenter()
    interactor = _interactor(spy)
    sentinel = {"trades": [1, 2, 3]}

    interactor.generate_markdown(sentinel)

    assert spy.markdown_calls[0] is sentinel


def test_interactor_requires_report_presenter_port():
    # Presenter 依存は ReportPresenterPort 抽象に対して行う（DIP）
    import inspect

    from simulator.usecase.generate_report import GenerateReportInteractor

    sig = inspect.signature(GenerateReportInteractor.__init__)
    assert "presenter" in sig.parameters


def test_generate_report_module_purity():
    import ast

    import simulator.usecase.generate_report as gr

    with open(gr.__file__, encoding="utf-8") as f:
        tree = ast.parse(f.read())
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module)
    forbidden = ("simulator.adapter", "simulator.framework", "simulator.main", "pydantic", "pandas")
    for name in imported:
        assert not name.startswith(forbidden), name
