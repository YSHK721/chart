"""settings 経路に Phase 6 / Phase 7 の拡張が効くことの結合検定（スライス 4・T-1）。

固定する不変条件: `run_settings_job` の ``extensions`` は `build_interactor` の拡張点
（`strategy_override` / `position_manager` / `strategy_decorator`）へ**実際に届く**。

なぜ必要か（実測された壊れ方の型・ISSUE-291）: 受け口だけを作って呼出側が渡さないと、
拡張は無音で不作動になる。「settings 経路では戦略条件が効かない」は結果の数字にしか
現れないため、**有無で結果が変わること**で固定する（引数の有無を覗くだけでは、渡した先で
捨てられていても緑になる）。

方式: 小さな合成 CSV（comma 形式）を実際に流す。EA は `TC24051901`（registry に
{madiff, close} を持つ＝条件が参照できる）。銘柄名は JP225 とする——`.ini` の `Currency`
（口座通貨）と突き合わせる決済通貨の供給源が `SymbolSpecCatalog` の profile であり、
登録の無い銘柄名では供給できない（推定値を発明しない設計）。銘柄仕様の数値は合成 CSV の
価格帯（1.0000 前後）に合わせた値を `backtest` が供給する。
"""
from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

from simulator.sim_ui.main import run_job
from simulator.sim_ui.main.composition_root_jobs import build_run_options_port
from simulator.tests.tester_settings_engine_fixtures import runnable_expert_mapping

#: 2024-01-01T00:00:00Z。comma 形式 CSV の `time` は UNIX 秒 int が契約
#: （`test_run_job_strategy_e2e` と同じ理由・同じ基点）。
_EPOCH_2024_01_01 = 1_704_067_200

_EA_NAME = "TC24051901"

#: 発火する条件（close は常に正）。参照系列は TC24051901 の registry にある。
_FIRING = {"entry_long": [{"indicator": "close", "shift": 0, "op": ">", "rhs": 0.0}]}


def _write_csv(path: Path) -> Path:
    """上昇トレンド（long が TP に届く）。`test_run_job_strategy_e2e` と同形。"""
    rows = []
    for index in range(40):
        base = 1.0000 + index * 0.0010
        rows.append(
            {
                "time": _EPOCH_2024_01_01 + 60 * index,
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


def _symbol() -> str:
    """決済通貨の供給源になる profile の銘柄名（カタログが権威）。"""
    return build_run_options_port().datasets()[0].symbol


def _backtest(csv: Path) -> dict:
    return {
        "ea_name": _EA_NAME,
        "symbol": _symbol(),
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
        # 建値基準系列は TC24051901 の registry にある `close` を使う
        # （settings 経路の既定 `current_open` は本 EA に系列が無い）。
        "config_overrides": {"entry_price_basis": "close"},
    }


def _settings() -> dict:
    return {
        "tester": runnable_expert_mapping(
            Expert=f"{_EA_NAME}.ex5",
            Symbol=_symbol(),
            Period="M1",
            Leverage="100",
        ),
        "inputs": [],
    }


def _run(tmp_path: Path, name: str, *, strategy) -> dict:
    job_dir = tmp_path / name
    job_dir.mkdir()
    csv = _write_csv(job_dir / "m1.csv")
    (job_dir / "spec.json").write_text(
        json.dumps(
            {
                "backtest": _backtest(csv),
                "sizing": None,
                "strategy": strategy,
                "settings": _settings(),
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    code = run_job.main(["--job-dir", str(job_dir)])
    failure = job_dir / "failure.json"
    assert code == 0, failure.read_text(encoding="utf-8") if failure.is_file() else code
    stats = json.loads((job_dir / "stats.json").read_text(encoding="utf-8"))["stats"]
    # **settings 経路を通ったことの証明**（これが無いと、settings を読み飛ばして現行経路で
    # 走っても本検定は緑になる＝拡張の結線を何も固定できない）。`Deposit` は `.ini` が権威
    # であり、`backtest.initial_deposit` とは別の値にしてある。
    assert stats["initial_deposit"] == float(_settings()["tester"]["Deposit"])
    assert stats["initial_deposit"] != _backtest(csv)["initial_deposit"]
    return stats


def test_settings経路でstrategy_overrideが効く(tmp_path: Path) -> None:
    """Phase 6: 条件を与えた run だけが建玉を出す（渡した先で捨てられていない）。"""
    # Arrange / Act
    without = _run(tmp_path, "p6_off", strategy=None)
    with_override = _run(tmp_path, "p6_on", strategy=_FIRING)
    # Assert
    assert without["trades"] == 0
    assert with_override["trades"] > 0


def test_settings経路でposition_managerが効く(tmp_path: Path) -> None:
    """Phase 7: 部分決済を足すと結果が変わる（建玉の推移が別物になる）。"""
    # Arrange
    partial = dict(_FIRING)
    partial["partial_close"] = {"trigger": {"profit_points": 10}, "close_fraction": 0.5}
    # Act
    plain = _run(tmp_path, "p7_off", strategy=_FIRING)
    managed = _run(tmp_path, "p7_on", strategy=partial)
    # Assert
    assert plain["trades"] > 0
    assert managed["trades"] != plain["trades"], "position_manager が結果に効いていない"
