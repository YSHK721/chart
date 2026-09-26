"""CalcProbe_EA でエンジンの計算を独立計算と照合する（計算処理の動作確認）。

正解の出どころ:
    MT5 とは突き合わせない（Python のみ・依頼者裁定 2026-09-26）。正解は設計書の式から
    **本ファイルが独立に**計算する。エンジンの関数（「`derive_quotes`」 / 「`Deal.from_close`」 /
    「`Position.required_margin`」 等）は呼ばない——呼べば照合が自分自身との比較になり、
    式の誤りを原理的に捕まえられない。
        約定価格:   新規 buy=Ask=open+spread×point / 新規 sell=Bid=open（PROCESS §4）
                    決済 long=Bid / short=Ask（同 §6）
        損益:       (exit − entry) × sign × lot × contract_size（METRICS §5.2）
        必要証拠金: lot × contract_size × entry / leverage
        維持率:     equity / margin × 100、equity = balance + Σ 含み損益
        ロスカット: 維持率 < stop_out_level の評価点で、全玉を当該点の Bid/Ask で決済
        ロット:     lot_size を volume_step へ切り捨て、[volume_min, volume_max] に収める
        SMA:        直近 period 本の終値の算術平均（期間未満は未成立）

観測の境界:
    口座の途中状態は `build_interactor(run_tracer=...)`（宣言された観測境界
    `RunTracePort`）からだけ読む。内部名の差し替えはしない。

未確定のため照合しないもの:
    期末清算（end_of_test）の**価格と時刻**は ISSUE-522 で未確定である。そのトレードは
    損益式の照合だけを行う（価格は記録値をそのまま入力に使う）。
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pandas as pd
import pytest

from simulator.domain.bar_time import epoch_seconds
from simulator.domain.exceptions import ConfigError
from simulator.main import build_interactor
from simulator.usecase.run_trace_ports import RunTracePort

#: 2024-01-01T00:00:00Z（comma 形式 CSV の 「`time`」 は epoch 秒 int が契約）。
_EPOCH = 1_704_067_200
_POINT = 0.1
_SPREAD = 3
_CONTRACT = 10.0
_LEVERAGE = 100.0
_DEPOSIT = 100_000.0
_STEP = 0.1
_VMIN = 0.1
_VMAX = 100.0


@dataclass(frozen=True)
class _Bar:
    open: float
    close: float


#: 始値が SMA を上下に跨ぐ並び（買い・売りの途転が複数回起きる）。
_CROSSING = [
    _Bar(100.0, 99.5), _Bar(102.0, 102.5), _Bar(104.0, 103.5), _Bar(103.0, 103.5),
    _Bar(99.0, 98.5), _Bar(97.0, 97.5), _Bar(98.0, 97.5), _Bar(101.0, 101.5),
    _Bar(105.0, 104.5), _Bar(104.0, 104.5), _Bar(100.0, 99.5), _Bar(96.0, 96.5),
    _Bar(95.0, 94.5), _Bar(99.0, 99.5), _Bar(103.0, 102.5),
]


def _write_csv(path: Path, bars: "list[_Bar]") -> Path:
    rows = [
        {
            "time": _EPOCH + 60 * i,
            "open": b.open,
            "high": max(b.open, b.close) + 1.0,
            "low": min(b.open, b.close) - 1.0,
            "close": b.close,
            "volume": 100,
            "spread": _SPREAD,
        }
        for i, b in enumerate(bars)
    ]
    pd.DataFrame(rows).to_csv(path, index=False)
    return path


def _meta(csv_path: Path, **overrides: Any) -> dict:
    base = dict(
        data_path=csv_path,
        symbol="SYNTH",
        period="M1",
        ea_name="CalcProbe_EA",
        initial_deposit=_DEPOSIT,
        contract_size=_CONTRACT,
        volume_min=_VMIN,
        volume_max=_VMAX,
        volume_step=_STEP,
        stops_level=0,
        digits=1,
        point_size=_POINT,
        leverage=_LEVERAGE,
        ma_period=3,
        ma_method="sma",
        lot_size=1.27,
        stop_loss_points=0,
        take_profit_points=0,
        config_overrides={"tick_model": "open_only"},
    )
    base.update(overrides)
    return base


# ---- 独立計算（エンジンの関数を呼ばない） ----

def _sma(closes: "list[float]", period: int) -> "list[float]":
    out = []
    for i in range(len(closes)):
        if i + 1 < period:
            out.append(math.nan)
        else:
            window = closes[i + 1 - period : i + 1]
            out.append(sum(window) / period)
    return out


def _floor_lot(lot: float) -> float:
    steps = math.floor(lot / _STEP + 1e-9)
    return min(max(steps * _STEP, _VMIN), _VMAX)


def _sign(side: str) -> int:
    return 1 if side == "buy" else -1


def _entry_price(side: str, bar: _Bar) -> float:
    return bar.open + _SPREAD * _POINT if side == "buy" else bar.open


def _exit_price(side: str, bar: _Bar) -> float:
    # long を閉じるのは Bid、short を閉じるのは Ask。
    return bar.open if side == "buy" else bar.open + _SPREAD * _POINT


def _pnl(side: str, entry: float, exit_: float, lot: float) -> float:
    return (exit_ - entry) * _sign(side) * lot * _CONTRACT


def _expected_entries(bars: "list[_Bar]", period: int) -> "list[tuple[int, str]]":
    """売買規則を独立に辿り、(建てた足, 方向) の列を返す。"""
    sma = _sma([b.close for b in bars], period)
    held = None
    out = []
    for i in range(1, len(bars)):
        if math.isnan(sma[i - 1]):
            continue
        side = "buy" if bars[i].open > sma[i - 1] else "sell"
        if side != held:
            out.append((i, side))
            held = side
    return out


def _bar_index(time: Any) -> int:
    """トレードの時刻から M1 足の位置を引く（時刻表現は経路で異なるので正規化してから）。"""
    return (epoch_seconds(time) - _EPOCH) // 60


def _run(csv_path: Path, **overrides: Any):
    controller, request = build_interactor(**_meta(csv_path, **overrides))
    return controller.execute(request)


# ---- 指標値 ----

def test_the_registered_sma_equals_the_independent_average(tmp_path: Path) -> None:
    from simulator.main.ea_bindings.calc_probe import build_registry

    # Arrange
    df = pd.read_csv(_write_csv(tmp_path / "d.csv", _CROSSING))
    expected = _sma([b.close for b in _CROSSING], 3)
    # Act
    got = build_registry(df, 3).get("sma")
    # Assert: 未成立（NaN）の位置も含めて一致する。
    pd.testing.assert_series_equal(
        got, pd.Series(expected), check_names=False, check_exact=False, atol=1e-9
    )


# ---- 約定価格・損益 ----

class TestFillsAndProfit:
    def test_entries_follow_the_independent_rule_on_both_sides(self, tmp_path: Path) -> None:
        # Arrange
        csv_path = _write_csv(tmp_path / "d.csv", _CROSSING)
        expected = _expected_entries(_CROSSING, 3)
        # Act
        trades = _run(csv_path).trades
        # Assert
        assert {s for _, s in expected} == {"buy", "sell"}, "両方向を通らない並び（検定が空虚）"
        got = [(_bar_index(t.entry_time), t.side) for t in trades]
        assert got == expected

    def test_entry_and_exit_prices_are_the_bid_ask_of_the_bar_open(self, tmp_path: Path) -> None:
        # Arrange
        csv_path = _write_csv(tmp_path / "d.csv", _CROSSING)
        # Act
        trades = _run(csv_path).trades
        # Assert
        reversed_ = [t for t in trades if t.exit_reason == "reverse"]
        assert reversed_, "途転決済が 1 件も無い（検定が空虚）"
        for t in trades:
            assert t.entry_price == pytest.approx(
                _entry_price(t.side, _CROSSING[_bar_index(t.entry_time)]), abs=1e-9
            )
        for t in reversed_:
            assert t.exit_price == pytest.approx(
                _exit_price(t.side, _CROSSING[_bar_index(t.exit_time)]), abs=1e-9
            )

    def test_profit_of_every_trade_matches_the_formula(self, tmp_path: Path) -> None:
        # Arrange
        csv_path = _write_csv(tmp_path / "d.csv", _CROSSING)
        # Act
        result = _run(csv_path)
        # Assert: end_of_test も損益式だけは照合する（価格は ISSUE-522 で未確定）。
        signs = set()
        for t in result.trades:
            expected = _pnl(t.side, t.entry_price, t.exit_price, t.volume)
            assert t.pnl() == pytest.approx(expected, abs=1e-9)
            signs.add(expected > 0)
        assert signs == {True, False}, "利益と損失の両方を通らない並び（検定が空虚）"
        assert result.balance_curve[-1] == pytest.approx(
            _DEPOSIT + sum(t.pnl() for t in result.trades), abs=1e-9
        )


# ---- ロット ----

class TestLot:
    @pytest.mark.parametrize("lot_size", [1.27, 0.1, 0.19, 250.0])
    def test_the_filled_volume_is_the_lot_floored_to_the_step(
        self, tmp_path: Path, lot_size: float
    ) -> None:
        # Arrange
        csv_path = _write_csv(tmp_path / "d.csv", _CROSSING)
        # Act
        trades = _run(csv_path, lot_size=lot_size, leverage=1_000_000.0).trades
        # Assert
        assert trades
        for t in trades:
            assert t.volume == pytest.approx(_floor_lot(lot_size), abs=1e-9)

    def test_a_lot_below_the_minimum_refuses_to_start(self, tmp_path: Path) -> None:
        csv_path = _write_csv(tmp_path / "d.csv", _CROSSING)
        # 何を入れれば通るか（最小ロット）を文言で名指す。
        with pytest.raises(ConfigError, match=f"最小ロット {_VMIN}"):
            _run(csv_path, lot_size=0.05)


# ---- 証拠金・ロスカット ----

class _AccountSpy(RunTracePort):
    """評価点ごとの口座を**値で**写す（観測は読むだけ・RunTracePort の契約）。"""

    def __init__(self) -> None:
        self.rows: "list[dict]" = []

    def observe(self, point: Any, account: Any, open_trades: Any, halted: bool) -> None:
        self.rows.append(
            {
                "bar_index": point.bar_index,
                "eval_bid": point.eval_bid,
                "eval_ask": point.eval_ask,
                "balance": account.balance,
                "margin": account.margin,
                "equity": account.equity,
                "positions": [
                    (p.side, p.volume, p.entry_price) for p in account.open_positions
                ],
                "halted": halted,
            }
        )


def _independent_margin(positions, leverage: float) -> float:
    return sum(v * _CONTRACT * e / leverage for _, v, e in positions)


def _independent_floating(positions, bid: float, ask: float) -> float:
    return sum(
        ((bid if s == "buy" else ask) - e) * _sign(s) * v * _CONTRACT for s, v, e in positions
    )


def _independent_level(row: dict, leverage: float) -> float:
    positions = row["positions"]
    equity = row["balance"] + _independent_floating(positions, row["eval_bid"], row["eval_ask"])
    return equity / _independent_margin(positions, leverage) * 100.0


def _split_at_halt(rows: "list[dict]") -> "tuple[list[dict], dict | None]":
    """halt した最初の評価点で観測列を割る（その点の観測は決済後の口座を写す）。"""
    for i, r in enumerate(rows):
        if r["halted"]:
            return rows[:i], r
    return rows, None


class TestMarginAndStopOut:
    def test_margin_and_equity_match_at_every_evaluation_point(self, tmp_path: Path) -> None:
        # Arrange
        csv_path = _write_csv(tmp_path / "d.csv", _CROSSING)
        spy = _AccountSpy()
        # Act
        result = _run(csv_path, run_tracer=spy)
        # Assert
        held = [r for r in spy.rows if r["positions"]]
        assert held, "保有のある評価点が無い（検定が空虚）"
        for r in held:
            assert r["margin"] == pytest.approx(
                _independent_margin(r["positions"], _LEVERAGE), abs=1e-9
            )
            assert r["equity"] == pytest.approx(
                r["balance"] + _independent_floating(r["positions"], r["eval_bid"], r["eval_ask"]),
                abs=1e-9,
            )
        closed_before = [t for t in result.trades if t.exit_reason == "reverse"]
        assert spy.rows[-1]["balance"] == pytest.approx(
            _DEPOSIT + sum(t.pnl() for t in closed_before), abs=1e-9
        )

    def test_stop_out_fires_at_the_first_point_below_the_level(self, tmp_path: Path) -> None:
        """維持率が水準を割る最初の評価点で、その点の Bid/Ask で全玉を決済する。"""
        # Arrange: 買いを建てたあと 1 本で大きく下げる（維持率 ≈ 49.9% < 50%）。
        bars = [
            _Bar(100.0, 100.5), _Bar(101.0, 101.5), _Bar(102.0, 102.5),
            _Bar(103.0, 103.5), _Bar(104.0, 45.0), _Bar(45.0, 46.0), _Bar(46.0, 47.0),
        ]
        csv_path = _write_csv(tmp_path / "crash.csv", bars)
        spy = _AccountSpy()
        deposit, leverage, level = 1300.0, 1.0, 50.0
        # Act
        result = _run(
            csv_path,
            initial_deposit=deposit,
            leverage=leverage,
            lot_size=1.2,
            ma_period=2,
            stop_out_level=level,
            config_overrides={"tick_model": "open_only", "stop_out_action": "close_and_halt"},
            run_tracer=spy,
        )
        # Assert: 割れた評価点より前は、保有のある全点で独立計算の維持率が水準以上。
        before, breach = _split_at_halt(spy.rows)
        assert breach is not None, "ロスカットが起きていない（検定が空虚）"
        held_before = [r for r in before if r["positions"]]
        assert held_before, "割れる前に保有のある評価点が無い（検定が空虚）"
        for r in held_before:
            assert _independent_level(r, leverage) >= level
        stop_outs = [t for t in result.trades if t.exit_reason == "stop_out"]
        assert len(stop_outs) == 1
        t = stop_outs[0]
        assert _bar_index(t.exit_time) == breach["bar_index"]
        assert t.exit_price == pytest.approx(
            breach["eval_bid"] if t.side == "buy" else breach["eval_ask"], abs=1e-9
        )
        position = [(t.side, t.volume, t.entry_price)]
        equity_at_breach = deposit + _independent_floating(
            position, breach["eval_bid"], breach["eval_ask"]
        )
        assert equity_at_breach / _independent_margin(position, leverage) * 100.0 < level
        # 以降は新規に建てない（halt）。
        assert result.trades[-1] is t


# ---- 期間指定（ISSUE-509）: 指標は全履歴で温め、足とは時刻で対応する ----

#: 期間の前に置く履歴の本数。SMA 窓より長くし、期間の先頭足から判定が成立するようにする。
_HISTORY = 10


def _write_marketdata_csv(path: Path, bars: "list[_Bar]") -> Path:
    """UI の実行と同じ marketdata 形式（「`date`」 列は UTC の文字列）で書く。"""
    rows = [
        {
            "date": pd.Timestamp(_EPOCH + 60 * i, unit="s").strftime("%Y-%m-%d %H:%M:%S"),
            "open": b.open,
            "high": max(b.open, b.close) + 1.0,
            "low": min(b.open, b.close) - 1.0,
            "close": b.close,
            "volume": 100.0,
            "up": 50.0,
            "dn": 50.0,
            "spread": _SPREAD,
        }
        for i, b in enumerate(bars)
    ]
    pd.DataFrame(rows).to_csv(path, index=False)
    return path


def _window_from(row: int) -> "tuple[Any, Any]":
    from datetime import datetime, timezone

    start = datetime.fromtimestamp(_EPOCH + 60 * row, tz=timezone.utc)
    end = datetime.fromtimestamp(_EPOCH + 60 * 10_000, tz=timezone.utc)
    return (start, end)


def _expected_windowed_entries(bars: "list[_Bar]", period: int, first_row: int):
    """全履歴の SMA で、期間内の足（期間の 2 本目以降）だけを判定した (時刻, 方向) の列。"""
    sma = _sma([b.close for b in bars], period)
    held = None
    out = []
    for row in range(first_row + 1, len(bars)):
        side = "buy" if bars[row].open > sma[row - 1] else "sell"
        if side != held:
            out.append((_EPOCH + 60 * row, side))
            held = side
    return out


class TestWindowedRun:
    def test_decisions_read_the_indicator_of_the_same_bar_in_time(self, tmp_path: Path) -> None:
        # Arrange: 期間の前の履歴と期間内で値の水準を大きく変え、位置がずれれば方向が変わるようにする。
        history = [_Bar(200.0 + i, 200.5 + i) for i in range(_HISTORY)]
        bars = history + _CROSSING
        csv_path = _write_marketdata_csv(tmp_path / "md.csv", bars)
        expected = _expected_windowed_entries(bars, 3, _HISTORY)
        # Act
        trades = _run(csv_path, marketdata_window=_window_from(_HISTORY)).trades
        # Assert
        assert {s for _, s in expected} == {"buy", "sell"}, "両方向を通らない並び（検定が空虚）"
        assert [(epoch_seconds(t.entry_time), t.side) for t in trades] == expected

    def test_the_entry_price_is_the_bid_ask_of_the_same_bar(self, tmp_path: Path) -> None:
        # Arrange
        history = [_Bar(200.0 + i, 200.5 + i) for i in range(_HISTORY)]
        bars = history + _CROSSING
        csv_path = _write_marketdata_csv(tmp_path / "md.csv", bars)
        # Act
        trades = _run(csv_path, marketdata_window=_window_from(_HISTORY)).trades
        # Assert
        assert trades
        for t in trades:
            row = _bar_index(t.entry_time)
            assert t.entry_price == pytest.approx(_entry_price(t.side, bars[row]), abs=1e-9)
