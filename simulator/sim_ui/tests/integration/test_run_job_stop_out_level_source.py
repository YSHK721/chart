"""sim 画面の run は台帳の水準でストップアウトする（ISSUE-546・端から端まで）。

何を固定するか:
    sim 画面の run はストップアウト水準を渡さず、「`build_interactor`」 の既定 0.0 が入って
    いた。判定は `margin_level < stop_out_level` なので、強制決済は有効証拠金が負になる
    まで起きず、維持率 0.66% まで取引が続いて残高が負になった（実測・ジョブ 94aadcb4 /
    1d08008a）。参照実装（同じ口座の MT5 テスター）は維持率が口座の ``margin_so_so``
    （100%）を割った評価点で全玉を現値で決済し、テストを終える。

    受け口（「`build_interactor`」 の引数）だけを直しても、呼出側が渡さなければ無言で旧経路が
    生き残る（ISSUE-291 の型）。よって run_job の**両経路**（settings あり / なし）を実際に
    走らせ、実行トレース（宣言された観測境界 「`RunTracePort`」 の成果物）から次を確かめる:

    1. run が使った水準（trace_meta.json）が台帳の水準（実行プロファイル）である。
    2. 強制決済（halt）が起きる。
    3. halt より前の、建玉のある評価点の維持率はすべて水準以上である
       （＝水準を割った最初の評価点で決済している。0.0 なら割れたまま取引が続くので赤）。

    水準の値はここに書かない（実行プロファイル＝台帳から引く）。
"""
from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
import pytest

from simulator.sim_ui.adapter import chart_overlay_writer, trace_writer
from simulator.sim_ui.main import run_job
from simulator.sim_ui.main.composition_root_jobs import build_run_options_port

# settings 経路の素材の差し替え（実カタログの全期間実体を MT5 突合 fixture へ向ける）は
#   `test_run_job_settings.py` の継ぎ目をそのまま使う（同じ差し替えを 2 つ書かない）。
from simulator.sim_ui.tests.integration.test_run_job_settings import (  # noqa: F401
    _fixture_dataset,
)

_SYMBOL = "JP225"
_EPOCH = 1_704_067_200
_POINT = 0.1
_SPREAD = 3


def _ledger_level() -> float:
    profile = [p for p in build_run_options_port().datasets() if p.symbol == _SYMBOL][0]
    return float(profile.stop_out_level)


def _falling_csv(path: Path) -> Path:
    """横ばいのあと 1 本ごとに 5 ずつ下げる並び。

    MarginProbe は狙い値（下の 150%）以下になる最小の量を建てる。下げ続けると維持率は
    150% から下がり、水準（100%）を途中の足で割る。割る前に水準以上の評価点が並ぶ。
    """
    opens = [100.0] * 4 + [100.0 - 5.0 * k for k in range(1, 13)]
    rows = [
        {
            "time": _EPOCH + 60 * i, "open": o, "high": o + 1.0, "low": o - 1.0,
            "close": o, "volume": 100, "spread": _SPREAD,
        }
        for i, o in enumerate(opens)
    ]
    pd.DataFrame(rows).to_csv(path, index=False)
    return path


