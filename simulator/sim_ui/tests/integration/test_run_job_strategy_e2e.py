"""戦略項目 end-to-end 結合検定（Phase 6 F-8・投入→実行→結果反映・決定性）.

run_job.main（子プロセス CLI 本体）を spec.json 経由で回し、実バックテストの出力
（stats.json）で以下を固定する:
    1. 条件由来の建玉が結果に反映される（発火する条件は 0 件でない結果を生む）。
    2. 発火しない条件は建玉ゼロ（＝条件が実際に評価されている＝写経でない）。
    3. 同一 spec を 2 回投入すると結果が byte 一致（決定性）。
    4. strategy 不在は override 無しの直接実行と byte 一致（OFF＝byte 等価）。

方式: 小さな CSV を実際に流す（合成データ・軽量）。register は ea_name=TC24051901 の
{madiff, close}（override は TC 戦略を置換するが registry は再利用する）。
"""
from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

from simulator.main import run_backtest
from simulator.sim_ui.main import run_job


#: 2024-01-01T00:00:00Z。comma 形式 CSV の `time` は UNIX 秒 int が契約である
#: （`Bar.time` = ``numpy.datetime64`` | epoch int。`CsvOHLCRepository._extract` は CSV の値を
#: **そのまま** `Bar.time` に載せるため、ISO 文字列を書くと契約違反の Bar が生まれる。
#: 委譲経路 `CsvCandleSource` は同じ CSV を ValueError で fail-fast する＝経路で解釈が割れる）。
_EPOCH_2024_01_01 = 1_704_067_200


