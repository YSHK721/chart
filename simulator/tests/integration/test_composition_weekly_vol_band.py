"""Composition Root の WeeklyVolBand_EA 結線回帰テスト（詳細設計 §11・§2.1）。

build_interactor が ea_name="WeeklyVolBand_EA" で WeeklyVolBand 戦略を解決できることを
固定する（標準 composition root から週次ボラ・バンド戦略を生成）。週単位セグメント
orchestration（run_weekly_segments）は tools/usecase 側に保ち、build_interactor は
戦略生成のみを担う（詳細設計 §4.3 D1）。

回帰目的（memory: bugfix-pair-with-regression-test）: 配線が壊れたら（分岐削除・既定
経路への誤フォールバック）本テストが落ちる。併せて既定 TC24051901 経路の不変
（後方互換）を 1 件で確認し、新分岐追加が既存戦略解決を破壊しないことを担保する。

戦略インスタンスは controller の interactor から参照する（build_interactor が返す
controller は _interactor._strategy で戦略を保持する — main/__init__.py 既存契約）。
"""
from __future__ import annotations

from pathlib import Path

from simulator.adapter.strategy.tc24051901 import TC24051901
from simulator.adapter.strategy.weekly_vol_band import WeeklyVolBand
from simulator.domain.variance_forecast import VarianceForecast
from simulator.main import build_interactor


def _write_csv(path: Path) -> Path:
    """週セグメント先頭 open を持つ最小の comma 形式 CSV（昇順時刻・OHLC 整合）。"""
    rows = [
        "time,open,high,low,close,volume,spread",
        # time は UNIX 秒 int（UTC・2024-02-12T00:00:00Z=1707696000）。同上（Candle 契約 §2.1）。
        "1707696000,100.0,100.5,99.5,100.2,1.0,0",
        "1707696060,100.2,100.8,100.0,100.6,1.0,0",
        "1707696120,100.6,101.0,100.4,100.9,1.0,0",
    ]
    path.write_text("\n".join(rows) + "\n", encoding="utf-8")
    return path


def _forecast() -> VarianceForecast:
    return VarianceForecast(
        week_id="2024-W07",
        sigma_plus=0.025,
        sigma_minus=0.020,
        sigma_total_prev=0.030,
        estimable=True,
    )


def _weekly_kwargs(csv_path: Path) -> dict:
    return dict(
        data_path=csv_path,
        symbol="JP225",
        period="M1",
        ea_name="WeeklyVolBand_EA",
        initial_deposit=1_000_000.0,
        contract_size=1.0,
        volume_min=0.0,
        volume_max=1_000_000.0,
        volume_step=0.0,
        stops_level=0,
        digits=2,
        point_size=0.01,
        leverage=10.0,
        ma_period=2,
        ma_method="sma",
        lot_size=1.0,
        stop_loss_points=0,
        take_profit_points=0,
        weekly_forecast=_forecast(),
        weekly_p_tp=0.50,
        weekly_capital=1_000_000.0,
        weekly_f_risk=0.01,
    )


class TestCompositionWeeklyVolBand:
    def test_weekly_vol_band_ea_selects_weekly_vol_band_strategy(self, tmp_path):
        # Arrange
        csv_path = _write_csv(tmp_path / "weekly.csv")
        # Act
        controller, _request = build_interactor(**_weekly_kwargs(csv_path))
        # Assert: ea_name="WeeklyVolBand_EA" で WeeklyVolBand が選択される（回帰）
        assert isinstance(controller._interactor._strategy, WeeklyVolBand)

    def test_weekly_vol_band_registry_registers_open_series(self, tmp_path):
        # Arrange
        csv_path = _write_csv(tmp_path / "weekly.csv")
        # Act
        controller, _ = build_interactor(**_weekly_kwargs(csv_path))
        # Assert: registry が "open" を解決できる（WeeklyVolBand が get("open") を参照）
        open_series = controller._interactor._indicators.get("open")
        assert open_series is not None
        assert float(open_series.iloc[0]) == 100.0

    def test_tc24051901_path_unchanged(self, tmp_path):
        # Arrange: 既定経路（comma 形式 CSV・新分岐追加後も後方互換）
        csv = tmp_path / "synth.csv"
        rows = [
            "time,open,high,low,close,volume,spread",
            # time は UNIX 秒 int（UTC・2024-01-01T00:00:00Z=1704067200）。comma 形式 CSV の `time` は epoch 秒が契約であり（Candle 契約 §2.1）、ISO 文字列は `Bar.time` 契約違反になる。
            "1704067200,1.1,1.101,1.099,1.0995,1.0,0",
            "1704067260,1.1,1.101,1.0985,1.099,1.0,0",
            "1704067320,1.099,1.105,1.099,1.104,1.0,0",
        ]
        csv.write_text("\n".join(rows) + "\n", encoding="utf-8")
        # Act
        controller, _ = build_interactor(
            data_path=csv,
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
        # Assert: 既定経路は従来どおり TC24051901（後方互換）
        assert isinstance(controller._interactor._strategy, TC24051901)