def _write_spec(job_dir: Path, *, backtest: dict, settings=None) -> Path:
    job_dir.mkdir(parents=True)
    (job_dir / "spec.json").write_text(
        json.dumps(
            {
                "backtest": backtest, "sizing": None, "strategy": None,
                "settings": settings, "trace": {"enabled": True},
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    return job_dir


def _run(job_dir: Path) -> None:
    code = run_job.main(["--job-dir", str(job_dir)])
    failure = job_dir / "failure.json"
    assert code == 0, failure.read_text(encoding="utf-8") if failure.exists() else code


def _observed(job_dir: Path) -> dict:
    """run の記録から、水準・halt の有無・halt 前の建玉中の維持率を読む。"""
    meta = json.loads((job_dir / trace_writer.META_FILENAME).read_text(encoding="utf-8"))
    points = pd.read_parquet(job_dir / trace_writer.POINTS_FILENAME)
    halted = points.index[points["halted"]]
    before = points.loc[: halted[0] - 1] if len(halted) else points
    holding = before[before["open_count"] > 0]
    declared = json.loads(
        (job_dir / chart_overlay_writer.CHART_BARS_DECLARATION_FILENAME).read_text(encoding="utf-8")
    )
    return {
        "level": meta[trace_writer.STOP_OUT_LEVEL_KEY],
        # 売買履歴チャートの宣言が名乗る水準（証拠金維持率の面の基準・2026-10-02）。
        "chart_bars_level": declared["stop_out_level"],
        "halts": len(halted),
        "holding_points": len(holding),
        "min_level_while_holding": float(holding["margin_level"].min()),
    }


def test_the_run_without_settings_stops_out_at_the_ledger_level(tmp_path: Path) -> None:
    """現行経路（settings なし）: 下げ続ける建玉が台帳の水準を割った点で強制決済される。"""
    csv = _falling_csv(tmp_path / "falling.csv")
    job_dir = _write_spec(
        tmp_path / ("0123456789abcdef" + "legacy".ljust(16, "0")),
        backtest={
            "ea_name": "MarginProbe_EA", "symbol": _SYMBOL, "period": "M1",
            "data_path": str(csv), "initial_deposit": 100_000.0, "contract_size": 10.0,
            "volume_min": 0.1, "volume_max": 10_000.0, "volume_step": 0.1, "stops_level": 0,
            "digits": 1, "point_size": _POINT, "leverage": 1.0, "ma_period": 3,
            "ma_method": "sma", "lot_size": 1.0, "stop_loss_points": 0,
            "take_profit_points": 0, "margin_level_target": 150.0,
            "config_overrides": {"tick_model": "open_only", "stop_out_action": "close_and_halt"},
        },
    )
    _run(job_dir)
    got = _observed(job_dir)
    assert got["level"] == _ledger_level()
    # 売買履歴チャートの宣言も trace_meta.json と同じ出所（run が使った水準）。
    assert got["chart_bars_level"] == got["level"]
    assert got["halts"] > 0, "強制決済が起きていない"
    assert got["holding_points"] > 0, "halt 前に建玉のある評価点が無い（検定が空虚）"
    assert got["min_level_while_holding"] >= _ledger_level()


def test_the_run_with_settings_stops_out_at_the_ledger_level(tmp_path: Path) -> None:
    """settings 経路: 本データセット・初期証拠金 10,000 JPY の run が台帳の水準で決済される。

    素材は `test_run_job_settings.py` の投入（同じ素材を 2 つ書かない）。同ファイルが
    「本データセット・本 EA は初期証拠金 10,000 JPY でストップアウトに達する」と記録している。
    """
    from simulator.sim_ui.tests.integration.test_run_job_settings import _backtest, _tester

    job_dir = _write_spec(
        tmp_path / ("0123456789abcdef" + "settings".ljust(16, "0")),
        backtest=_backtest(),
        settings={"tester": _tester(), "inputs": []},
    )
    _run(job_dir)
    got = _observed(job_dir)
    assert got["level"] == _ledger_level()
    # 売買履歴チャートの宣言も trace_meta.json と同じ出所（run が使った水準）。
    assert got["chart_bars_level"] == got["level"]
    assert got["halts"] > 0, "強制決済が起きていない"
    assert got["holding_points"] > 0, "halt 前に建玉のある評価点が無い（検定が空虚）"
    assert got["min_level_while_holding"] >= _ledger_level()


@pytest.mark.parametrize("with_settings", [False, True], ids=["legacy", "settings"])
def test_a_stop_out_level_in_the_spec_is_refused(tmp_path: Path, with_settings: bool) -> None:
    """投入に水準が載っていたら走らせない（台帳に優先させず、黙って捨てもしない）。"""
    from simulator.sim_ui.tests.integration.test_run_job_settings import _backtest, _tester

    job_dir = _write_spec(
        tmp_path / ("0123456789abcdef" + str(with_settings).ljust(16, "0")),
        backtest={**_backtest(), "stop_out_level": _ledger_level()},
        settings={"tester": _tester(), "inputs": []} if with_settings else None,
    )
    code = run_job.main(["--job-dir", str(job_dir)])
    # 走らせない（理由の文言は単体検定 `test_run_job_stop_out_level_rule.py` が固定する）。
    assert code != 0
    assert (job_dir / "failure.json").is_file()
    assert not (job_dir / "stats.json").exists()
