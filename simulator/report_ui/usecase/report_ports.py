"""report_ui Output Boundary（詳細設計 §3.3）。

既存 ReportPresenterPort（present_markdown/html/json）とは別 Port（ISP・既存非拡張）。
報告ドメインモデルを JSON 契約ファイルへ出力する境界抽象のみを定義する（domain のみ依存）。
"""
from __future__ import annotations

import abc
from typing import Any


class ReportPayloadPresenterPort(abc.ABC):
    """ReportPayloadModel を report.json（§5 JSON契約）へ書き出す境界。"""

    @abc.abstractmethod
    def present_report_payload(self, payload_model: Any, path: Any) -> None:
        raise NotImplementedError
