"""Section 5 integration テスト: Composition Root 経由の end-to-end 1 run（DESIGN §11）。

合成 OHLC（一時 CSV）を Composition Root（``simulator.main``）経由で実行し、
BacktestResult が生成され stats が算出され Presenter 出力が生成されることを検証する。

合成データは MADiff(SMA period=2) が bar2 で負→正（買い）・bar4 で正→負（売り反転）
にクロスするよう構築する。prototype 実測（Section 5 設計時）で確定した決定論的振る舞い:
    bar2 で買い建て → bar4 の反対シグナルで reverse 決済 → 確定トレード 1 件（pnl=-0.009）。

usecase/adapter/framework のコミット済コードは変更しない（読み取り・結線のみ）。
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

# Composition Root（未実装なら ModuleNotFoundError＝真の Red）
from simulator.main import build_interactor, run_backtest


# 合成 OHLC（MADiff SMA period=2 が bar2 で負→正・bar4 で正→負にクロス）
_ROWS = [
    # time,                  open,   high,   low,    close,  volume, spread
    ("2024-01-01T00:00:00", 1.1000, 1.1010, 1.0990, 1.0995, 1.0, 0),
    ("2024-01-01T00:01:00", 1.1000, 1.1010, 1.0985, 1.0990, 1.0, 0),
    ("2024-01-01T00:02:00", 1.0990, 1.1050, 1.0990, 1.1040, 1.0, 0),
    ("2024-01-01T00:03:00", 1.1040, 1.1100, 1.1040, 1.1090, 1.0, 0),
    ("2024-01-01T00:04:00", 1.1090, 1.1120, 1.0900, 1.0950, 1.0, 0),
    ("2024-01-01T00:05:00", 1.0950, 1.0960, 1.0900, 1.0920, 1.0, 0),
]


def _write_csv(path: Path) -> Path:
    lines = ["time,open,high,low,close,volume,spread"]
    for t, o, h, l, c, v, s in _ROWS:
        lines.append(f"{t},{o},{h},{l},{c},{v},{s}")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def _meta_kwargs(csv_path: Path) -> dict:
    """Composition Root に渡す run メタ（symbol/period/ea_name/spec/strategy params）。"""
    return dict(
        data_path=csv_path,
        symbol="EURUSD",
        period="M1",
        ea_name="TC24051901",
        initial_deposit=10_000.0,
        contract_size=1.0,
        volume_min=0.01,
        volume_max=100.0,
        volume_step=0.01,
        stops_level=0,
        digits=5,
        point_size=0.0001,
        leverage=100.0,
        ma_period=2,
        ma_method="sma",
        lot_size=1.0,
        stop_loss_points=500,
        take_profit_points=3000,
        config_overrides={"tick_model": "ohlc_expand"},
    )


class TestEndToEndRun:
    def test_composition_root_produces_result_with_stats(self, tmp_path):
        # Arrange: 合成 CSV を書き出す
        csv_path = _write_csv(tmp_path / "synth_m1.csv")
        # Act: Composition Root 経由で 1 run（exit_code, result）
        exit_code, result = run_backtest(**_meta_kwargs(csv_path))
        # Assert: 正常終了・確定トレード 1 件・stats が確定トレードから算出
        assert exit_code == 0
        assert result is not None
        assert result.stats.trades == 1
        assert len(result.trades) == 1
        assert result.trades[0].side == "buy"
        assert result.trades[0].exit_reason == "reverse"
        assert result.stats.profit == pytest.approx(result.trades[0].pnl())

    def test_composition_root_writes_presenter_outputs(self, tmp_path):
        # Arrange
        csv_path = _write_csv(tmp_path / "synth_m1.csv")
        out_dir = tmp_path / "result"
        # Act: 出力先を指定して run（Presenter/ResultSink へ書き出し）
        exit_code, result = run_backtest(output_dir=out_dir, **_meta_kwargs(csv_path))
        # Assert: stats.json と report.md が生成され、json は再 load で stats と一致
        assert exit_code == 0
        stats_json = out_dir / "stats.json"
        report_md = out_dir / "report.md"
        assert stats_json.is_file()
        assert report_md.is_file()
        payload = json.loads(stats_json.read_text(encoding="utf-8"))
        assert payload["stats"]["trades"] == result.stats.trades
        # Markdown レポートに ea_name が反映される（Presenter 結線の実証）
        assert "TC24051901" in report_md.read_text(encoding="utf-8")


class TestBuildInteractorUnit:
    def test_build_interactor_returns_input_boundary(self, tmp_path):
        # Arrange / Act: DI 構築関数を単体で呼ぶ（CLI から分離・薄い __main__ 方針）
        csv_path = _write_csv(tmp_path / "synth_m1.csv")
        from simulator.usecase.ports import RunBacktestInputBoundary

        controller, request = build_interactor(**_meta_kwargs(csv_path))
        # Assert: Interactor が RunBacktestInputBoundary を満たし request が結線される
        from simulator.usecase.run_backtest import RunBacktestRequest

        assert isinstance(controller._interactor, RunBacktestInputBoundary)
        assert isinstance(request, RunBacktestRequest)
        # meta が結線される: initial_deposit と symbol_spec が反映
        assert request.initial_deposit == 10_000.0
        assert request.symbol_spec.contract_size == 1.0
