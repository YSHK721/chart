"""選んだ足（Period）で判定した run は、選んだ足で表示する（2026-10-06・依頼者指示）。

利用者の経路（Tester Settings 付きのジョブ → `run_job.main`）で、Period=Daily のジョブの成果物が
日足であることを固定する:
    * 売買履歴チャートの足（chart_bars）は日足で、宣言の時間足は "1D"。
    * 売買の印は日足のチャートの足の時刻に置かれ、時間足も "1D"。
    * 成績表（report.json）の時間足は選んだ Period（"Daily"）。

正解の出どころ（marketdata の関数を呼ばない）:
    データは 2025 年 1 月の 1 分足（NY は冬時間＝ブローカー時間は UTC+2）。日足の数は
    「UTC+2 時間の日付」の種類の数として独立に数える。
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd
import pytest

from simulator.sim_ui.main import run_job
from simulator.sim_ui.tests.integration.test_run_job_settings import (
    _backtest,
    _job,
    _reason,
    _tester,
)

_FIXTURE = (
    Path(__file__).resolve().parents[4] / "simulator" / "tests" / "fixtures" / "mt5"
    / "ma_slope_jp225_202501" / "input" / "JP225_M1_202501.csv"
)


@pytest.fixture(autouse=True)
def _fixture_dataset(monkeypatch):
    from marketdata.dataset_registry import sim_offered_refs

    from simulator.tests.ledger_entity_fixtures import point_entity_at

    point_entity_at(monkeypatch, sim_offered_refs()[0], _FIXTURE)


def _broker_days(job_dir: Path) -> "set[str]":
    """run が読んだ 1 分足（売買履歴チャートの元）から、UTC+2 時間の日付の集合を数える。"""
    from simulator.main.ea_bindings.sources import ohlc_repository_for
    from simulator.usecase.bar_times import bar_epoch_seconds

    bars = ohlc_repository_for(str(_FIXTURE)).load(str(_FIXTURE), None, None)
    return {
        (datetime.fromtimestamp(t, timezone.utc) + timedelta(hours=2)).date().isoformat()
        for t in bar_epoch_seconds(bars)
    }


def test_a_daily_job_writes_its_chart_markers_and_report_on_daily_bars(tmp_path: Path) -> None:
    # Arrange: データは 1 分足（profile の period は M1）、選んだ Period は Daily。
    job_dir = _job(
        tmp_path, "daily",
        settings={"tester": _tester(Period="Daily"), "inputs": []},
    )
    # Act
    code = run_job.main(["--job-dir", str(job_dir)])
    # Assert
    assert code == 0, _reason(job_dir)
    assert not (job_dir / "chart_overlay_error.json").exists()
    declaration = json.loads((job_dir / "chart_bars.json").read_text(encoding="utf-8"))
    assert declaration["timeframe"] == "1D"
    frame = pd.read_parquet(job_dir / "chart_bars.parquet")
    assert declaration["rows"] == len(frame) == len(_broker_days(job_dir))
    markers = json.loads((job_dir / "trade_markers.json").read_text(encoding="utf-8"))
    assert markers["timeframe"] == "1D"
    assert markers["markers"], "取引が 1 件も無いと印の置き場所を確かめられない（検定が空虚）"
    bar_times = set(frame["time"].tolist())
    assert {m["lwc"]["time"] for m in markers["markers"]} <= bar_times
    report = json.loads((job_dir / "report.json").read_text(encoding="utf-8"))
    assert report["segments"]["single"]["meta"]["timeframe"] == "Daily"


def test_an_m1_job_still_writes_1_minute_bars(tmp_path: Path) -> None:
    job_dir = _job(tmp_path, "m1", settings={"tester": _tester(), "inputs": []})
    assert run_job.main(["--job-dir", str(job_dir)]) == 0, _reason(job_dir)
    declaration = json.loads((job_dir / "chart_bars.json").read_text(encoding="utf-8"))
    assert declaration["timeframe"] == "1m"
    assert declaration["rows"] > len(_broker_days(job_dir)) * 100


@pytest.mark.parametrize("period", ["Daily", "H1"])
def test_one_job_builds_the_selected_bars_once(tmp_path: Path, period: str) -> None:
    """計算量: 1 ジョブで選んだ足の実体を作る回数 − 1 = 0（実行・表示・接点で作り直さない）。

    観測の境界は `simulator.main.run_period.set_build_observer`（宣言された注入点）。
    足の種類（日足・1 時間足）を変えても 1 回のまま。
    """
    from simulator.main import run_period

    built: "list[str]" = []
    run_period.set_build_observer(built.append)
    try:
        job_dir = _job(
            tmp_path, period, settings={"tester": _tester(Period=period), "inputs": []}
        )
        assert run_job.main(["--job-dir", str(job_dir)]) == 0, _reason(job_dir)
    finally:
        run_period.set_build_observer(None)
    assert len(built) - 1 == 0, built
