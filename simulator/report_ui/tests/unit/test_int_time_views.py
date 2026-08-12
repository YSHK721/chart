"""int_time_views（UC へ渡す int 時刻ビュー・H-D1）の単体検定。

移設の回帰ゲート（H-D3）: `export_report_payload.py` と `sim_ui/adapter/report_payload_writer.py`
が 43 行の同一実装を各自持っていた（codescan clone 第 1 位・実測 2026-08-11）。単一ソース化で
**入出力が 1 ビットも変わらない**ことを、移設の前に本検定で固定する（Red → 移設 → Green）。

`test_export_oracle.py` は confirmation データ不在の環境では走らないため、回帰ゲートに数えない
（走らない検定は壁ではない）。ここでは fake 入力だけで契約を閉じる。

固定する契約:
    1. unix_seconds: int はそのまま／bool は int 扱いにしない／datetime 系は UNIX 秒 int へ
    2. IntTimeBar: time だけ int 化し OHLC は素通し（read-only ビュー）
    3. IntTimeTrade: entry/exit を int 化・pnl() は構築時の値を返す（再計算しない）
    4. ResultView: trades を写像し balance_curve は list 化・stats/deals/equity_curve は素通し
"""
from __future__ import annotations

from datetime import datetime, timezone

import pandas as pd
import pytest

from simulator.report_ui.tools.int_time_views import (
    IntTimeBar,
    IntTimeTrade,
    ResultView,
    unix_seconds,
)

_EPOCH = 1776643200  # 2026-04-20 00:00:00 UTC


class _Bar:
    def __init__(self, time) -> None:
        self.time = time
        self.high = 112.0
        self.low = 95.0
        self.open = 100.0
        self.close = 105.0


class _Trade:
    def __init__(self, entry, exit_) -> None:
        self.side = "buy"
        self.entry_time = entry
        self.exit_time = exit_
        self.entry_price = 100.0
        self.exit_price = 105.0
        self.volume = 0.1
        self.exit_reason = "tp"
        self._calls = 0

    def pnl(self) -> float:
        self._calls += 1
        return 50.0


class _Result:
    def __init__(self) -> None:
        self.trades = [_Trade(_EPOCH, _EPOCH + 60)]
        self.balance_curve = (10000.0, 10050.0)
        self.stats = {"net": 50.0}
        self.deals = ["d1"]
        self.equity_curve = [1, 2]


# --- 1. unix_seconds ------------------------------------------------------------

def test_int時刻はそのまま返す() -> None:
    assert unix_seconds(_EPOCH) == _EPOCH


def test_boolはintとして素通ししない() -> None:
    """`isinstance(True, int)` は真。素通しすると `time=1`（1970 年）が黙って混ざる。

    移設元の実装は bool を int 経路から外し、pandas 経路で `TypeError` になる。
    移設で挙動を変えない（緩めて 0/1 を通すと不正な時刻が通る）。
    """
    with pytest.raises(TypeError):
        unix_seconds(True)


def test_timestampはunix秒intへ正規化する() -> None:
    assert unix_seconds(pd.Timestamp("2026-04-20 00:00:00", tz="UTC")) == _EPOCH


def test_datetime64はunix秒intへ正規化する() -> None:
    assert unix_seconds(pd.Timestamp(_EPOCH, unit="s").to_datetime64()) == _EPOCH


def test_datetimeはunix秒intへ正規化する() -> None:
    assert unix_seconds(datetime(2026, 4, 20, tzinfo=timezone.utc)) == _EPOCH


def test_戻り値はint型() -> None:
    assert type(unix_seconds(pd.Timestamp("2026-04-20", tz="UTC"))) is int


# --- 2. IntTimeBar --------------------------------------------------------------

def test_barはtimeだけint化しOHLCは素通し() -> None:
    view = IntTimeBar(_Bar(pd.Timestamp("2026-04-20", tz="UTC")))
    assert view.time == _EPOCH
    assert (view.open, view.high, view.low, view.close) == (100.0, 112.0, 95.0, 105.0)


def test_barビューは属性を増やさない() -> None:
    view = IntTimeBar(_Bar(_EPOCH))
    with pytest.raises(AttributeError):
        view.volume = 1  # type: ignore[attr-defined]


# --- 3. IntTimeTrade ------------------------------------------------------------

def test_tradeはentryとexitをint化する() -> None:
    view = IntTimeTrade(_Trade(pd.Timestamp("2026-04-20", tz="UTC"), _EPOCH + 60))
    assert (view.entry_time, view.exit_time) == (_EPOCH, _EPOCH + 60)


def test_tradeはpnlを構築時に1回だけ読む() -> None:
    """UC は pnl() を何度も呼ぶ。都度計算し直すと元実装と負荷特性が変わる。"""
    trade = _Trade(_EPOCH, _EPOCH + 60)
    view = IntTimeTrade(trade)
    assert view.pnl() == 50.0
    assert view.pnl() == 50.0
    assert trade._calls == 1


def test_tradeは表示に要る属性をそのまま持つ() -> None:
    view = IntTimeTrade(_Trade(_EPOCH, _EPOCH + 60))
    assert (view.side, view.entry_price, view.exit_price, view.volume, view.exit_reason) == (
        "buy", 100.0, 105.0, 0.1, "tp",
    )


# --- 4. ResultView --------------------------------------------------------------

def test_resultはtradesをint時刻ビューへ写像する() -> None:
    view = ResultView(_Result())
    assert [type(t) for t in view.trades] == [IntTimeTrade]
    assert view.trades[0].entry_time == _EPOCH


def test_result_balance_curveはlist化する() -> None:
    view = ResultView(_Result())
    assert view.balance_curve == [10000.0, 10050.0]
    assert isinstance(view.balance_curve, list)


def test_result_statsは素通し() -> None:
    result = _Result()
    assert ResultView(result).stats is result.stats


def test_result_dealsとequity_curveも素通し() -> None:
    """移植元 export_report_payload の `_ResultView` が持っていた 2 属性（契約の欠落防止）。"""
    result = _Result()
    view = ResultView(result)
    assert view.deals is result.deals
    assert view.equity_curve is result.equity_curve


def test_dealsが無い結果は受け付けない() -> None:
    """`BacktestResult` は deals / equity_curve を必須フィールドに持つ（models.py:150-151）。
    既定値で受けると本番の型契約をテストダブルへ合わせることになる（移設で緩めない）。
    """
    class _Minimal:
        trades: list = []
        balance_curve: list = []
        stats: dict = {}

    with pytest.raises(AttributeError):
        ResultView(_Minimal())
