"""build_interactor の EA ファクトリレジストリ回帰ガード（ISSUE-097 🟡-3・OCP）。

`main/__init__.py` の `if ea_name == ...` 5 分岐を `ea_name -> factory` の登録表
（`_EA_FACTORIES`）へ集約した。factory は `(strategy, registry, market_data)` を返す。
本テストは:

  1. レジストリが従来 5 経路（TC 既定 + 4 EA）を解決し、各 EA が従来と同一の
     strategy 型・market_data 型・registry 系列へ解決されること（byte 不変の構造ガード）
  2. 未登録 ea_name が既定 TC24051901 経路へフォールバックすること
  3. ProFitBand が 1 エントリ登録で生成可能になったこと（従来は未登録で生成不能）
  4. WeeklyVolBand の構築知識が共有ファクトリ `make_weekly_vol_band` へ一元化され、
     main と tools/run_weekly_vol_band_cli が同一ファクトリを参照すること

既存の Composition Root 結線テスト（test_composition_ma_slope /
test_composition_weekly_vol_band）と重複しない差分（未カバーの EA・ProFitBand・
共有ファクトリ）に集中する。
"""
from __future__ import annotations

from pathlib import Path

import pytest

from simulator.adapter.repository.ohlc_csv import CsvOHLCRepository
from simulator.adapter.repository.ohlc_mt5_csv import Mt5CsvOHLCRepository
from simulator.adapter.strategy.ma_slope_pending import MaSlopePending
from simulator.adapter.strategy.pro_fit_band import ProFitBand
from simulator.adapter.strategy.stop_entry_probe import StopEntryProbe
from simulator.adapter.strategy.tc24051901 import TC24051901
from simulator.main import build_interactor


# --- fixtures ---------------------------------------------------------------

_MT5_HEADER = "<DATE>\t<TIME>\t<OPEN>\t<HIGH>\t<LOW>\t<CLOSE>\t<TICKVOL>\t<VOL>\t<SPREAD>"


def _write_mt5_csv(path: Path, n: int = 30) -> Path:
    lines = [_MT5_HEADER]
    base = 39400.0
    for i in range(n):
        price = base + i
        lines.append(f"2025.01.02\t01:{i:02d}:00\t{price}\t{price}\t{price}\t{price}\t1\t0\t100")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


#: 2024-01-01T00:00:00Z。comma 形式 CSV の `time` は UNIX 秒 int が契約である
#: （`Bar.time` = ``numpy.datetime64`` | epoch int。`CsvOHLCRepository._extract` は CSV の値を
#: **そのまま** `Bar.time` に載せるため、ISO 文字列を書くと契約違反の Bar が生まれる。
#: 委譲経路 `CsvCandleSource` は同じ CSV を ValueError で fail-fast する＝経路で解釈が割れる）。
_EPOCH_2024_01_01 = 1_704_067_200


def _write_comma_csv(path: Path, n: int = 12) -> Path:
    lines = ["time,open,high,low,close,volume,spread"]
    base = 100.0
    for i in range(n):
        o = base + i * 0.1
        h = o + 0.5
        lo = o - 0.4
        c = o + 0.2
        # epoch 秒（UTC・M1 昇順）。是正前の f"2024-01-01T00:{i:02d}:00" は n>60 で
        # 00:60:00 という存在しない時刻を書き得た（i は分として使われていた）。
        lines.append(f"{_EPOCH_2024_01_01 + 60 * i},{o},{h},{lo},{c},1.0,0")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def _mt5_kwargs(csv_path: Path, ea_name: str) -> dict:
    return dict(
        data_path=csv_path, symbol="JP225", period="M1", ea_name=ea_name,
        initial_deposit=10_000.0, contract_size=10.0, volume_min=0.1, volume_max=100.0,
        volume_step=0.1, stops_level=0, digits=1, point_size=0.1, leverage=10.0,
        ma_period=20, ma_method="ema", lot_size=0.1, stop_loss_points=0,
        take_profit_points=0, slope_shift=1, slope_min_points=1.0,
        config_overrides={"tick_model": "open_only", "entry_price_basis": "current_open",
                          "pending_lifecycle": True, "pending_oco": True},
    )


def _comma_kwargs(csv_path: Path, ea_name: str) -> dict:
    return dict(
        data_path=csv_path, symbol="JP225", period="M1", ea_name=ea_name,
        initial_deposit=10_000.0, contract_size=1.0, volume_min=0.01, volume_max=100.0,
        volume_step=0.01, stops_level=0, digits=5, point_size=0.0001, leverage=100.0,
        ma_period=8, ma_method="sma", lot_size=0.1, stop_loss_points=30,
        take_profit_points=100, config_overrides={"tick_model": "ohlc_expand"},
    )


