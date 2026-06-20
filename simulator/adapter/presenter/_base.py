"""Presenter 共通基底（ReportPresenterPort のデフォルト未対応スタブ）。

各 Presenter は present_markdown / present_html / present_json のうち 1 操作のみを担う
（単一責任）。未対応操作は専用 Presenter への案内付き NotImplementedError を送出する。
この未対応スタブは 3 Presenter で共通のため本基底に集約する（DRY）。各サブクラスは
自身が担う 1 操作のみをオーバーライドする。

adapter 層は usecase + domain + 技術ドライバのみに依存する。
"""
from __future__ import annotations

from typing import Any

from simulator.usecase.ports import ReportPresenterPort


class _BasePresenter(ReportPresenterPort):
    """未対応操作を案内付き NotImplementedError で拒否するデフォルト実装を提供する。"""

    def present_markdown(self, result: Any) -> str:
        raise NotImplementedError("Markdown 出力は MarkdownPresenter を使用してください")

    def present_html(self, result: Any, path: Any) -> None:
        raise NotImplementedError("HTML 出力は HtmlPresenter を使用してください")

    def present_json(self, result: Any, path: Any) -> None:
        raise NotImplementedError("JSON 出力は JsonPresenter を使用してください")
