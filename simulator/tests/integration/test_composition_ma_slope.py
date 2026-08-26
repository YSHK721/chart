"""Composition Root の config-gated 戦略選択テスト（cycle3 B）。

build_interactor が ea_name で戦略を選択できることを固定する:
    ea_name="MA_Slope_EA" → strategy=MaSlope・registry に "ema"(EMA(period,close)) 登録
    ea_name="TC24051901"（既定経路）→ strategy=TC24051901（後方互換・回帰）

既存 TC24051901 経路・既存テストは不変（config gated）。新結線は ea_name で gate。

戦略インスタンスは controller の interactor から参照する（build_interactor が返す
controller は _interactor 属性で interactor を保持する — main/__init__.py 既存契約）。
"""
from __future__ import annotations

from pathlib import Path

from simulator.adapter.strategy.ma_slope import MaSlope
from simulator.adapter.strategy.tc24051901 import TC24051901
from simulator.main import build_interactor

# MT5 形式の最小 CSV（タブ区切り）。EMA(20) 計算に足りる本数を与える。
_HEADER = "<DATE>\t<TIME>\t<OPEN>\t<HIGH>\t<LOW>\t<CLOSE>\t<TICKVOL>\t<VOL>\t<SPREAD>"


def _write_mt5_csv(path: Path, n: int = 30) -> Path:
    lines = [_HEADER]
    base = 39400.0
    for i in range(n):
        mm = f"{i:02d}"
        price = base + i  # 単調増加（昇順時刻・OHLC 整合）
        lines.append(
            f"2025.01.02\t01:{mm}:00\t{price}\t{price}\t{price}\t{price}\t1\t0\t100"
        )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def _ma_slope_kwargs(csv_path: Path) -> dict:
    """⚠ ISSUE-445 段階 B: 本モジュールは銘柄仕様の**正しさを検証していない**。

    下の `contract_size=10.0` ほか 5 項目は供給元スナップショット
    （`marketdata/symbol_specs/OANDA-Japan-MT5-Live/JP225.json`）と食い違うが、
    本モジュールが見るのは「どの strategy が選ばれたか」と「registry が ema を解決するか」
    だけで、いずれも `build_interactor` が backtest を**走らせない**段階で決まる。
    実測（2026-08-26）: `contract_size` だけを真値 1.0 にしても、5 項目を対で真値へ
    寄せても、3 検定とも緑のまま通る。

    したがって数値ピンを足す余地が無い（積 `lot × contract_size` が効く出力がここには
    無い）。段階 C は本モジュールの緑を「銘柄仕様の是正が正しい」根拠にしてはならない。
    損益への波及は `simulator/tests/unit/test_is_oos_barmode_index.py` の不変ピンが見る。
    """
    return dict(
        data_path=csv_path,
        symbol="JP225",
        period="M1",
        ea_name="MA_Slope_EA",
        initial_deposit=10_000.0,
        contract_size=10.0,
        volume_min=0.1,
        volume_max=100.0,
        volume_step=0.1,
        stops_level=0,
        digits=1,
        point_size=0.1,
        leverage=10.0,
        ma_period=20,
        ma_method="ema",
        lot_size=0.1,
        stop_loss_points=0,
        take_profit_points=0,
        slope_shift=1,
        slope_min_points=1.0,
        config_overrides={"tick_model": "open_only", "entry_price_basis": "current_open"},
    )


class TestCompositionMaSlope:
    def test_ma_slope_ea_selects_ma_slope_strategy(self, tmp_path):
        # Arrange
        csv_path = _write_mt5_csv(tmp_path / "mt5.csv")
        # Act
        controller, request = build_interactor(**_ma_slope_kwargs(csv_path))
        # Assert: ea_name="MA_Slope_EA" で MaSlope が選択される
        assert isinstance(controller._interactor._strategy, MaSlope)

    def test_ma_slope_registry_registers_ema_series(self, tmp_path):
        # Arrange
        csv_path = _write_mt5_csv(tmp_path / "mt5.csv")
        # Act
        controller, _ = build_interactor(**_ma_slope_kwargs(csv_path))
        # Assert: registry が "ema" を解決できる（MaSlope が get("ema") を参照）
        ema = controller._interactor._indicators.get("ema")
        assert ema is not None
        assert len(ema) == 30

    def test_tc24051901_path_unchanged(self, tmp_path):
        # Arrange: 既定経路（comma 形式 CSV・既存 build_interactor 契約）
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
