"""選んだ足（Period）で判定する run（2026-10-06・依頼者承認）。

仕様（MT5 と同じく「判定はチャートの足・約定はティック」）:
    * 戦略は選んだ足が始まる 1 分足でだけ判定し、渡る番号は選んだ足の番号。
    * 指標は選んだ足の系列（日足なら日足の終値の EMA）。
    * 約定・SL/TP は 1 分足の精度（約定時刻は触れた 1 分足の時刻）。

正解の出どころ（エンジン・marketdata の関数を呼ばない）:
    合成 1 分足は各日 UTC 00:00〜01:59 にだけ置く。ブローカー日の区切り（UTC 21:00/22:00）を
    またがないので、日足は「UTC の日ごと」に独立に作れる（始値=最初・終値=最後）。

計算量（観測の境界は宣言された差し込み口 `strategy_override`）:
    判定の呼び出し回数 − 選んだ足の本数 = 0。1 日あたりの 1 分足を増やしても変わらない。
"""
from __future__ import annotations

import math
from pathlib import Path

import pytest

from simulator.domain.bar_time import epoch_seconds
from simulator.domain.exceptions import ConfigError
from simulator.main import build_interactor
from simulator.tests.ledger_stop_out import ledger_stop_out_level

_DAY0 = 1_704_153_600  # 2024-01-02T00:00:00Z
_PERIOD = 21
_PCT = 8.0
_POINT = 0.1
_TP_PTS = 5000  # 500.0

_COMMON = dict(
    symbol="JP225", initial_deposit=10_000_000.0, contract_size=1.0,
    volume_min=1.0, volume_max=100.0, volume_step=1.0, stops_level=0, digits=1,
    point_size=_POINT, leverage=100.0, stop_out_level=ledger_stop_out_level(),
    ma_period=_PERIOD, ma_method="ema", lot_size=1.0, ema_deviation_pct=_PCT,
    stop_loss_points=0, take_profit_points=_TP_PTS,
)


def _write(path: Path, days: "list[list[tuple[float, float, float, float]]]") -> Path:
    rows = ["date,open,high,low,close,volume,spread"]
    for d, bars in enumerate(days):
        for m, (o, h, lo, c) in enumerate(bars):
            t = _DAY0 + d * 86_400 + m * 60
            stamp = f"{_iso(t)}"
            rows.append(f"{stamp},{o},{h},{lo},{c},1,10")
    path.write_text("\n".join(rows) + "\n", encoding="utf-8")
    return path


def _iso(t: int) -> str:
    import datetime as _dt

    return _dt.datetime.fromtimestamp(t, _dt.timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def _flat_day(p: float, n: int) -> "list[tuple[float, float, float, float]]":
    return [(p, p + 5.0, p - 5.0, p) for _ in range(n)]


def _ema(closes: "list[float]") -> "list[float]":
    a = 2.0 / (_PERIOD + 1)
    out = [closes[0]]
    for c in closes[1:]:
        out.append(a * c + (1 - a) * out[-1])
    return out


def _level(ema_prev: float) -> float:
    a = 2.0 / (_PERIOD + 1)
    up = 1.0 + _PCT / 100.0
    return math.ceil(ema_prev * up * (1 - a) / (1 - up * a) / _POINT - 1e-9) * _POINT


class _CountingStrategy:
    """判定の呼び出しを数える戦略（Test Spy・発注しない）。"""

    entry_price_basis = "current_open"

    def __init__(self) -> None:
        self.calls: "list[int]" = []

    def on_init(self, config, indicators) -> None:
        return None

    def on_new_bar(self, bar_index, indicators, account):
        self.calls.append(bar_index)
        return []

    def on_position_check(self, position, bar_index, indicators) -> str:
        return "hold"


def _run(csv: Path, period: str, **kw):
    controller, request = build_interactor(
        data_path=str(csv), ea_name="EMA_Deviation_Short_EA", period=period,
        config_overrides={"tick_model": "ohlc_expand", "stop_out_action": "close_and_halt"},
        **{**_COMMON, **kw},
    )
    return controller.execute(request)


class TestDecisionsHappenOncePerSelectedBar:
    @pytest.mark.parametrize("per_day", [30, 120])
    def test_calls_minus_daily_bars_is_zero_and_indices_are_daily(self, tmp_path, per_day):
        # Arrange: 12 日 × per_day 本の 1 分足。
        days = [_flat_day(20_000.0 + d, per_day) for d in range(12)]
        csv = _write(tmp_path / "md.csv", days)
        spy = _CountingStrategy()
        # Act
        _run(csv, "Daily", strategy_override=spy)
        # Assert: 判定 − 日足の本数 = 0（1 日あたりの 1 分足の本数に依らない）。
        assert len(spy.calls) - len(days) == 0
        assert spy.calls == list(range(len(days)))

    def test_m1_still_decides_on_every_bar(self, tmp_path):
        days = [_flat_day(20_000.0, 10) for _ in range(3)]
        csv = _write(tmp_path / "md.csv", days)
        spy = _CountingStrategy()
        _run(csv, "M1", strategy_override=spy)
        assert len(spy.calls) - 30 == 0


def test_daily_ema_short_sells_at_the_daily_level_inside_the_day_and_takes_profit(tmp_path):
    # Arrange: 横ばい 30 日 → 30 日目の 1 分足 #80 で日足 8% 水準を突き抜ける → 翌日に TP。
    days = [_flat_day(20_000.0, 120) for _ in range(30)]
    spike = _flat_day(20_000.0, 80) + [(20_000.0, 22_500.0, 19_995.0, 22_000.0)]
    spike += _flat_day(22_000.0, 39)
    days.append(spike)
    days.append(_flat_day(22_000.0, 10) + [(22_000.0, 22_005.0, 21_000.0, 21_100.0)] + _flat_day(21_100.0, 109))
    days += [_flat_day(21_100.0, 120) for _ in range(3)]
    csv = _write(tmp_path / "md.csv", days)
    daily_close = [bars[-1][3] for bars in days]
    level = _level(_ema(daily_close)[29])
    assert 20_000.0 < level <= 22_500.0, "30 日目の途中で水準に触れる並びでない（検定が空虚）"
    # Act
    trades = _run(csv, "Daily").trades
    # Assert
    first = trades[0]
    assert first.side == "sell"
    assert first.entry_price == pytest.approx(level, abs=1e-9)
    # 約定時刻は触れた 1 分足（30 日目の #80）であって、日足の時刻ではない。
    assert epoch_seconds(first.entry_time) == _DAY0 + 30 * 86_400 + 80 * 60
    assert first.exit_reason == "tp"
    assert first.exit_price == pytest.approx(level - _TP_PTS * _POINT, abs=1e-9)
    assert epoch_seconds(first.exit_time) == _DAY0 + 31 * 86_400 + 10 * 60


def test_a_period_that_cannot_be_built_from_1_minute_bars_refuses_to_start(tmp_path):
    csv = _write(tmp_path / "md.csv", [_flat_day(20_000.0, 10) for _ in range(3)])
    with pytest.raises(ConfigError):
        _run(csv, "H2")
