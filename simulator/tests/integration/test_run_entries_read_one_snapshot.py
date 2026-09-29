"""公開の run 入口は実行中に追記されるデータ実体を 1 回だけ読む（ISSUE-551 の入口側）。

なぜ在るか（実測 2026-09-29）: 読みの固定区間は当初 sim の子プロセス（run_job）にだけ掛けた。
全期間実データの統合検定（`test_marketdata_dataset_run.py`）は `run_backtest` を直接呼び、
export の常駐が毎分追記する実体を読むため、読みの合間に追記が入ると exit=1 で落ちた
（全件実行で 1 回・単独の再実行では通過）。追記する reader で再現すると `run_backtest` は
同じ実体を 3 回 parse して exit=1 だった。

表明:
    1. 状態検証: 読むたびに実体へ行が追記されても、`run_backtest`・Settings 経路の実行段
       （`run_from_settings`）は成功する。
    2. 計算量: 発行（parse）− 相異なる (実体, sep) = 0。追記の量（1 行 / 3 行）で変わらない。

観測の境界: `ohlc_frame_cache` の注入点（`set_reader`）と発行記録（`parse_log`）だけを使う。
"""
from __future__ import annotations

from datetime import date
from pathlib import Path

import pandas as pd
import pytest

from simulator.adapter.repository import ohlc_frame_cache as cache
from simulator.main import run_backtest
from simulator.main.tester_settings.run_from_settings import run_from_settings
from simulator.tests.tester_settings_engine_fixtures import custom_range_settings, engine_binding

_HOURS = 72
_EA_PARAMS = {"ma_period": 2, "ma_method": "sma", "lot_size": 1.0,
              "stop_loss_points": 0, "take_profit_points": 0}


@pytest.fixture(autouse=True)
def _fresh_cache():
    cache.clear()
    yield
    cache.set_reader(None)
    cache.clear()


def _row(hour: int) -> str:
    base = 100.0 + hour
    return (f"2024-01-{1 + hour // 24:02d} {hour % 24:02d}:00:00,"
            f"{base},{base + 0.5},{base - 0.5},{base + 0.2},1,3")


def _appended_csv(tmp_path: Path, rows_per_parse: int) -> Path:
    """marketdata 形式の CSV を書き、parse のたびに行を追記する reader を注入する。"""
    csv = tmp_path / "md.csv"
    csv.write_text("date,open,high,low,close,volume,spread\n"
                   + "\n".join(_row(h) for h in range(_HOURS)) + "\n", encoding="utf-8")
    appended = [_HOURS]

    def read(source_ref, **options):
        frame = pd.read_csv(source_ref, **options)
        if Path(source_ref).resolve() == csv.resolve():
            with csv.open("a", encoding="utf-8") as handle:
                for _ in range(rows_per_parse):
                    handle.write(_row(appended[0]) + "\n")
                    appended[0] += 1
        return frame

    cache.set_reader(read)
    return csv


def _distinct_minus_issued() -> int:
    distinct = {(path, sep) for (path, _m, _s, sep) in cache.parse_log}
    return len(cache.parse_log) - len(distinct)


@pytest.mark.parametrize("rows_per_parse", [1, 3])
def test_run_backtest_reads_the_entity_once_while_it_is_appended(tmp_path, rows_per_parse) -> None:
    csv = _appended_csv(tmp_path, rows_per_parse)

    code, _result = run_backtest(
        output_dir=tmp_path / "out", data_path=str(csv), ea_name="CalcProbe_EA", symbol="JP225",
        period="H1", initial_deposit=100000.0, contract_size=1.0, volume_min=1.0,
        volume_max=100.0, volume_step=1.0, stops_level=0, digits=1, point_size=0.1,
        leverage=10.0, **_EA_PARAMS,
    )

    assert code == 0
    assert _distinct_minus_issued() == 0, cache.parse_log


@pytest.mark.parametrize("rows_per_parse", [1, 3])
def test_settings_run_reads_the_entity_once_while_it_is_appended(tmp_path, rows_per_parse) -> None:
    csv = _appended_csv(tmp_path, rows_per_parse)
    settings = custom_range_settings(
        date(2024, 1, 1), date(2024, 1, 3), Expert="CalcProbe_EA.ex5", Period="H1"
    )

    code, _result, _meta = run_from_settings(
        settings, engine_binding(data_path=str(csv), period="H1", ea_params=_EA_PARAMS)
    )

    assert code == 0
    assert _distinct_minus_issued() == 0, cache.parse_log
