"""MarginProbe_EA で証拠金維持率 100% 割れの挙動を確認する（動作確認・依頼 2026-09-27）。

正解の出どころ:
    参照挙動は MT5 原本 fixture（`fixtures/mt5/ma_slope_jp225_202501`・tester.log
    「position stop out triggered at 99.95%」→ 強制決済 deal を生成して完走）で、その再現は
    `test_tester_settings_stop_out_action.py` が固定済み。本ファイルは **MarginProbe_EA が
    割れの条件を決定論的に作れること**と、割れた点での挙動（`close_and_halt`＝強制決済して
    完走 / `fail_stop`＝`MarginCallError` で run 破棄）を照合する。

    数値の正解は設計書の式から**本ファイルが独立に**計算する（CalcProbe 検定と同じ規約。
    エンジンの関数は呼ばない）:
        必要証拠金: lot × contract_size × entry ÷ leverage
        維持率:     equity / margin × 100、equity = balance + Σ 含み損益
        建値:       buy の新規 = Ask = open + spread × point（PROCESS §4）

観測の境界:
    口座の途中状態は `build_interactor(run_tracer=...)`（宣言された観測境界
    `RunTracePort`）からだけ読む。内部名の差し替えはしない。

計算量（絶対命令・無駄の不在）:
    MarginProbe の発注は 1 回だけである。足数を増やしても発注（＝建玉）が増えないことを
    2 点で固定する（回数リテラルではなく「入力に比例しない」ことの表明）。
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pandas as pd
import pytest

from simulator.domain.exceptions import MarginCallError
from simulator.main import build_interactor
from simulator.usecase.run_trace_ports import RunTracePort

#: 2024-01-01T00:00:00Z（comma 形式 CSV の time は epoch 秒 int が契約）。
_EPOCH = 1_704_067_200
_POINT = 0.1
_SPREAD = 3
_CONTRACT = 10.0
_LEVERAGE = 1.0
_DEPOSIT = 100_000.0
_STEP = 0.1
_VMIN = 0.1
_VMAX = 10_000.0
_STOP_OUT_LEVEL = 100.0


@dataclass(frozen=True)
class _Bar:
    open: float
    close: float


def _flat(n: int) -> "list[_Bar]":
    """価格が動かない並び（狙い値 < 100 なら建てた瞬間に割れる＝データの向きに依らない）。"""
    return [_Bar(100.0, 100.0) for _ in range(n)]


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
        ea_name="MarginProbe_EA",
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
        lot_size=1.0,
        stop_loss_points=0,
        take_profit_points=0,
        stop_out_level=_STOP_OUT_LEVEL,
        margin_level_target=95.0,
        config_overrides={"tick_model": "open_only", "stop_out_action": "close_and_halt"},
    )
    base.update(overrides)
    return base


def _run(csv_path: Path, **overrides: Any):
    controller, request = build_interactor(**_meta(csv_path, **overrides))
    return controller.execute(request)


# ---- 独立計算（エンジンの関数を呼ばない） ----

def _sign(side: str) -> int:
    return 1 if side == "buy" else -1


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


def _independent_volume(target: float, entry: float) -> float:
    """維持率が target 以下になる最小の刻み量（式は margin_probe.py docstring と同じ）。"""
    raw = _DEPOSIT * _LEVERAGE * 100.0 / (_CONTRACT * entry * target)
    steps = math.floor(raw / _STEP + 1e-9)
    lot = steps * _STEP
    if lot + 1e-9 < raw:
        lot += _STEP
    return lot


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
                "equity": account.equity,
                "margin": account.margin,
                "positions": [
                    (p.side, p.volume, p.entry_price) for p in account.open_positions
                ],
                "halted": halted,
            }
        )


def _split_at_halt(rows: "list[dict]") -> "tuple[list[dict], dict | None]":
    for i, r in enumerate(rows):
        if r["halted"]:
            return rows[:i], r
    return rows, None


# ---- close_and_halt: 割れの点で全玉を強制決済して完走する（MT5 fixture と同じ挙動） ----

class TestBreachWithCloseAndHalt:
    def test_a_target_below_100_breaches_immediately_on_flat_data(self, tmp_path: Path) -> None:
        """狙い値 95% の建玉は、価格が動かないデータでも 100% 割れを起こす。"""
        # Arrange
        csv_path = _write_csv(tmp_path / "flat.csv", _flat(8))
        spy = _AccountSpy()
        # Act
        result = _run(csv_path, run_tracer=spy)
        # Assert: 建玉は 1 玉・強制決済（stop_out）で閉じ、run は完走している。
        assert [t.exit_reason for t in result.trades] == ["stop_out"]
        t = result.trades[0]
        entry_ask = 100.0 + _SPREAD * _POINT
        assert t.side == "buy"
        assert t.entry_price == pytest.approx(entry_ask, abs=1e-9)
        assert t.volume == pytest.approx(_independent_volume(95.0, entry_ask), abs=1e-9)
        # 割れの実測: halt した評価点があり、その建玉の独立計算の維持率は 100% を割っている。
        _before, breach = _split_at_halt(spy.rows)
        assert breach is not None, "ロスカットが起きていない（検定が空虚）"
        position = [(t.side, t.volume, t.entry_price)]
        level = (
            (_DEPOSIT + _independent_floating(position, breach["eval_bid"], breach["eval_ask"]))
            / _independent_margin(position, _LEVERAGE) * 100.0
        )
        assert level < _STOP_OUT_LEVEL
        # 狙い値以下に建っている（切り捨てではなく 1 刻み上げで狙い以下へ収める規則）。
        assert level <= 95.0 + 1e-6
        # 強制決済後は残高と有効証拠金が一致し、以降の新規建ては無い。
        assert breach["equity"] == pytest.approx(breach["balance"], abs=1e-9)
        assert breach["balance"] == pytest.approx(_DEPOSIT + t.pnl(), abs=1e-9)
        assert result.trades[-1] is t

    def test_a_target_above_100_breaches_when_price_moves_against(self, tmp_path: Path) -> None:
        """狙い値 120% の建玉は、逆行の足で初めて 100% を割る（割れる前は 100% 以上）。"""
        # Arrange: 3 本は動かず、4 本目で大きく下げる。
        bars = [_Bar(100.0, 100.0), _Bar(100.0, 100.0), _Bar(100.0, 100.0),
                _Bar(80.0, 79.5), _Bar(79.5, 79.0)]
        csv_path = _write_csv(tmp_path / "drop.csv", bars)
        spy = _AccountSpy()
        # Act
        result = _run(csv_path, margin_level_target=120.0, run_tracer=spy)
        # Assert: 割れる前の保有評価点はすべて 100% 以上、割れた点で強制決済している。
        before, breach = _split_at_halt(spy.rows)
        assert breach is not None, "ロスカットが起きていない（検定が空虚）"
        held_before = [r for r in before if r["positions"]]
        assert held_before, "割れる前に保有のある評価点が無い（検定が空虚）"
        for r in held_before:
            assert _independent_level(r, _LEVERAGE) >= _STOP_OUT_LEVEL
        assert [t.exit_reason for t in result.trades] == ["stop_out"]
        assert breach["bar_index"] == 3


# ---- fail_stop: 割れで run を捨てる（エンジン既定の方針） ----

class TestBreachWithFailStop:
    def test_the_default_action_raises_margin_call_error(self, tmp_path: Path) -> None:
        csv_path = _write_csv(tmp_path / "flat.csv", _flat(8))
        with pytest.raises(MarginCallError):
            _run(csv_path, config_overrides={"tick_model": "open_only"})


# ---- 計算量: 発注は入力（足数）に比例しない ----

class TestOrderIssuanceComplexity:
    def test_entries_do_not_grow_with_the_number_of_bars(self, tmp_path: Path) -> None:
        """発注した建玉 − 出力（確認対象の割れ）に使った建玉 = 0 を、足数 2 点で固定する。

        MarginProbe の仕事は「割れの条件を作る 1 玉」だけであり、足数を増やしても発注が
        増えるなら、それは確認に使われない建玉（浪費）が発行されている。回数そのものは
        期待値に焼き込まない——固定するのは入力規模への非比例である。
        """
        counts = []
        for n in (8, 24):
            csv_path = _write_csv(tmp_path / f"flat_{n}.csv", _flat(n))
            result = _run(csv_path)
            counts.append(len(result.trades))
        assert counts[0] == counts[1], f"足数を増やすと建玉が増えました: {counts}"
