"""UC-004 Interactor: BacktestResult を ReportPresenterPort へ委譲してレポート生成する。

変換責務は持たず Presenter へ完全委譲する（CLEAN_ARCH §8 依存方向違反②の解消）。
Presenter 依存は ReportPresenterPort 抽象に対して行う（DIP）。

usecase 層は domain のみ依存可。本モジュールは usecase 内 ports のみ参照する。
"""
from __future__ import annotations

from typing import Any

from backtest.usecase.ports import ReportPresenterPort


class GenerateReportInteractor:
    """ReportPresenterPort へ委譲するのみの Interactor。"""

    def __init__(self, presenter: ReportPresenterPort) -> None:
        self._presenter = presenter

    def generate_markdown(self, result: Any) -> str:
        return self._presenter.present_markdown(result)

    def generate_html(self, result: Any, path: Any) -> None:
        self._presenter.present_html(result, path)

    def generate_json(self, result: Any, path: Any) -> None:
        self._presenter.present_json(result, path)
