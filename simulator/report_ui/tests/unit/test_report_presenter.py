"""ReportUiPresenter 単体テスト（詳細設計 §5.5・§8.1）。

_sanitize（inf/-inf/nan→null）と JSON 契約形状・allow_nan=False で JSON.parse 可能な
出力を検証する。
"""
from __future__ import annotations

import json
import math

import pytest

from simulator.report_ui.adapter.report_presenter import ReportUiPresenter, _sanitize
from simulator.report_ui.usecase.report_models import (
    ReportPayloadModel,
    SegmentModel,
    SummaryModel,
    TradeRow,
    VerdictModel,
)
from simulator.report_ui.usecase.report_ports import ReportPayloadPresenterPort


# --- _sanitize 純関数 -------------------------------------------------------

class TestSanitize:
    def test_inf_to_null(self):
        assert _sanitize(float("inf")) is None

    def test_neg_inf_to_null(self):
        assert _sanitize(float("-inf")) is None

    def test_nan_to_null(self):
        assert _sanitize(float("nan")) is None

    def test_finite_float_kept(self):
        assert _sanitize(3.14) == 3.14

    def test_nested_dict_and_list(self):
        obj = {"a": float("inf"), "b": [1.0, float("nan"), {"c": float("-inf")}]}
        out = _sanitize(obj)
        assert out == {"a": None, "b": [1.0, None, {"c": None}]}

    def test_int_and_str_kept(self):
        assert _sanitize({"i": 5, "s": "x"}) == {"i": 5, "s": "x"}


# --- Presenter 契約・実装 ---------------------------------------------------

def _payload():
    trade = TradeRow(
        id=1, side="buy", entry_time=1000, exit_time=2000,
        entry_price=39402.0, exit_price=39452.0, profit=50.0, volume="0.1",
        sl="39382.0", tp="39452.0", order=1, comment="tp", balance=10050.0,
        hold_sec=1000, mfe=5.0, mae=1.0,
    )
    seg = SegmentModel(
        label="IS", meta={"symbol": "JP225", "bars": 1, "trades": 1},
        report={}, bars=[{"time": 1000, "open": 1.0, "high": 2.0, "low": 0.5, "close": 1.5}],
        trades=[trade], orders=[],
        agg={"balance_curve": [{"time": 2000, "value": 10050.0}], "heat": []},
    )
    summary = SummaryModel(
        trades=1, net=50.0, final_balance=10050.0, win_rate=100.0,
        profit_factor=float("inf"), expectancy=50.0, payoff=float("inf"),
        return_pct=0.5, max_dd_pct=0.0,
    )
    return ReportPayloadModel(
        meta={"symbol": "JP225"},
        segments={"is": seg, "oos": seg},
        summary={"is": summary, "oos": summary},
        degradation={"net": {"is": 50.0, "oos": 50.0, "ratio": 1.0, "delta": 0.0}},
        verdict=VerdictModel(result="pass", reasons=["OOSでも優位性を維持"]),
        contract_notes=["note"],
    )


class TestPresenter:
    def test_implements_port(self):
        assert isinstance(ReportUiPresenter(), ReportPayloadPresenterPort)

    def test_writes_parseable_json(self, tmp_path):
        out = tmp_path / "report.json"
        ReportUiPresenter().present_report_payload(_payload(), out)
        # 非有限値を含む payload でも JSON.parse 可能（allow_nan=False で inf→null 済）
        data = json.loads(out.read_text())
        assert data["meta"]["symbol"] == "JP225"
        assert set(data["segments"].keys()) == {"is", "oos"}

    def test_inf_profit_factor_becomes_null(self, tmp_path):
        out = tmp_path / "report.json"
        ReportUiPresenter().present_report_payload(_payload(), out)
        data = json.loads(out.read_text())
        assert data["summary"]["is"]["profit_factor"] is None
        assert data["summary"]["is"]["payoff"] is None

    def test_trade_contract_keys_present(self, tmp_path):
        out = tmp_path / "report.json"
        ReportUiPresenter().present_report_payload(_payload(), out)
        data = json.loads(out.read_text())
        tr = data["segments"]["is"]["trades"][0]
        expected = {"id", "side", "entry_time", "exit_time", "entry_price",
                    "exit_price", "profit", "volume", "sl", "tp", "order",
                    "comment", "balance", "hold_sec", "mfe", "mae"}
        assert set(tr.keys()) == expected

    def test_segment_has_orders_and_agg_keys(self, tmp_path):
        out = tmp_path / "report.json"
        ReportUiPresenter().present_report_payload(_payload(), out)
        data = json.loads(out.read_text())
        seg = data["segments"]["is"]
        assert "orders" in seg and seg["orders"] == []
        assert "balance_curve" in seg["agg"]

    def test_verdict_and_contract_notes_present(self, tmp_path):
        out = tmp_path / "report.json"
        ReportUiPresenter().present_report_payload(_payload(), out)
        data = json.loads(out.read_text())
        assert data["verdict"]["result"] == "pass"
        assert "_contract_notes" in data

    def test_no_nonfinite_in_output(self, tmp_path):
        # allow_nan=False を実証: NaN/Infinity 文字列が JSON テキストに現れない
        out = tmp_path / "report.json"
        ReportUiPresenter().present_report_payload(_payload(), out)
        text = out.read_text()
        assert "Infinity" not in text
        assert "NaN" not in text
