"""report_models / report_ports の DTO・Port 形状テスト（詳細設計 §3.3・§5）。

ステージ① payload 形状を全確定するため、DTO のフィールド存在と Port の契約を固定する。
"""
from __future__ import annotations

import abc
import dataclasses

import pytest

from simulator.report_ui.usecase import report_models as rm
from simulator.report_ui.usecase.report_ports import ReportPayloadPresenterPort


def _field_names(cls):
    return {f.name for f in dataclasses.fields(cls)}


class TestTradeRow:
    def test_has_16_contract_keys(self):
        # 詳細設計 §4.1 trades[] 16キー
        expected = {
            "id", "side", "entry_time", "exit_time", "entry_price", "exit_price",
            "profit", "volume", "sl", "tp", "order", "comment", "balance",
            "hold_sec", "mfe", "mae",
        }
        assert _field_names(rm.TradeRow) == expected


class TestSummaryModel:
    def test_has_summary_keys(self):
        expected = {
            "trades", "net", "final_balance", "win_rate", "profit_factor",
            "expectancy", "payoff", "return_pct", "max_dd_pct",
        }
        assert _field_names(rm.SummaryModel) == expected


class TestSegmentModel:
    def test_has_segment_keys(self):
        expected = {"label", "meta", "report", "bars", "trades", "orders", "agg"}
        assert _field_names(rm.SegmentModel) == expected


class TestReportPayloadModel:
    def test_has_toplevel_keys(self):
        expected = {"meta", "segments", "summary", "degradation", "verdict",
                    "contract_notes"}
        assert _field_names(rm.ReportPayloadModel) == expected


class TestVerdictModel:
    def test_has_result_and_reasons(self):
        assert _field_names(rm.VerdictModel) == {"result", "reasons"}


class TestReportPayloadPresenterPort:
    def test_is_abstract_with_present_method(self):
        # 抽象 Port: 直接インスタンス化不可・present_report_payload を要求
        assert issubclass(ReportPayloadPresenterPort, abc.ABC)
        with pytest.raises(TypeError):
            ReportPayloadPresenterPort()
        assert hasattr(ReportPayloadPresenterPort, "present_report_payload")