def _write_csv(path: Path) -> Path:
    # 上昇トレンド（long の TP が必ず引っかかる）。close は 1.0000 から +0.0010/bar。
    rows = []
    for i in range(40):
        base = 1.0000 + i * 0.0010
        rows.append(
            {
                # 是正前 "2024-01-01 {i//60:02d}:{i%60:02d}:00" と同一時刻の epoch 秒（UTC）。
                "time": _EPOCH_2024_01_01 + 3600 * (i // 60) + 60 * (i % 60),
                "open": base,
                "high": base + 0.0005,
                "low": base - 0.0005,
                "close": base,
                "volume": 100,
                "spread": 0,
            }
        )
    pd.DataFrame(rows).to_csv(path, index=False)
    return path


def _backtest(csv: Path) -> dict:
    return {
        "ea_name": "TC24051901",
        "symbol": "EURUSD",
        "period": "M1",
        "data_path": str(csv),
        "initial_deposit": 100_000.0,
        "contract_size": 1.0,
        "volume_min": 0.01,
        "volume_max": 100.0,
        "volume_step": 0.01,
        "stops_level": 0,
        "digits": 5,
        "point_size": 0.0001,
        "leverage": 100.0,
        "ma_period": 2,
        "ma_method": "sma",
        "lot_size": 1.0,
        "stop_loss_points": 100,
        "take_profit_points": 200,
    }


# 発火する条件（close は常に正）／発火しない条件（close は 1e9 未満）。
_FIRING = {"entry_long": [{"indicator": "close", "shift": 0, "op": ">", "rhs": 0.0}]}
_NEVER = {"entry_long": [{"indicator": "close", "shift": 0, "op": ">", "rhs": 1.0e9}]}


def _run_job_with(tmp: Path, name: str, csv: Path, *, strategy) -> dict:
    job_dir = tmp / ("0123456789abcdef" + name.ljust(16, "0")[:16])
    job_dir.mkdir()
    (job_dir / "spec.json").write_text(
        json.dumps({"backtest": _backtest(csv), "sizing": None, "strategy": strategy}),
        encoding="utf-8",
    )
    code = run_job.main(["--job-dir", str(job_dir)])
    assert code == 0, f"run_job failed: {(job_dir / 'failure.json').read_text() if (job_dir / 'failure.json').exists() else code}"
    return json.loads((job_dir / "stats.json").read_text(encoding="utf-8"))


def _trades(stats: dict) -> int:
    # stats.json の件数フィールド（BacktestStats.trades）。構造差異に頑健化して探す。
    for key in ("trades",):
        if key in stats:
            return int(stats[key])
    # ネスト形式（{"stats": {...}}）にも対応。
    return int(stats.get("stats", {}).get("trades", 0))


def test_firing_condition_produces_trades(tmp_path: Path) -> None:
    csv = _write_csv(tmp_path / "m1.csv")
    stats = _run_job_with(tmp_path, "fire", csv, strategy=_FIRING)
    assert _trades(stats) > 0


def test_non_firing_condition_produces_no_trades(tmp_path: Path) -> None:
    csv = _write_csv(tmp_path / "m1.csv")
    stats = _run_job_with(tmp_path, "never", csv, strategy=_NEVER)
    assert _trades(stats) == 0


def test_same_spec_twice_is_bit_identical(tmp_path: Path) -> None:
    csv = _write_csv(tmp_path / "m1.csv")
    a = _run_job_with(tmp_path, "detA", csv, strategy=_FIRING)
    b = _run_job_with(tmp_path, "detB", csv, strategy=_FIRING)
    # 決定性: 同一 spec 再実行で結果が完全一致
    assert json.dumps(a, sort_keys=True) == json.dumps(b, sort_keys=True)


def _run_job_full_spec(tmp: Path, name: str, csv: Path, *, strategy, sizing) -> tuple:
    job_dir = tmp / ("0123456789abcdef" + name.ljust(16, "0")[:16])
    job_dir.mkdir()
    (job_dir / "spec.json").write_text(
        json.dumps({"backtest": _backtest(csv), "sizing": sizing, "strategy": strategy}),
        encoding="utf-8",
    )
    code = run_job.main(["--job-dir", str(job_dir)])
    return code, job_dir


def test_strategy_and_sizing_compose_end_to_end(tmp_path: Path) -> None:
    # override（GenericConditionStrategy・SL 付き）× sizing 併用が実経路で成立する。
    # build_interactor が override を置換→sizing decorator で包む合成順を e2e で固定する。
    csv = _write_csv(tmp_path / "m1.csv")
    code, job_dir = _run_job_full_spec(
        tmp_path, "combo", csv, strategy=_FIRING, sizing={"enabled": True, "sims": 5}
    )
    reason = (job_dir / "failure.json").read_text() if (job_dir / "failure.json").exists() else ""
    assert code == 0, f"strategy×sizing 併用が失敗: {reason or code}"
    assert (job_dir / "stats.json").is_file()


def test_strategy_and_sizing_are_deterministic(tmp_path: Path) -> None:
    csv = _write_csv(tmp_path / "m1.csv")
    _c1, d1 = _run_job_full_spec(
        tmp_path, "cmbA", csv, strategy=_FIRING, sizing={"enabled": True, "sims": 5}
    )
    _c2, d2 = _run_job_full_spec(
        tmp_path, "cmbB", csv, strategy=_FIRING, sizing={"enabled": True, "sims": 5}
    )
    a = (d1 / "stats.json").read_text(encoding="utf-8")
    b = (d2 / "stats.json").read_text(encoding="utf-8")
    assert json.dumps(json.loads(a), sort_keys=True) == json.dumps(json.loads(b), sort_keys=True)


def test_strategy_absent_is_byte_equivalent_to_direct_run(tmp_path: Path) -> None:
    # Arrange: strategy 不在の run_job 出力
    csv = _write_csv(tmp_path / "m1.csv")
    via_job = _run_job_with(tmp_path, "off", csv, strategy=None)
    # override 無しの直接実行（strategy 不在の既存経路）
    out = tmp_path / "direct"
    out.mkdir()
    code, _ = run_backtest(output_dir=out, **_backtest(csv))
    assert code == 0
    direct = json.loads((out / "stats.json").read_text(encoding="utf-8"))
    # Assert: OFF は override 無しの既存経路と byte 一致
    assert json.dumps(via_job, sort_keys=True) == json.dumps(direct, sort_keys=True)
