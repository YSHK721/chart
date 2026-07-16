"""UC-004 Interactor: BacktestResult を Presenter へ委譲してレポート生成する。

変換責務は持たず Presenter へ完全委譲する（CLEAN_ARCH §8 依存方向違反②の解消）。
Presenter 依存は Port 抽象に対して行う（DIP）。

形式別 Interactor（GenerateMarkdownReportInteractor / GenerateHtmlReportInteractor /
GenerateJsonReportInteractor）は形式別 1 メソッド Port のみを注入で受け取り、自形式へ
委譲する（ISSUE-098 🔴-1 LSP / ISSUE-099 🟡-1 ISP の是正＝未使用メソッドへの型依存排除）。
GenerateReportInteractor は 3 形式すべてを 1 実装（ReportPresenterPort）で扱う消費者向けの
後方互換集約 Interactor として温存する。

usecase 層は domain のみ依存可。本モジュールは usecase 内 ports のみ参照する。
"""
from __future__ import annotations

from typing import Any

from simulator.usecase.ports import (
    HtmlReportPort,
    JsonReportPort,
    MarkdownReportPort,
    ReportPresenterPort,
)


class GenerateReportInteractor:
    """ReportPresenterPort（3 形式集約）へ委譲するのみの後方互換 Interactor。"""

    def __init__(self, presenter: ReportPresenterPort) -> None:
        self._presenter = presenter

    def generate_markdown(self, result: Any) -> str:
        return self._presenter.present_markdown(result)

    def generate_html(self, result: Any, path: Any) -> None:
        self._presenter.present_html(result, path)

    def generate_json(self, result: Any, path: Any) -> None:
        self._presenter.present_json(result, path)


class GenerateMarkdownReportInteractor:
    """MarkdownReportPort へ委譲するのみの形式専用 Interactor（ISP）。"""

    def __init__(self, presenter: MarkdownReportPort) -> None:
        self._presenter = presenter

    def generate(self, result: Any) -> str:
        return self._presenter.present_markdown(result)


class GenerateHtmlReportInteractor:
    """HtmlReportPort へ委譲するのみの形式専用 Interactor（ISP）。"""

    def __init__(self, presenter: HtmlReportPort) -> None:
        self._presenter = presenter

    def generate(self, result: Any, path: Any) -> None:
        self._presenter.present_html(result, path)


class GenerateJsonReportInteractor:
    """JsonReportPort へ委譲するのみの形式専用 Interactor（ISP）。"""

    def __init__(self, presenter: JsonReportPort) -> None:
        self._presenter = presenter

    def generate(self, result: Any, path: Any) -> None:
        self._presenter.present_json(result, path)
