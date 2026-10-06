"""EMA_Deviation_Short_EA を build_interactor から通しで動かし、独立計算と照合する。

正解の出どころ（エンジン・戦略の関数を呼ばない）:
    EMA:   α = 2/(n+1)・先頭の終値でシード（MQL 忠実 EMA）
    水準:  形成中の EMA から d 上方に触れる価格 = ema[i-1]·(1+d)(1−a)/(1−(1+d)a) を刻みへ切り上げ
    約定:  sell_limit は Bid が水準に届いた足で、水準の価格で約定（足の途中）
    決済:  SL = 建値 + SL 距離 / TP = 建値 − TP 距離
"""
from __future__ import annotations

import math
from pathlib import Path

import pytest

from simulator.main import build_interactor
from simulator.tests.ledger_stop_out import ledger_stop_out_level

_PERIOD = 21
_PCT = 8.0
_POINT = 0.1
_SL_PTS = 3000   # 300.0
_TP_PTS = 5000   # 500.0

_COMMON = dict(
    symbol="JP225", period="M1", initial_deposit=10_000_000.0, contract_size=1.0,
    volume_min=1.0, volume_max=100.0, volume_step=1.0, stops_level=0, digits=1,
    point_size=_POINT, leverage=100.0, stop_out_level=ledger_stop_out_level(),
    ma_period=_PERIOD, ma_method="ema", lot_size=1.0, ema_deviation_pct=_PCT,
    stop_loss_points=_SL_PTS, take_profit_points=_TP_PTS,
)


def _write_csv(path: Path, bars: "list[tuple[float, float, float, float]]") -> Path:
    rows = ["date,open,high,low,close,volume,spread"]
    for i, (o, h, lo, c) in enumerate(bars):
        rows.append(f"2024-01-02 {i // 60:02d}:{i % 60:02d}:00,{o},{h},{lo},{c},1,10")
    path.write_text("\n".join(rows) + "\n", encoding="utf-8")
    return path


def _ema(closes: "list[float]") -> "list[float]":
    a = 2.0 / (_PERIOD + 1)
    out = [closes[0]]
    for c in closes[1:]:
        out.append(a * c + (1 - a) * out[-1])
    return out


def _level(ema_prev: float) -> float:
    a = 2.0 / (_PERIOD + 1)
    up = 1.0 + _PCT / 100.0
    exact = ema_prev * up * (1 - a) / (1 - up * a)
    return math.ceil(exact / _POINT - 1e-9) * _POINT


def _flat(n: int, p: float = 20_000.0) -> "list[tuple[float, float, float, float]]":
    return [(p, p + 5.0, p - 5.0, p) for _ in range(n)]


def _run(csv: Path) -> list:
    controller, request = build_interactor(
        data_path=str(csv), ea_name="EMA_Deviation_Short_EA",
        config_overrides={"tick_model": "ohlc_expand", "stop_out_action": "close_and_halt"},
        **_COMMON,
    )
    return controller.execute(request).trades


def _bar(t) -> int:
    from simulator.domain.bar_time import epoch_seconds

    return (epoch_seconds(t) - 1_704_153_600) // 60  # 2024-01-02T00:00:00Z


@pytest.mark.parametrize(("exit_low", "exit_high", "reason"), [(19_000.0, 21_700.0, "tp"), (21_500.0, 23_500.0, "sl")])
def test_a_touch_of_the_level_sells_at_the_level_and_exits_at_the_fixed_distance(
    tmp_path: Path, exit_low: float, exit_high: float, reason: str
) -> None:
    # Arrange: 横ばい 30 本 → 足 30 で 8% 水準を突き抜ける → 足 31 で TP か SL に届く。
    bars = _flat(30) + [(20_000.0, 22_500.0, 19_995.0, 21_600.0), (21_600.0, exit_high, exit_low, 21_600.0)]
    bars += _flat(5, 21_600.0)
    csv = _write_csv(tmp_path / "md.csv", bars)
    ema = _ema([b[3] for b in bars])
    level = _level(ema[29])
    assert bars[30][0] < level <= bars[30][1], "足 30 の途中で水準に触れる並びでない（検定が空虚）"
    # Act
    trades = _run(csv)
    # Assert
    first = trades[0]
    assert (first.side, _bar(first.entry_time)) == ("sell", 30)
    assert first.entry_price == pytest.approx(level, abs=1e-9)
    assert first.exit_reason == reason
    expected_exit = level - _TP_PTS * _POINT if reason == "tp" else level + _SL_PTS * _POINT
    assert first.exit_price == pytest.approx(expected_exit, abs=1e-9)


def test_no_touch_no_trade(tmp_path: Path) -> None:
    # 7% 上までしか届かない（8% 水準に触れない）。
    bars = _flat(30) + [(20_000.0, 21_400.0, 19_995.0, 21_000.0)] + _flat(5, 21_000.0)
    csv = _write_csv(tmp_path / "md.csv", bars)
    assert _run(csv) == []
