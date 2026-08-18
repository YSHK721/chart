"""Section 5 integration: 終了コード（0/2/1）の end-to-end 検証。

- 終了コード: 正常→0 / ConfigError→2 / BacktestError→1（DESIGN §9.4・controller の翻訳を利用）。

usecase/adapter/framework のコミット済コードは変更しない（結線のみ）。
"""
from __future__ import annotations

from pathlib import Path

from simulator.main import run_backtest


_ROWS = [
    # time は UNIX 秒 int（UTC・2024-01-01T00:00:00Z=1704067200）。comma 形式 CSV の `time` は epoch 秒が契約であり（Candle 契約 §2.1）、ISO 文字列は `Bar.time` 契約違反になる。
    (1704067200, 1.1000, 1.1010, 1.0990, 1.0995, 1.0, 0),
    (1704067260, 1.1000, 1.1010, 1.0985, 1.0990, 1.0, 0),
    (1704067320, 1.0990, 1.1050, 1.0990, 1.1040, 1.0, 0),
    (1704067380, 1.1040, 1.1100, 1.1040, 1.1090, 1.0, 0),
    (1704067440, 1.1090, 1.1120, 1.0900, 1.0950, 1.0, 0),
    (1704067500, 1.0950, 1.0960, 1.0900, 1.0920, 1.0, 0),
]


def _write_csv(path: Path, rows=_ROWS) -> Path:
    lines = ["time,open,high,low,close,volume,spread"]
    for t, o, h, l, c, v, s in rows:
        lines.append(f"{t},{o},{h},{l},{c},{v},{s}")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def _meta(csv_path: Path) -> dict:
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


class TestExitCodes:
    def test_success_returns_exit_code_0(self, tmp_path):
        csv_path = _write_csv(tmp_path / "ok.csv")
        exit_code, _ = run_backtest(**_meta(csv_path))
        assert exit_code == 0

    def test_invalid_config_returns_exit_code_2(self, tmp_path):
        # Arrange: 列挙外の tick_model（config_loader が ConfigError へ翻訳）
        csv_path = _write_csv(tmp_path / "ok.csv")
        meta = _meta(csv_path)
        meta["config_overrides"] = {"tick_model": "not_a_real_model"}
        # Act
        exit_code, result = run_backtest(**meta)
        # Assert: ConfigError → 2・部分結果なし
        assert exit_code == 2
        assert result is None

    def test_data_error_returns_exit_code_1(self, tmp_path):
        # Arrange: 存在しない CSV パス（CsvOHLCRepository が DataError へ翻訳）
        missing = tmp_path / "does_not_exist.csv"
        meta = _meta(missing)
        # Act
        exit_code, result = run_backtest(**meta)
        # Assert: BacktestError(DataError) → 1・部分結果なし
        assert exit_code == 1
        assert result is None

    def test_output_write_failure_returns_exit_code_1(self, tmp_path):
        # Arrange: output_dir の親をファイルにして mkdir/write を失敗させる（🟡-1 回帰）。
        # 出力 I/O 失敗はトレースバックを貫通させず BacktestError → exit 1 へ翻訳すべき
        # （DESIGN §9.4）。run 本体は成功するため exit_code は出力段階の失敗で決まる。
        csv_path = _write_csv(tmp_path / "ok.csv")
        blocker = tmp_path / "blocker"
        blocker.write_text("not a dir", encoding="utf-8")
        meta = _meta(csv_path)
        # Act: output_dir の親（blocker）がファイル → mkdir で NotADirectoryError
        exit_code, _ = run_backtest(output_dir=blocker / "result", **meta)
        # Assert: 例外を漏らさず exit 1 を返す
        assert exit_code == 1
