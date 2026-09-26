"""run_job が上のチャートへ重ねる成果物を書く（2026-09-26 依頼者指示）。

`run_job.main` を spec.json 経由で実際に回し、job-dir の 2 ファイルを独立計算と照合する:
    「`trade_markers.json`」: 全トレードの建て・決済のマーク（件数＝トレード数×2・時間足 1m）。
    「`chart_overlay.json`」:
        指標   … EA が宣言した系列（CalcProbe は SMA）。値は終値の単純平均を独立に計算して照合。
        口座   … 足ごと。損益（確定の累計・含み）・DD（有効証拠金の最高値からの下落）を
                 独立に計算して照合。保有中は有効証拠金が残高と異なる足が在ること。
    実行トレースを有効にした run でも同じ成果物が出る（観測口の合成）。
"""
from __future__ import annotations

import json
import math
from pathlib import Path

import pandas as pd
import pytest

from simulator.sim_ui.main import run_job

_EPOCH = 1_704_067_200
_DEPOSIT = 100_000.0
_PERIOD = 3
#: 始値が SMA を上下に跨ぐ並び（買い・売りの途転が複数回起きる）。
_OPENS = [100, 102, 104, 103, 99, 97, 98, 101, 105, 104, 100, 96, 95, 99, 103, 106, 102, 98]


def _closes() -> "list[float]":
    return [o + (0.5 if i % 2 else -0.5) for i, o in enumerate(_OPENS)]


def _write_csv(path: Path) -> Path:
    rows = []
    for i, (o, c) in enumerate(zip(_OPENS, _closes())):
        rows.append(
            {
                "time": _EPOCH + 60 * i, "open": float(o), "high": max(o, c) + 1.0,
                "low": min(o, c) - 1.0, "close": c, "volume": 100, "spread": 3,
            }
        )
    pd.DataFrame(rows).to_csv(path, index=False)
    return path


def _run(tmp: Path, csv: Path, *, trace=None) -> Path:
    job_dir = tmp / "0123456789abcdef0123456789abcdef"
    job_dir.mkdir()
    backtest = {
        "ea_name": "CalcProbe_EA", "symbol": "SYNTH", "period": "M1", "data_path": str(csv),
        "initial_deposit": _DEPOSIT, "contract_size": 10.0, "volume_min": 0.1,
        "volume_max": 100.0, "volume_step": 0.1, "stops_level": 0, "digits": 1,
        "point_size": 0.1, "leverage": 100.0, "ma_period": _PERIOD, "ma_method": "sma",
        "lot_size": 1.0, "stop_loss_points": 0, "take_profit_points": 0,
    }
    (job_dir / "spec.json").write_text(
        json.dumps({"backtest": backtest, "sizing": None, "strategy": None,
                    "settings": None, "trace": trace}),
        encoding="utf-8",
    )
    assert run_job.main(["--job-dir", str(job_dir)]) == 0
    return job_dir


def _load(job_dir: Path, name: str) -> dict:
    return json.loads((job_dir / name).read_text(encoding="utf-8"))


def _sma(values: "list[float]", period: int) -> "list[float | None]":
    return [
        None if i + 1 < period else sum(values[i + 1 - period : i + 1]) / period
        for i in range(len(values))
    ]


@pytest.fixture
def job_dir(tmp_path: Path) -> Path:
    return _run(tmp_path, _write_csv(tmp_path / "d.csv"))


def test_markers_cover_every_trade_on_the_run_timeframe(job_dir: Path) -> None:
    markers = _load(job_dir, "trade_markers.json")
    trades = _load(job_dir, "report.json")["segments"]["single"]["trades"]
    assert trades, "トレードが無い（検定が空虚）"
    assert markers["timeframe"] == "1m"
    assert markers["count"] == 2 * len(trades)
    assert len(markers["pairs"]) == len(trades)


def test_the_declared_trigger_indicator_is_the_independent_sma(job_dir: Path) -> None:
    overlay = _load(job_dir, "chart_overlay.json")
    (sma,) = overlay["indicators"]
    assert (sma["series"], sma["placement"]) == ("sma", "price")
    assert sma["time"] == [_EPOCH + 60 * i for i in range(len(_OPENS))]
    assert sma["value"] == pytest.approx(_sma(_closes(), _PERIOD))


def test_account_rows_are_per_bar_and_the_derived_series_match(job_dir: Path) -> None:
    account = _load(job_dir, "chart_overlay.json")["account"]
    balance, equity = account["balance"], account["equity"]
    assert account["time"] == [_EPOCH + 60 * i for i in range(len(_OPENS))]
    # 損益: 確定の累計＝残高−初期資金 / 含み＝有効証拠金−残高。
    assert account["realized_pnl"] == pytest.approx([b - _DEPOSIT for b in balance])
    assert account["floating_pnl"] == pytest.approx([e - b for b, e in zip(balance, equity)])
    # 保有中は有効証拠金が残高と異なる（決済時だけでなく足ごとに動いている）。
    assert any(abs(f) > 1e-9 for f in account["floating_pnl"])
    # DD: 有効証拠金の最高値からの下落。
    peak, expected = -math.inf, []
    for e in equity:
        peak = max(peak, e)
        expected.append(peak - e)
    assert account["drawdown"] == pytest.approx(expected)
    # 証拠金維持率: 保有の無い足は値なし、保有中は 有効証拠金÷必要証拠金×100。
    rows = list(zip(account["margin"], account["margin_level"], equity))
    flat = [level for margin, level, _ in rows if margin == 0]
    held = [(margin, level, e) for margin, level, e in rows if margin != 0]
    assert flat and held, "保有の有無の両方を通らない並び（検定が空虚）"
    assert flat == [None] * len(flat)
    assert [level for _, level, _ in held] == pytest.approx([e / m * 100.0 for m, _, e in held])


def test_the_same_outputs_are_written_with_the_run_trace_enabled(tmp_path: Path) -> None:
    csv = _write_csv(tmp_path / "d.csv")
    job_dir = _run(tmp_path, csv, trace={"enabled": True, "start": None, "end": None})
    account = _load(job_dir, "chart_overlay.json")["account"]
    assert len(account["time"]) == len(_OPENS)
    assert (job_dir / "trade_markers.json").exists()


def test_a_csv_outside_the_ledger_has_no_dataset_ref(job_dir: Path) -> None:
    """台帳外の CSV は系列名を推測しない（チャート側は描かずに理由を出す）。"""
    assert _load(job_dir, "chart_overlay.json")["dataset_ref"] is None


def test_a_ledger_path_resolves_to_exactly_its_ref() -> None:
    from marketdata.dataset_registry import whitelist

    ref, path = next(iter(whitelist().items()))
    assert run_job._dataset_ref_of(path) == ref
