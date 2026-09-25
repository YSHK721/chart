"""約定価格が**判定の瞬間**と一致することの回帰（ISSUE-533 段階 1）。

測り方は ISSUE-533 の実測と同じである: 合成バーで始値と終値を 50 ポイント離し、約定価格が
どちらと一致するかを見る。始値の集合と終値の集合は交わらないので、「どちらの側で約定したか」
が 1 件ずつ判別できる。

固定する不変条件:
    1. 確定足だけを読む EA（MA_Slope_EA）の約定は**始値**側と一致し、終値側とは一致しない。
    2. 当該足の終値を読む EA（SmaTouchLong_EA）の約定は**終値**側と一致し、始値側とは一致しない。
    3. 建値基準を run の設定から与えていない（既定は置かない）。値は戦略の宣言から来る。

これが本段の成果そのものである。是正前は両 EA が同じ設定値で約定していたため、どちらかは
必ず「判定の瞬間に取得できない価格」で約定していた。
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

import pandas as pd

from simulator.main import build_interactor

#: 2024-01-01T00:00:00Z（comma 形式 CSV の 「`time`」 は epoch 秒 int が契約）。
_EPOCH_2024_01_01 = 1_704_067_200
#: 始値と終値の隔たり（ポイント）。判別可能にするだけの意味しか無い。
_GAP = 50.0
#: 終値の並び。上昇のあと急落させる（MA_Slope は傾きで、SmaTouchLong は SMA 下抜けで発注する）。
_CLOSES = [100.0 + i for i in range(12)] + [100.0, 101.0, 102.0]


def _write_csv(path: Path) -> Path:
    """始値を終値から `_GAP` だけ離した合成バーを書く（気配幅 0）。"""
    rows = []
    for i, close in enumerate(_CLOSES):
        open_ = close - _GAP
        rows.append(
            {
                "time": _EPOCH_2024_01_01 + 60 * i,
                "open": open_,
                "high": close + 1.0,
                "low": open_ - 1.0,
                "close": close,
                "volume": 100,
                "spread": 0,
            }
        )
    pd.DataFrame(rows).to_csv(path, index=False)
    return path


def _meta(csv_path: Path, ea_name: str, **overrides: Any) -> dict:
    base = dict(
        data_path=csv_path,
        symbol="SYNTH",
        period="M1",
        ea_name=ea_name,
        initial_deposit=100_000.0,
        contract_size=1.0,
        volume_min=1.0,
        volume_max=100.0,
        volume_step=1.0,
        stops_level=0,
        digits=1,
        point_size=0.1,
        leverage=100.0,
        ma_period=2,
        ma_method="ema",
        lot_size=1.0,
        stop_loss_points=0,
        take_profit_points=0,
        slope_shift=1,
        slope_min_points=1.0,
        # 建値基準は**渡さない**（戦略の宣言が権威である）。
        config_overrides={"tick_model": "open_only"},
    )
    base.update(overrides)
    return base


def _entry_prices(csv_path: Path, ea_name: str) -> "list[float]":
    controller, request = build_interactor(**_meta(csv_path, ea_name))
    result = controller.execute(request)
    return [float(t.entry_price) for t in result.trades]


def _opens() -> "set[float]":
    return {c - _GAP for c in _CLOSES}


def _closes() -> "set[float]":
    return set(_CLOSES)


def test_a_strategy_deciding_at_the_bar_open_fills_at_the_open(tmp_path: Path) -> None:
    """確定足だけを読む EA は始値で約定する（終値では約定しない）。"""
    # Arrange
    csv_path = _write_csv(tmp_path / "gap.csv")
    # Act
    entries = _entry_prices(csv_path, "MA_Slope_EA")
    # Assert
    assert entries, "トレードが 1 件も成立していない（検定が空虚）"
    assert set(entries) <= _opens()
    assert not set(entries) & _closes()


def test_a_strategy_deciding_at_the_bar_close_fills_at_the_close(tmp_path: Path) -> None:
    """当該足の終値を読む EA は終値で約定する（始値では約定しない）。"""
    # Arrange
    csv_path = _write_csv(tmp_path / "gap.csv")
    # Act
    entries = _entry_prices(csv_path, "SmaTouchLong_EA")
    # Assert
    assert entries, "トレードが 1 件も成立していない（検定が空虚）"
    assert set(entries) <= _closes()
    assert not set(entries) & _opens()