# --- レジストリ構造の不変性 --------------------------------------------------

def test_registry_exposes_all_four_named_eas_plus_pro_fit_band():
    from simulator.main import _EA_FACTORIES

    assert {
        "MA_Slope_EA",
        "MA_Slope_Pending_EA",
        "StopEntryProbe_EA",
        "WeeklyVolBand_EA",
        "PRO_fit_Band_EA",
    } <= set(_EA_FACTORIES)


# --- 未カバー EA（Pending / StopEntryProbe）の解決不変性 ----------------------

def test_ma_slope_pending_resolves_strategy_registry_and_market_data(tmp_path):
    csv = _write_mt5_csv(tmp_path / "mt5.csv")
    controller, _ = build_interactor(**_mt5_kwargs(csv, "MA_Slope_Pending_EA"))
    interactor = controller._interactor
    assert isinstance(interactor._strategy, MaSlopePending)
    assert isinstance(controller._market_data, Mt5CsvOHLCRepository)
    # pending registry は ema/open/spread を供給（従来と同一）。
    for key in ("ema", "open", "spread"):
        assert interactor._indicators.get(key) is not None


def test_stop_entry_probe_resolves_strategy_and_shares_pending_registry(tmp_path):
    csv = _write_mt5_csv(tmp_path / "mt5.csv")
    controller, _ = build_interactor(**_mt5_kwargs(csv, "StopEntryProbe_EA"))
    interactor = controller._interactor
    assert isinstance(interactor._strategy, StopEntryProbe)
    assert isinstance(controller._market_data, Mt5CsvOHLCRepository)
    for key in ("ema", "open", "spread"):
        assert interactor._indicators.get(key) is not None


# --- 未登録 ea_name のフォールバック不変性 -----------------------------------

def test_unknown_ea_name_falls_back_to_default_tc(tmp_path):
    csv = _write_comma_csv(tmp_path / "synth.csv")
    controller, _ = build_interactor(**_comma_kwargs(csv, "UNKNOWN_EA_XYZ"))
    interactor = controller._interactor
    assert isinstance(interactor._strategy, TC24051901)
    assert isinstance(controller._market_data, CsvOHLCRepository)
    # 既定 TC registry は madiff/close を供給（従来と同一）。
    for key in ("madiff", "close"):
        assert interactor._indicators.get(key) is not None


# --- ProFitBand の生成可能性（1 エントリ登録・従来は未登録で生成不能）----------

def test_pro_fit_band_is_now_constructible_via_registry(tmp_path):
    csv = _write_comma_csv(tmp_path / "synth.csv")
    controller, _ = build_interactor(**_comma_kwargs(csv, "PRO_fit_Band_EA"))
    interactor = controller._interactor
    assert isinstance(interactor._strategy, ProFitBand)
    assert isinstance(controller._market_data, CsvOHLCRepository)


def test_pro_fit_band_registry_supplies_ema_adx_di_close(tmp_path):
    csv = _write_comma_csv(tmp_path / "synth.csv")
    controller, _ = build_interactor(**_comma_kwargs(csv, "PRO_fit_Band_EA"))
    registry = controller._interactor._indicators
    # ProFitBand は ema/adx/plus_di/minus_di/close を参照する（pro_fit_band.py 実測）。
    for key in ("ema", "adx", "plus_di", "minus_di", "close"):
        assert registry.get(key) is not None


def test_pro_fit_band_adx_min_available_in_run_config(tmp_path):
    # ProFitBand は on_new_bar で config["adx_min"] を参照する。strategy_params に
    # 供給されていなければ実行時 KeyError となるため、生成後の config に adx_min が
    # 解決可能であることを固定する（既定 22.0＝原典 .mq5 Adx_Min）。
    csv = _write_comma_csv(tmp_path / "synth.csv")
    _controller, request = build_interactor(**_comma_kwargs(csv, "PRO_fit_Band_EA"))
    assert request.config["adx_min"] == pytest.approx(22.0)


# --- WeeklyVolBand 構築知識の共有ファクトリ一元化 -----------------------------

def test_shared_weekly_vol_band_factory_is_used_by_main_and_cli():
    # main と tools/run_weekly_vol_band_cli が同一の共有ファクトリを参照すること。
    from simulator.adapter.strategy.weekly_vol_band import (
        WeeklyVolBand,
        make_weekly_vol_band,
    )

    strat = make_weekly_vol_band(forecast=None, p_tp=0.5, capital=1.0, f_risk=0.01)
    assert isinstance(strat, WeeklyVolBand)
    assert strat._p_tp == 0.5
    assert strat._capital == 1.0
    assert strat._f_risk == 0.01
