"""単一区間の公開口（G-2 execute_single / G-4 not_evaluated）の単体テスト。

背景（なぜ要るか）: sim のバックテストジョブは **1 run = 1 区間**である（`run_backtest` は
1 result を返す）。`execute` は IS/OOS の 2 区間を必須にし、両者の比較から degradation と
verdict を導く。単一 run をそこへ流すには同じ result を 2 回渡すしかなく、それは
**実施していない比較の判定値**を生成する＝不実データになる。よって単一区間の口を分ける。

固定する不変条件（承認 G の回帰ゲート）:
    2. execute_single の契約: segments/summary は 1 キー・degradation == {}（None 不可）
    3. **写像の単一ソース証明**: 同一 result に対し `execute` の segments["is"] と
       `execute_single` の segments["single"] が完全一致する（写像を 2 本持っていない）
    4. **不実データ機械ゲート**: verdict.result は空文字であり、pass/warn/fail のいずれでもない
    5. Presenter 無改変で通ること（6 トップキー・allow_nan=False・キー名非依存）
    6. not_evaluated の契約（理由文字列を 1 件持つ・VerdictModel の書き手は policy 1 箇所）
"""
from __future__ import annotations

import json
import math
from dataclasses import asdict
from pathlib import Path

import pytest

from simulator.report_ui.adapter.report_presenter import ReportUiPresenter
from simulator.report_ui.tests.unit.test_build_report_payload import (
    _ea_params,
    _make_result,
    _meta,
    _spec,
)
from simulator.report_ui.usecase.assessment_policy import AssessmentPolicy
from simulator.report_ui.usecase.build_report_payload import BuildReportPayload
from simulator.report_ui.usecase.report_models import ReportPayloadModel, VerdictModel

_NOT_EVALUATED_REASON = "単一区間のため IS/OOS 比較は未実施"


def _result():
    return _make_result(
        [100.0, -50.0, 30.0], [2000, 3000, 4000], [10100.0, 10050.0, 10080.0])


def _single(**kwargs):
    return BuildReportPayload().execute_single(
        result=kwargs.pop("result", None) or _result(),
        bars=kwargs.pop("bars", []),
        spec=_spec(),
        ea_params=_ea_params(),
        meta=kwargs.pop("meta", None) or _meta("is"),
        **kwargs,
    )


# --- ゲート 2: execute_single の契約 -------------------------------------------

def test_execute_single_returns_a_payload_model() -> None:
    assert isinstance(_single(), ReportPayloadModel)


def test_segments_and_summary_hold_exactly_one_key() -> None:
    payload = _single()
    assert list(payload.segments) == ["single"]
    assert list(payload.summary) == ["single"]


def test_the_segment_key_is_not_a_fabricated_is_or_oos_label() -> None:
    """"is" を名乗らせない（実施していない区分を騙らせない）。"""
    payload = _single()
    assert "is" not in payload.segments
    assert "oos" not in payload.segments


def test_the_segment_key_can_be_named_by_the_caller() -> None:
    payload = _single(segment_key="run")
    assert list(payload.segments) == ["run"]
    assert list(payload.summary) == ["run"]


def test_degradation_is_an_empty_dict_not_none() -> None:
    """None は JSON で null になり「算出したが空」と区別できない。空 dict で型を保つ。"""
    payload = _single()
    assert payload.degradation == {}
    assert payload.degradation is not None


def test_meta_is_built_by_the_same_mapping_as_execute() -> None:
    payload = _single()
    assert list(payload.meta) == [
        "symbol", "timeframe", "strategy", "params",
        "initial_deposit", "split", "note",
    ]


def test_summary_is_measured_from_the_actual_run() -> None:
    payload = _single()
    summary = payload.summary["single"]
    assert summary.trades == 3
    assert summary.net == pytest.approx(80.0)


def test_contract_notes_include_the_single_segment_caveat() -> None:
    """単一区間であること（比較未実施）が payload 自身に残る。"""
    notes = _single(contract_notes_extra=["単一区間（IS/OOS 分割なし）"]).contract_notes
    assert any("単一区間" in n for n in notes)
    # 既存の契約注記も失われない（連結であって置換ではない）。
    assert any("trades.order" in n for n in notes)


# --- ゲート 3: 写像の単一ソース証明 ---------------------------------------------

