"""取得窓 `[start, end)` の**境界正規化と半開判定**が 2 段で同一規則であることを固定する。

是正対象（レビュー 🟡-2 の実測）:
    窓境界を epoch へ正規化する処理が 2 箇所にあり、**解釈が食い違っていた**。

      - Candle 段 (`marketdata/csv_source.py`): ``int(start.timestamp())``
        → naive datetime を**プロセスのローカル TZ**で解釈する。
      - Bar 段 (`simulator/adapter/repository/windowed_market_data.py`):
        ``simulator.domain.bar_time.epoch_seconds`` → naive datetime を **UTC** とみなす。

    実測（``TZ=Asia/Tokyo`` ・ ``datetime(2025, 1, 10)`` naive）::

        epoch_seconds   (Bar 段)    = 1736467200
        int(.timestamp) (Candle 段) = 1736434800   差 = 32400 秒（9 時間）

    aware UTC のときは一致するため、唯一の窓生成点（`main/tester_settings/window.py`
    `resolve_data_window` が `_midnight_utc` で aware を生成する）を通る限り表面化しない。
    しかし ``marketdata_window`` は `build_interactor` の**公開引数**であり、呼出側が naive を
    渡せば経路依存で 9 時間ずれる。本テストは「窓境界の解釈が経路で分岐しないこと」を
    naive 入力で直接測る（aware だけを測ると食い違いを検出できない＝症状回避になる）。

本モジュールが固定する契約:
  1. **同一の関数オブジェクト**: 境界正規化（datetime → epoch 秒）の実体は中立共有パッケージ
     ``datawindow.half_open`` が唯一所有し、Candle 段・Bar 段・`bar.time` 正規化表
     （`simulator.domain.bar_time.EPOCH_CONVERTERS`）の 3 者が同じオブジェクトを読む。
     手書き複製が入り込むと ``is`` 比較が落ちる（A-3 が `window.py` と窓デコレータの間で
     行った同一性テストと同じ流儀）。
  2. **半開判定も単一ソース**: ``[start, end)`` の述語は
     ``HalfOpenEpochWindow.contains`` ひとつであり、両段が同じメソッドを読む。
  3. **naive datetime = UTC**（`simulator/tests/unit/test_bar_time_epoch.py` が既に固定して
     いる既存合意）。両段でこの 1 つの解釈に確定する。
  4. **ローカル TZ 非依存**: ``TZ=UTC`` と ``TZ=Asia/Tokyo`` で同一結果。窓境界の意味が
     実行環境に依存するという**原因そのもの**を除去する（naive を「来ないことにする」
     症状回避ではない）。
"""
from __future__ import annotations

import os
import time
from datetime import datetime, timezone
from typing import Any

import pytest

from datawindow.half_open import HalfOpenEpochWindow, epoch_seconds_of_datetime
from marketdata import csv_source as csv_source_module
from marketdata.csv_source import CsvCandleSource
from simulator.adapter.repository import windowed_market_data as windowed_module
from simulator.adapter.repository.windowed_market_data import WindowedMarketDataRepository
from simulator.domain.bar import Bar
from simulator.domain.bar_time import EPOCH_CONVERTERS
from simulator.usecase.ports import MarketDataPort

#: 検証に用いる窓（**naive**）。ローカル TZ が非 UTC のとき 2 段の解釈差が最大化する。
_NAIVE_WINDOW = (datetime(2025, 1, 10), datetime(2025, 1, 11))

#: 同一時刻を aware UTC で書いたもの（naive = UTC 確定後は両者が同じ窓になる）。
_AWARE_WINDOW = (
    datetime(2025, 1, 10, tzinfo=timezone.utc),
    datetime(2025, 1, 11, tzinfo=timezone.utc),
)


def _epoch(*args: int) -> int:
    return int(datetime(*args, tzinfo=timezone.utc).timestamp())


#: 窓の内外にまたがる 1 分足 6 本の epoch（1/9 22:00Z 〜 1/11 02:00Z）。
#: ローカル TZ を JST と誤解釈した窓（= UTC 窓の 9 時間前）と UTC 窓の双方に、
#: 「入る足」と「入らない足」が両方存在するように置く（差が観測可能であること）。
_BAR_EPOCHS = [
    _epoch(2025, 1, 9, 22, 0),   # UTC 窓外 / JST 誤解釈窓内
    _epoch(2025, 1, 10, 0, 0),   # UTC 窓の始端（含む）
    _epoch(2025, 1, 10, 12, 0),  # 両方の窓内
    _epoch(2025, 1, 10, 15, 0),  # UTC 窓内 / JST 誤解釈窓の終端（含まない）
    _epoch(2025, 1, 10, 23, 0),  # UTC 窓内 / JST 誤解釈窓外
    _epoch(2025, 1, 11, 2, 0),   # 両方の窓外
]


class _FixedBarsPort(MarketDataPort):
    """内側 port の代役（窓の外側合成だけを測るため load は固定 bars を返す）。"""

    def __init__(self, bars: "list[Bar]") -> None:
        self._bars = bars

    def load(self, source_ref: Any, timeframe: Any = None, period: Any = None) -> "list[Bar]":
        return list(self._bars)


