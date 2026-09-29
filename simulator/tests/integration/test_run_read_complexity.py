"""1 run の CSV parse 回数の計算量検定（ISSUE-541 段 1＋段 2・絶対命令 2026-08-28）。

なぜ計算量で固定するか（実測 2026-09-27・cProfile）:
    是正前は 1 run が同じ系列 CSV を **12 回** parse していた（横: run 本体・表示用の足の
    取り直し・接点・売買履歴チャートの指標の 4 組み立て × 縦: 1 組み立てにつき 3 parse）。
    出力は 1 ビットも変わらないため状態検証では原理的に落ちない（ISSUE-450/257 と同型）。

観測の境界:
    読みの単一点 `simulator/adapter/repository/ohlc_frame_cache.py` が宣言する発行記録
    （`parse_log`）だけを読む（内部名の monkeypatch はしない・絶対命令 2026-09-25）。

表明:
    1. 発行（parse）− 相異なる (実体, sep) = 0（同じ実体を 2 度 parse しない）。
    2. 成果物まで書く 1 ジョブ（run_job.main・settings 経路）で、系列 CSV の parse は
       ちょうど 1 回（run 本体と表示・接点・売買履歴チャートの書き手がすべて共有する）。
    3. 期間窓の長さ 2 点（2 日 / 3 日）で発行が増えない（入力を増やしても発行が増えない）。
    4. Bar 列の組み立て（DataFrame → Bar 列の変換）も 1 ジョブ 1 回（ISSUE-553 項目 2）。表示用の
       指標の対応づけは run が実行した Bar 列を受け取り、組み立て直さない。観測は変換の
       観測口（`_ohlc_frame.set_observer`）で行う。
    **回数そのものを窓の長さから導かない**（どの窓でも「実体 1 つ＝parse 1 回」）。
"""
from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pytest

from simulator.adapter.repository import _ohlc_frame
from simulator.adapter.repository import ohlc_frame_cache as cache
from simulator.adapter.trace.account_curve import AccountCurveRecorder
from simulator.main.tester_settings.kwargs_mapper import effective_to_interactor_kwargs
from simulator.main.tester_settings.run_settings_job import run_settings_job
from simulator.sim_ui.main import run_job
from simulator.tests.tester_settings_engine_fixtures import (
    custom_range_settings,
    daily_epochs,
    engine_binding,
    write_comma_csv,
)

_FIRST_DAY = date(2024, 1, 1)
_BAR_DAYS = 6


@pytest.fixture(autouse=True)
def _fresh_cache():
    cache.clear()
    yield
    cache.clear()


@pytest.fixture
def conversions():
    """DataFrame → Bar 列の変換の観測口へ繋いだ記録（1 回の変換につき 1 行）。"""
    seen: "list[int]" = []
    _ohlc_frame.set_observer(lambda rows, _required: seen.append(rows))
    yield seen
    _ohlc_frame.set_observer(None)


def _run(tmp_path: Path, *, days: int) -> Path:
    """settings 経路の実体（run_settings_job → _write_report_payload）を run_job と同じ結線で回す。

    `run_job.main` を使わないのは、settings 経路の受付が sim core の銘柄プロファイル台帳を
    要るため（合成銘柄では組めない）。結線（request の再利用・指標 1 回）は
    run_job の settings 経路と同じ形をここに組む——検定の対象はその結線が生む読みの回数である。
    """
    csv_path = write_comma_csv(tmp_path / "synth.csv", daily_epochs(_FIRST_DAY, _BAR_DAYS))
    job_dir = tmp_path / "job"
    job_dir.mkdir()
    # writer は job_dir の spec.json（表示メタの出所）を読む。JSON で表せる引数だけを置く。
    json_safe = {"ea_name": "TC24051901", "symbol": "SYNTH", "period": "H8", "initial_deposit": 100000.0,
                 "data_path": str(csv_path)}
    (job_dir / "spec.json").write_text(json.dumps({"backtest": json_safe}), encoding="utf-8")
    effective = custom_range_settings(_FIRST_DAY, date(2024, 1, days)).effective()
    binding = engine_binding(data_path=str(csv_path))
    account = AccountCurveRecorder()
    exit_code, result, _meta, request = run_settings_job(
        effective, binding, output_dir=job_dir,
        extensions={"run_tracer": account},
    )
    assert exit_code == 0
    run_kwargs = effective_to_interactor_kwargs(effective, binding)
    run_job._write_report_payload(
        job_dir, result,
        load_run_inputs=lambda _backtest: (request.bars, request.symbol_spec),
        load_indicators=lambda: run_job._build_run_indicators(run_kwargs, request.bars),
        run_kwargs=dict(run_kwargs), account=account,
    )
    return job_dir