def test_single_segment_is_identical_to_the_is_segment_of_execute() -> None:
    """同一 result なら execute の segments["is"] と完全一致する（写像は 1 本）。"""
    result = _result()
    both = BuildReportPayload().execute(
        result_is=result, result_oos=result, bars_is=[], bars_oos=[],
        spec=_spec(), ea_params=_ea_params(),
        meta_is=_meta("is"), meta_oos=_meta("is"),
    )
    one = BuildReportPayload().execute_single(
        result=result, bars=[], spec=_spec(), ea_params=_ea_params(), meta=_meta("is"),
    )
    assert asdict_segment(one.segments["single"]) == asdict_segment(both.segments["is"])


def test_single_summary_is_identical_to_the_is_summary_of_execute() -> None:
    result = _result()
    both = BuildReportPayload().execute(
        result_is=result, result_oos=result, bars_is=[], bars_oos=[],
        spec=_spec(), ea_params=_ea_params(),
        meta_is=_meta("is"), meta_oos=_meta("is"),
    )
    one = BuildReportPayload().execute_single(
        result=result, bars=[], spec=_spec(), ea_params=_ea_params(), meta=_meta("is"),
    )
    assert asdict(one.summary["single"]) == asdict(both.summary["is"])


def asdict_segment(seg) -> dict:
    """SegmentModel を比較可能な dict へ（TradeRow も dataclass なので再帰で展開）。"""
    return {
        "label": seg.label, "meta": seg.meta, "report": seg.report,
        "bars": seg.bars, "orders": seg.orders, "agg": seg.agg,
        "trades": [asdict(t) for t in seg.trades],
    }


# --- ゲート 4: 不実データ機械ゲート ---------------------------------------------

def test_verdict_is_not_a_pass_warn_or_fail() -> None:
    """実施していない判定を名乗らない（これが破れたら不実データが出る）。"""
    verdict = _single().verdict
    assert verdict.result == ""
    assert verdict.result not in {"pass", "warn", "fail"}


def test_verdict_states_why_it_was_not_evaluated() -> None:
    verdict = _single().verdict
    assert len(verdict.reasons) >= 1
    assert all(isinstance(r, str) and r for r in verdict.reasons)


def test_no_degradation_metric_is_invented() -> None:
    """劣化率のキーが 1 つでも生えたら比較を騙っている。"""
    assert _single().degradation == {}


# --- ゲート 6: not_evaluated（VerdictModel の書き手は policy 1 箇所）--------------

def test_not_evaluated_returns_an_empty_result_with_the_reason() -> None:
    verdict = AssessmentPolicy().not_evaluated(_NOT_EVALUATED_REASON)
    assert isinstance(verdict, VerdictModel)
    assert verdict.result == ""
    assert verdict.reasons == [_NOT_EVALUATED_REASON]


def test_not_evaluated_does_not_disturb_the_existing_verdict_tree() -> None:
    """既存の判定木（pass/warn/fail）は不変。"""
    policy = AssessmentPolicy()
    sum_is = _make_result([100.0], [2000], [10100.0])
    both = BuildReportPayload().execute(
        result_is=sum_is, result_oos=sum_is, bars_is=[], bars_oos=[],
        spec=_spec(), ea_params=_ea_params(),
        meta_is=_meta("is"), meta_oos=_meta("oos"),
    )
    assert both.verdict.result in {"pass", "warn", "fail"}
    assert policy.not_evaluated("x").result == ""


# --- ゲート 5: Presenter 無改変で通る ------------------------------------------

def test_presenter_writes_the_single_segment_payload_unchanged(tmp_path: Path) -> None:
    """Presenter は segments/summary のキー名に依存しない（改変 0 行の実証）。"""
    out = tmp_path / "report.json"
    ReportUiPresenter().present_report_payload(_single(), out)
    data = json.loads(out.read_text(encoding="utf-8"))
    assert set(data) == {
        "meta", "segments", "summary", "degradation", "verdict", "_contract_notes",
    }
    assert list(data["segments"]) == ["single"]
    assert data["degradation"] == {}
    assert data["verdict"]["result"] == ""


def test_presenter_output_is_browser_parseable_json(tmp_path: Path) -> None:
    """inf/nan が混ざらない（allow_nan=False で書けている）。"""
    out = tmp_path / "report.json"
    ReportUiPresenter().present_report_payload(_single(), out)
    text = out.read_text(encoding="utf-8")
    assert "Infinity" not in text and "NaN" not in text
    data = json.loads(text)
    pf = data["summary"]["single"]["profit_factor"]
    assert pf is None or math.isfinite(pf)
