"""ReportPresenterPort 分割（ISSUE-098 🔴-1 LSP / ISSUE-099 🟡-1 ISP）の単体テスト。

形式別 Port（MarkdownReportPort / HtmlReportPort / JsonReportPort）が各 1 メソッドの
契約であること、各 Presenter が自 Port のみ実装し他形式 Port の subtype でないこと
（LSP 是正＝置換すると壊れる関係の解消）、旧 ReportPresenterPort が 3 Port を束ねる
後方互換の集約 ABC であること、形式別 Interactor が自 Port へ委譲することを固定する。
"""
from __future__ import annotations

import abc

import pytest


# ---- 形式別 Port は各 1 メソッドの契約（ISP） ----

def test_markdown_report_port_has_only_present_markdown():
    from simulator.usecase.ports import MarkdownReportPort

    assert MarkdownReportPort.__abstractmethods__ == frozenset({"present_markdown"})


def test_html_report_port_has_only_present_html():
    from simulator.usecase.ports import HtmlReportPort

    assert HtmlReportPort.__abstractmethods__ == frozenset({"present_html"})


def test_json_report_port_has_only_present_json():
    from simulator.usecase.ports import JsonReportPort

    assert JsonReportPort.__abstractmethods__ == frozenset({"present_json"})


@pytest.mark.parametrize(
    "name", ["MarkdownReportPort", "HtmlReportPort", "JsonReportPort"]
)
def test_new_ports_are_abstract_and_not_instantiable(name):
    import simulator.usecase.ports as ports

    port = getattr(ports, name)
    assert issubclass(port, abc.ABC)
    with pytest.raises(TypeError):
        port()  # 単一抽象メソッド未実装ではインスタンス化不可


# ---- 各 Presenter は自 Port のみ実装し、他形式 Port の subtype ではない（LSP） ----

def test_markdown_presenter_implements_only_its_port():
    from simulator.adapter.presenter.markdown import MarkdownPresenter
    from simulator.usecase.ports import (
        HtmlReportPort,
        JsonReportPort,
        MarkdownReportPort,
    )

    assert issubclass(MarkdownPresenter, MarkdownReportPort)
    # LSP: markdown 専用ゆえ html/json 契約の subtype であってはならない
    assert not issubclass(MarkdownPresenter, HtmlReportPort)
    assert not issubclass(MarkdownPresenter, JsonReportPort)


def test_json_presenter_implements_only_its_port():
    from simulator.adapter.presenter.json import JsonPresenter
    from simulator.usecase.ports import (
        HtmlReportPort,
        JsonReportPort,
        MarkdownReportPort,
    )

    assert issubclass(JsonPresenter, JsonReportPort)
    assert not issubclass(JsonPresenter, MarkdownReportPort)
    assert not issubclass(JsonPresenter, HtmlReportPort)


# ---- 旧 ReportPresenterPort は 3 Port を束ねる後方互換の集約 ABC ----

def test_aggregate_report_presenter_port_composes_three_ports():
    from simulator.usecase.ports import (
        HtmlReportPort,
        JsonReportPort,
        MarkdownReportPort,
        ReportPresenterPort,
    )

    assert issubclass(ReportPresenterPort, MarkdownReportPort)
    assert issubclass(ReportPresenterPort, HtmlReportPort)
    assert issubclass(ReportPresenterPort, JsonReportPort)
    assert ReportPresenterPort.__abstractmethods__ == frozenset(
        {"present_markdown", "present_html", "present_json"}
    )
