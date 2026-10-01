"""1 ジョブは実行中に追記されるデータ実体を 1 回だけ読む（ISSUE-551）。

なぜ在るか（実測 2026-09-28・実 UI）:
    MT5 常駐が毎分追記しているデータセット jp225_mt5_spread の系列 CSV で run を投入すると、約 3.5 分
    「準備中」のあと「指標系列の長さがデータ実体の行数と一致しません」で失敗した。1 ジョブが
    同じ実体を registry 用・Bar 列用・行の時刻用に別々に読み、読みの間に行が増えていた
    （読みの単一点 `ohlc_frame_cache` の鍵が (mtime, サイズ) を含むため、追記のたびに読み直す）。

表明:
    1. 状態検証: 読むたびに実体へ行が追記されても、ジョブは成功し成果物まで書く。
    2. 計算量: 発行（parse）− 相異なる (実体, sep) = 0。追記の量（1 行 / 3 行）を変えても
       発行は増えない。回数そのものは焼き込まない。

観測の境界: `ohlc_frame_cache` が宣言する注入点（`set_reader`）と発行記録（`parse_log`）
だけを使う（内部名の monkeypatch はしない・絶対命令 2026-09-25）。追記は注入した reader が
parse の直後に行う（常駐の書き手が読みの合間に追記するのと同じ事象）。
"""
from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
import pytest

from simulator.adapter.repository import ohlc_frame_cache as cache
from simulator.sim_ui.main import run_job

#: 2024-01-01T00:00:00Z（comma 形式の time 列は UNIX 秒 int が契約）。
_EPOCH_2024_01_01 = 1_704_067_200
_ROWS = 40


@pytest.fixture(autouse=True)
def _fresh_cache():
    cache.clear()
    yield
    cache.set_reader(None)
    cache.clear()


def _row(i: int) -> str:
    base = 1.0000 + i * 0.0010
    return f"{_EPOCH_2024_01_01 + 60 * i},{base},{base + 0.0005},{base - 0.0005},{base},100,0"


def _write_csv(path: Path) -> Path:
    lines = ["time,open,high,low,close,volume,spread", *(_row(i) for i in range(_ROWS))]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def _appending_reader(csv: Path, rows_per_parse: int):
    """parse の直後に ``csv`` へ行を追記する reader（常駐の書き手の追記を再現する）。"""
    appended = [_ROWS]

    def read(source_ref, **options):
        frame = pd.read_csv(source_ref, **options)
        if Path(source_ref).resolve() == csv.resolve():
            with csv.open("a", encoding="utf-8") as handle:
                for _ in range(rows_per_parse):
                    handle.write(_row(appended[0]) + "\n")
                    appended[0] += 1
        return frame

    return read


def _run(tmp_path: Path, rows_per_parse: int) -> "tuple[int, Path, Path]":
    csv = _write_csv(tmp_path / "m1.csv")
    job_dir = tmp_path / "0123456789abcdef0123456789abcdef"
    job_dir.mkdir()
    backtest = {
        "ea_name": "TC24051901", "symbol": "JP225", "period": "M1", "data_path": str(csv),
        "initial_deposit": 100_000.0, "contract_size": 1.0, "volume_min": 0.01,
        "volume_max": 100.0, "volume_step": 0.01, "stops_level": 0, "digits": 5,
        "point_size": 0.0001, "leverage": 100.0, "ma_period": 2, "ma_method": "sma",
        "lot_size": 1.0, "stop_loss_points": 100, "take_profit_points": 200,
    }
    (job_dir / "spec.json").write_text(
        json.dumps({"backtest": backtest, "sizing": None}), encoding="utf-8"
    )
    cache.set_reader(_appending_reader(csv, rows_per_parse))
    code = run_job.main(["--job-dir", str(job_dir)])
    return code, job_dir, csv


def _failure(job_dir: Path) -> str:
    path = job_dir / "failure.json"
    return path.read_text(encoding="utf-8") if path.exists() else ""


@pytest.mark.parametrize("rows_per_parse", [1, 3])
def test_job_succeeds_while_the_entity_is_appended(tmp_path, rows_per_parse) -> None:
    # Act
    code, job_dir, _csv = _run(tmp_path, rows_per_parse)

    # Assert: 成功し、成果物まで書けている。
    assert code == 0, _failure(job_dir)
    assert (job_dir / "stats.json").is_file()
    assert (job_dir / "report.json").is_file()


@pytest.mark.parametrize("rows_per_parse", [1, 3])
def test_job_parses_each_entity_once_while_it_is_appended(tmp_path, rows_per_parse) -> None:
    # Act
    code, job_dir, csv = _run(tmp_path, rows_per_parse)

    # Assert: 発行 − 相異なる (実体, sep) = 0（追記の量に依らない）。
    assert code == 0, _failure(job_dir)
    distinct = {(path, sep) for (path, _m, _s, sep) in cache.parse_log}
    assert len(cache.parse_log) - len(distinct) == 0, cache.parse_log
    assert any(path == str(csv.resolve()) for (path, _sep) in distinct), cache.parse_log
