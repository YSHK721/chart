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

import numpy as np
import pandas as pd
import pytest

from simulator.domain.exceptions import ConfigError
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

    例外型が `TypeError` から `ConfigError` へ変わったのは ISSUE-412 の単一ソース委譲の
    帰結である。判定規則を `simulator.domain.bar_time` の 1 か所へ寄せた以上、bool の
    特例をここに残せば「時刻表現の判定」の写しが復活する（写しが復活すれば表への追加に
    追随せず受理集合が二重定義になる＝本 issue の欠陥そのものが再発する）。
    拒否の意図（不正な時刻を無音で通さない）は不変であり、緩めていない。
    """
    with pytest.raises(ConfigError):
        unix_seconds(True)


def test_timestampはunix秒intへ正規化する() -> None:
    assert unix_seconds(pd.Timestamp("2026-04-20 00:00:00", tz="UTC")) == _EPOCH


def test_datetime64はunix秒intへ正規化する() -> None:
    assert unix_seconds(pd.Timestamp(_EPOCH, unit="s").to_datetime64()) == _EPOCH


def test_datetimeはunix秒intへ正規化する() -> None:
    assert unix_seconds(datetime(2026, 4, 20, tzinfo=timezone.utc)) == _EPOCH


def test_戻り値はint型() -> None:
    assert type(unix_seconds(pd.Timestamp("2026-04-20", tz="UTC"))) is int


def test_numpy_int64のepoch秒を1970年へ落とさない() -> None:
    """ISSUE-412 (C): comma 形式 CSV 由来の実型（`numpy.int64`）が int 分岐から外れていた。

    手書きの `isinstance(t, int)` は `isinstance(np.int64(1), int)` が **False**
    （numpy 2.4.6 実測）なので分岐を外れ、`pd.Timestamp(np.int64(1776643200))` ＝
    ns 解釈で **1970-01-01 00:00:01.776643200** へ落ちていた（例外なしの桁ずれ）。
    `report.json` へ書く値なので `numpy.int64` のまま素通しもしない（JSON 化不能）。
    """
    assert isinstance(np.int64(_EPOCH), int) is False  # 前提の実測（numpy 2.4.6）
    assert unix_seconds(np.int64(_EPOCH)) == _EPOCH
    assert type(unix_seconds(np.int64(_EPOCH))) is int


def test_整数の種類で結果が変わらない() -> None:
    """同一時刻は「どの整数型で書かれたか」に依存せず同一 UNIX 秒を返す（表現非依存）。"""
    assert unix_seconds(np.int64(_EPOCH)) == unix_seconds(int(_EPOCH))


def test_未対応表現は無音で1970年を出さずConfigErrorになる() -> None:
    """表現差の吸収規則は `bar_time.epoch_seconds` が唯一持ち、未対応は推測解釈しない。"""
    with pytest.raises(ConfigError):
        unix_seconds("2026-04-20T00:00:00")


def test_時刻正規化の実体はdomainの単一ソースそのもの() -> None:
    """規則の写しを持たない（複製が入り込むと本検定が落ちる）。

    先例の流儀: `simulator/tests/unit/test_tick_window_single_source.py`
    `test_tick_stage_reads_the_shared_normalizer`。
    """
    from simulator.domain import bar_time
    from simulator.report_ui.tools import int_time_views

    assert int_time_views.epoch_seconds is bar_time.epoch_seconds


# --- 1b. 委譲で変わらないこと（不変条件の固定・Red ではない） -----------------
# 下 2 件は是正前から通る。単一ソース委譲で naive の解釈が動いていないことを
# 固定する不変条件の壁であり、Red 証拠には数えない。

def test_naive_timestampはUTCとして解釈する() -> None:
    """pandas の naive `Timestamp.timestamp()` は UTC 基準（実測）。委譲後も同値である。

    `bar_time` 側の datetime 変換（`datawindow.half_open.epoch_seconds_of_datetime`）も
    naive を UTC とみなすため、旧実装 `int(pd.Timestamp(t).timestamp())` と一致する
    （ISSUE-411 実測記録）。プロセスのローカル TZ に依存しない。
    """
    assert unix_seconds(pd.Timestamp("2026-04-20 00:00:00")) == _EPOCH


def test_naive_datetimeもUTCとして解釈する() -> None:
    assert unix_seconds(datetime(2026, 4, 20)) == _EPOCH


# --- 2. IntTimeBar --------------------------------------------------------------

def test_barはtimeだけint化しOHLCは素通し() -> None:
    view = IntTimeBar(_Bar(pd.Timestamp("2026-04-20", tz="UTC")))
    assert view.time == _EPOCH
    assert (view.open, view.high, view.low, view.close) == (100.0, 112.0, 95.0, 105.0)


def test_barはnumpy_int64のtimeを1970年へ落とさない() -> None:
    """ISSUE-412 (C) の実害面: `raw_bars` が comma CSV 由来なら bar.time は `numpy.int64`。

    ビュー経由でも 1970 年へ落ちない（report.json の bars が全て 1970 年になる）。
    """
    assert IntTimeBar(_Bar(np.int64(_EPOCH))).time == _EPOCH


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