def _bars() -> "list[Bar]":
    return [
        Bar(time=t, open=100.0, high=101.0, low=99.0, close=100.5, volume=1.0, spread=7)
        for t in _BAR_EPOCHS
    ]


def _write_csv(path) -> Any:
    header = "time,open,high,low,close,volume"
    rows = [f"{t},100.0,101.0,99.0,100.5,1.0" for t in _BAR_EPOCHS]
    path.write_text("\n".join([header, *rows]) + "\n", encoding="utf-8")
    return path


def _candle_stage_times(csv_path, window) -> "list[int]":
    return [c["time"] for c in CsvCandleSource(csv_path).fetch_candles(*window)]


def _bar_stage_times(window) -> "list[int]":
    repo = WindowedMarketDataRepository(_FixedBarsPort(_bars()), window=window)
    return [b.time for b in repo.load("x")]


@pytest.fixture()
def tokyo_local_timezone():
    """プロセスのローカル TZ を Asia/Tokyo に固定する（UTC との差 +9h）。

    naive datetime の解釈がローカル TZ に依存していれば、この fixture 下で 2 段の
    結果が食い違う。テスト後は必ず元へ戻す（他テストへ漏らさない）。
    """
    saved = os.environ.get("TZ")
    os.environ["TZ"] = "Asia/Tokyo"
    time.tzset()
    try:
        yield
    finally:
        if saved is None:
            os.environ.pop("TZ", None)
        else:
            os.environ["TZ"] = saved
        time.tzset()


class TestBoundaryNormalizationIsSymmetric:
    """Candle 段と Bar 段が同じ窓境界解釈を持つ（是正前は naive で 9 時間ずれた）。"""

    def test_naive_window_selects_the_same_bars_in_both_stages(
        self, tmp_path, tokyo_local_timezone
    ):
        csv_path = _write_csv(tmp_path / "ohlc.csv")
        assert _candle_stage_times(csv_path, _NAIVE_WINDOW) == _bar_stage_times(_NAIVE_WINDOW)

    def test_naive_window_is_interpreted_as_utc_in_both_stages(
        self, tmp_path, tokyo_local_timezone
    ):
        # naive の扱いは「UTC とみなす」で確定（既存合意 test_bar_time_epoch.py と同一）。
        csv_path = _write_csv(tmp_path / "ohlc.csv")
        assert _candle_stage_times(csv_path, _NAIVE_WINDOW) == _candle_stage_times(
            csv_path, _AWARE_WINDOW
        )
        assert _bar_stage_times(_NAIVE_WINDOW) == _bar_stage_times(_AWARE_WINDOW)

    def test_aware_window_selects_the_same_bars_in_both_stages(self, tmp_path):
        # 是正前から成立していた側（回帰の固定）。
        csv_path = _write_csv(tmp_path / "ohlc.csv")
        assert _candle_stage_times(csv_path, _AWARE_WINDOW) == _bar_stage_times(_AWARE_WINDOW)


class TestLocalTimezoneIndependence:
    """窓境界の意味が実行環境の TZ に依存しない（原因の除去）。"""

    @pytest.mark.parametrize("tz_name", ["UTC", "Asia/Tokyo", "America/New_York"])
    def test_both_stages_are_identical_under_any_local_timezone(self, tmp_path, tz_name):
        csv_path = _write_csv(tmp_path / "ohlc.csv")
        saved = os.environ.get("TZ")
        os.environ["TZ"] = tz_name
        time.tzset()
        try:
            candle = _candle_stage_times(csv_path, _NAIVE_WINDOW)
            bar = _bar_stage_times(_NAIVE_WINDOW)
        finally:
            if saved is None:
                os.environ.pop("TZ", None)
            else:
                os.environ["TZ"] = saved
            time.tzset()
        expected = [t for t in _BAR_EPOCHS if _epoch(2025, 1, 10) <= t < _epoch(2025, 1, 11)]
        assert candle == bar == expected


class TestSingleSourceOfTheRule:
    """正規化・半開判定の実体が 1 つであること（複製が入ると落ちる）。"""

    def test_candle_stage_reads_the_shared_window_type(self):
        assert csv_source_module.HalfOpenEpochWindow is HalfOpenEpochWindow

    def test_bar_stage_reads_the_shared_window_type(self):
        assert windowed_module.HalfOpenEpochWindow is HalfOpenEpochWindow

    def test_bar_time_converter_table_reads_the_shared_normalizer(self):
        # `bar.time` の datetime 変換器＝窓境界の変換器（規則を 2 つ持たない）。
        converters = [convert for _matches, convert in EPOCH_CONVERTERS]
        assert epoch_seconds_of_datetime in converters

    def test_half_open_predicate_has_a_single_owner(self):
        # 述語 `[start, end)` は `contains` ひとつ。両段が同じメソッドを読む。
        window = HalfOpenEpochWindow.from_datetimes(*_AWARE_WINDOW)
        assert window.contains(_epoch(2025, 1, 10))          # 始端は含む
        assert not window.contains(_epoch(2025, 1, 11))      # 終端は含まない
        assert not window.contains(_epoch(2025, 1, 9, 23))
