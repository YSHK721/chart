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
    from simulator.adapter.repository.ohlc_mt5_csv import Mt5CsvOHLCRepository
    from simulator.usecase.bar_times import bar_epoch_seconds

    bars = Mt5CsvOHLCRepository().load(str(_FIXTURE), None, None)
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
    assert not (job_dir / "report_payload_error.json").exists()
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


def _with_trace(job_dir: Path) -> Path:
    spec_path = job_dir / "spec.json"
    spec = json.loads(spec_path.read_text(encoding="utf-8"))
    spec["trace"] = {"enabled": True}
    spec_path.write_text(json.dumps(spec, ensure_ascii=False), encoding="utf-8")
    return job_dir


@pytest.mark.parametrize("period", ["Daily", "H1"])
def test_one_job_builds_the_selected_bars_once_even_with_the_trace(tmp_path: Path, period: str) -> None:
    """計算量: 1 ジョブで選んだ足の実体を作る回数 − 1 run が使う判定足の系列の数 = 0。

    実行・表示・接点・実行トレースのどこでも作り直さない（レビュー 🔴3: トレースが
    組み直していた）。観測の境界は `simulator.main.run_period.set_build_observer`。
    """
    from simulator.main import run_period

    built: "list[str]" = []
    run_period.set_build_observer(built.append)
    try:
        job_dir = _with_trace(
            _job(tmp_path, period, settings={"tester": _tester(Period=period), "inputs": []})
        )
        assert run_job.main(["--job-dir", str(job_dir)]) == 0, _reason(job_dir)
    finally:
        run_period.set_build_observer(None)
    runs = 1  # 1 ジョブ＝1 run、run が受け取る判定足の系列は 1 つ
    assert len(built) - runs == 0, built
    # トレースは書けている（作り直しも 1 分足の番号での引き損ないも無い）。
    assert not (job_dir / "trace_error.json").exists()
    assert (job_dir / "trace_indicators.parquet").is_file()


def test_the_ema_deviation_chart_draws_the_ema_and_the_daily_limit_level(tmp_path: Path) -> None:
    """EMA_Deviation_Short_EA の売買履歴チャートは、日足の EMA と足ごとの指値の水準を描く（2026-10-06）。

    線の値は、前の日足の EMA から形成中の EMA を独立に作ると、ちょうど 8% 上になる。
    """
    job_dir = _job(
        tmp_path, "level",
        backtest=_backtest(ea_name="EMA_Deviation_Short_EA", ma_period=21),
        settings={
            "tester": _tester(Expert="EMA_Deviation_Short_EA.ex5", Period="Daily"),
            "inputs": [],
        },
    )
    assert run_job.main(["--job-dir", str(job_dir)]) == 0, _reason(job_dir)
    declaration = json.loads((job_dir / "chart_bars.json").read_text(encoding="utf-8"))
    series = {d["series"]: d["column"] for d in declaration["indicators"]}
    assert set(series) == {"ema", "deviation_level"}
    frame = pd.read_parquet(job_dir / "chart_bars.parquet")
    # 定義へ戻す: 足 k の線は、形成中 EMA（前の日足の EMA から）からちょうど 8% 上の価格。
    #   前の足の EMA が在る足だけを、列ごとにまとめて照合する（行ごとの分岐を書かない）。
    a = 2.0 / 22
    pairs = pd.DataFrame(
        {"prev_ema": frame[series["ema"]].shift(1), "level": frame[series["deviation_level"]]}
    ).dropna()
    assert len(pairs), "照合した足が 0 本（検定が空虚）"
    ratio = pairs["level"] / (a * pairs["level"] + (1 - a) * pairs["prev_ema"])
    assert (ratio - 1.08).abs().max() == pytest.approx(0.0, abs=1e-12)