def _parses_of(csv_name: str) -> "list[tuple]":
    return [key for key in cache.parse_log if key[0].endswith(csv_name)]


@pytest.mark.parametrize("days", [2, 3])
def test_one_job_parses_the_series_exactly_once_regardless_of_window(tmp_path, days) -> None:
    # Act
    job_dir = _run(tmp_path, days=days)

    # Assert: 成果物（stats / report.json / chart_overlay.json）まで書けている＝
    #   parse を削っても出力は欠けない（正の対照）。
    assert (job_dir / "stats.json").is_file()
    assert (job_dir / "report.json").is_file()
    assert (job_dir / "chart_overlay.json").is_file()
    # 発行 − 相異なる (実体, sep) = 0（全実体）。
    distinct = {(path, sep) for (path, _m, _s, sep) in cache.parse_log}
    assert len(cache.parse_log) - len(distinct) == 0, cache.parse_log
    # 系列 CSV はちょうど 1 回（run 本体＋表示・接点・売買履歴チャートの共有）。
    assert len(_parses_of("synth.csv")) == 1, cache.parse_log



def _run_marketdata(tmp_path: Path, *, days: int) -> Path:
    """marketdata 形式（実 run の系列と同じ形式）の CSV で、settings 経路の 1 ジョブを回す。

    comma 形式の合成 CSV は窓つきの委譲経路（CandleSource）で Bar を組むため、DataFrame → Bar 列の
    変換を通らない。本番の jp225_mt5_spread と同じ形式で数える。結線は `_run` と同じ。
    """
    csv_path = tmp_path / "md.csv"
    rows = ["date,open,high,low,close,volume,spread"]
    for hour in range(_BAR_DAYS * 24):
        base = 100.0 + hour
        rows.append(
            f"2024-01-{1 + hour // 24:02d} {hour % 24:02d}:00:00,"
            f"{base},{base + 0.5},{base - 0.5},{base + 0.2},1,3"
        )
    csv_path.write_text("\n".join(rows) + "\n", encoding="utf-8")
    job_dir = tmp_path / "job"
    job_dir.mkdir()
    json_safe = {"ea_name": "CalcProbe_EA", "symbol": "JP225", "period": "H1",
                 "initial_deposit": 100000.0, "data_path": str(csv_path)}
    (job_dir / "spec.json").write_text(json.dumps({"backtest": json_safe}), encoding="utf-8")
    effective = custom_range_settings(
        _FIRST_DAY, date(2024, 1, days), Expert="CalcProbe_EA.ex5", Period="H1"
    ).effective()
    binding = engine_binding(
        data_path=str(csv_path), period="H1",
        ea_params={"ma_period": 2, "ma_method": "sma", "lot_size": 1.0,
                   "stop_loss_points": 0, "take_profit_points": 0},
    )
    account = AccountCurveRecorder()
    exit_code, result, _meta, request = run_settings_job(
        effective, binding, output_dir=job_dir, extensions={"run_tracer": account},
    )
    assert exit_code == 0
    run_kwargs = effective_to_interactor_kwargs(effective, binding)
    run_job._write_report_payload(
        job_dir, result,
        load_run_inputs=lambda _backtest: (request.bars, request.symbol_spec),
        load_indicators=lambda: run_job._build_run_indicators(run_kwargs, request.bars),
        run_kwargs=dict(run_kwargs), account=account,
    )
    return job_dir


@pytest.mark.parametrize("days", [2, 3])
def test_one_job_builds_the_bars_once_regardless_of_window(tmp_path, conversions, days) -> None:
    # Act
    job_dir = _run_marketdata(tmp_path, days=days)

    # Assert: 成果物まで書けている（正の対照）・Bar 列の組み立て − 1 = 0（窓の長さに依らない）
    assert (job_dir / "chart_overlay.json").is_file()
    assert len(conversions) - 1 == 0, conversions
