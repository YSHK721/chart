"""Section 5 integration: 薄い CLI（__main__・DESIGN §5.2）の end-to-end 検証。

CLI は `python -m backtest.main run --config <yaml> --data <csv> --output <dir>` を解析し
Composition Root（run_backtest）へ委譲する薄いラッパ。終了コード（0/2/1）を sys.exit へ
渡す（DESIGN §9.4）。CLI 本体は薄く保ち、DI 構築は build_interactor が担う。

yaml は determinism（config_loader へ）と meta（戦略/シンボルパラメータ）を分けて持つ。
"""
from __future__ import annotations

from pathlib import Path

import yaml

from backtest.main.__main__ import main as cli_main


_ROWS = [
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


def _write_config(path: Path) -> Path:
    cfg = {
        "determinism": {"tick_model": "ohlc_expand"},
        "meta": {
            "symbol": "EURUSD",
            "period": "M1",
            "ea_name": "TC24051901",
            "initial_deposit": 10_000.0,
            "contract_size": 1.0,
            "volume_min": 0.01,
            "volume_max": 100.0,
            "volume_step": 0.01,
            "digits": 5,
            "stops_level": 0,
            "point_size": 0.0001,
            "leverage": 100.0,
            "ma_period": 2,
            "ma_method": "sma",
            "lot_size": 1.0,
            "stop_loss_points": 500,
            "take_profit_points": 3000,
        },
    }
    path.write_text(yaml.safe_dump(cfg), encoding="utf-8")
    return path


class TestCli:
    def test_cli_success_returns_exit_code_0_and_writes_outputs(self, tmp_path):
        # Arrange
        csv_path = _write_csv(tmp_path / "m1.csv")
        cfg_path = _write_config(tmp_path / "config.yaml")
        out_dir = tmp_path / "result"
        argv = ["run", "--config", str(cfg_path), "--data", str(csv_path),
                "--output", str(out_dir)]
        # Act
        exit_code = cli_main(argv)
        # Assert: 成功 → 0・stats.json 生成
        assert exit_code == 0
        assert (out_dir / "stats.json").is_file()

    def test_cli_invalid_config_returns_exit_code_2(self, tmp_path):
        # Arrange: determinism に列挙外 tick_model
        csv_path = _write_csv(tmp_path / "m1.csv")
        cfg_path = tmp_path / "bad.yaml"
        cfg = yaml.safe_load(_write_config(tmp_path / "config.yaml").read_text())
        cfg["determinism"]["tick_model"] = "not_real"
        cfg_path.write_text(yaml.safe_dump(cfg), encoding="utf-8")
        argv = ["run", "--config", str(cfg_path), "--data", str(csv_path)]
        # Act / Assert: ConfigError → 2
        assert cli_main(argv) == 2

    def test_cli_missing_config_file_returns_exit_code_2(self, tmp_path):
        # Arrange: 存在しない --config パス（🟡-2 回帰）。設定読込失敗は ConfigError 相当
        # として exit 2 へ翻訳すべき（DESIGN §5.2「config.yaml をロード」・§9.4）。
        csv_path = _write_csv(tmp_path / "m1.csv")
        argv = ["run", "--config", str(tmp_path / "nope.yaml"), "--data", str(csv_path)]
        # Act / Assert: 例外を漏らさず exit 2
        assert cli_main(argv) == 2

    def test_cli_invalid_yaml_returns_exit_code_2(self, tmp_path):
        # Arrange: 不正 YAML（パース失敗）も設定起因のため exit 2（🟡-2 回帰）。
        csv_path = _write_csv(tmp_path / "m1.csv")
        bad_yaml = tmp_path / "bad_syntax.yaml"
        bad_yaml.write_text("determinism: [unclosed\n", encoding="utf-8")
        argv = ["run", "--config", str(bad_yaml), "--data", str(csv_path)]
        # Act / Assert
        assert cli_main(argv) == 2
