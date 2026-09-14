"""WindowedMarketDataRepository（A-3）: 取得窓を任意の MarketDataPort 実装へ効かせるデコレータ。

A-3（L-2 の解消）: 現行 `marketdata_window` は `CsvOHLCRepository` のときだけ委譲 repo へ
差し替わり、`Mt5CsvOHLCRepository` では無視される（実測: 窓あり/なしで bars の sha256 が
同一・28097 本）。本デコレータは `load` の署名・各 repository の実装・`_ohlc_frame` を
1 行も変えずに、**合成**で窓を適用する。

本モジュールが固定する契約:
  1. 委譲: `load(source_ref, timeframe, period)` の 3 引数を内側 port へそのまま渡す
     （LSP: 内側の事前条件を強化しない）。
  2. 半開区間 `[start, end)`（`marketdata/csv_source.py:59` の `t < start_ts or t >= end_ts`
     と同一規約）。
  3. 保存: 窓内に残した Bar は**同一インスタンス**（spread を含む全フィールドが無改変）。
     spread を 0 へ潰す `MarketDataSourceRepository` 経路との差を固定する（H-4 の禁止）。
  4. 時刻型の非対称性: 内側 port の `bar.time` は epoch int（comma 形式）と
     `numpy.datetime64`（MT5 タブ形式）の両方があり、窓境界は UTC aware datetime。
     比較は `simulator.domain.bar_time.epoch_seconds` で正規化する（単一ソース）。
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import numpy as np
import pytest

from simulator.adapter.repository.windowed_market_data import WindowedMarketDataRepository
from simulator.domain.bar import Bar
from simulator.usecase.ports import MarketDataPort


def _epoch(*args) -> int:
    return int(datetime(*args, tzinfo=timezone.utc).timestamp())


class _RecordingPort(MarketDataPort):
    """内側 port の代役。受け取った load 引数と返す bars を記録・固定する。"""

    def __init__(self, bars: "list[Bar]") -> None:
        self._bars = bars
        self.calls: "list[tuple[Any, Any, Any]]" = []

    def load(self, source_ref: Any, timeframe: Any = None, period: Any = None) -> "list[Bar]":
        self.calls.append((source_ref, timeframe, period))
        return list(self._bars)


def _bar(time: Any, *, spread: int = 7) -> Bar:
    return Bar(time=time, open=100.0, high=101.0, low=99.0, close=100.5, volume=1.0, spread=spread)


#: epoch int の 5 本（2024-01-01 〜 2024-01-05 の 00:00Z）。
_EPOCH_BARS = [_bar(_epoch(2024, 1, d)) for d in range(1, 6)]

#: numpy.datetime64 の 5 本（同じ 5 日・MT5 タブ形式ローダの実読型）。
_DT64_BARS = [_bar(np.datetime64(f"2024-01-0{d}T00:00:00")) for d in range(1, 6)]

_WINDOW = (
    datetime(2024, 1, 2, tzinfo=timezone.utc),
    datetime(2024, 1, 4, tzinfo=timezone.utc),
)


class TestPortSubstitutability:
    """LSP: MarketDataPort として内側 port と置換可能であること。"""

    def test_is_market_data_port_subclass(self):
        assert issubclass(WindowedMarketDataRepository, MarketDataPort)

    def test_load_delegates_all_three_arguments_unchanged(self):
        inner = _RecordingPort(_EPOCH_BARS)
        repo = WindowedMarketDataRepository(inner, window=_WINDOW)
        repo.load("some/path.csv", "M1", "202501")
        # 窓は構築時パラメータへ隔離する（ISSUE-135）。load の引数は素通しする。
        assert inner.calls == [("some/path.csv", "M1", "202501")]

    def test_load_accepts_default_timeframe_and_period(self):
        inner = _RecordingPort(_EPOCH_BARS)
        repo = WindowedMarketDataRepository(inner, window=_WINDOW)
        repo.load("some/path.csv")
        assert inner.calls == [("some/path.csv", None, None)]

    def test_inner_port_is_observable(self):
        # 合成の相手を検証できないと「委譲経路に紛れ込んでいない」を測れない（H-4 の壁）。
        inner = _RecordingPort(_EPOCH_BARS)
        assert WindowedMarketDataRepository(inner, window=_WINDOW).inner is inner


class TestHalfOpenWindow:
    """半開区間 `[start, end)`（`marketdata/csv_source.py:59` と同一規約）。"""

    def test_epoch_int_bars_are_filtered_half_open(self):
        repo = WindowedMarketDataRepository(_RecordingPort(_EPOCH_BARS), window=_WINDOW)
        bars = repo.load("x")
        assert [b.time for b in bars] == [_epoch(2024, 1, 2), _epoch(2024, 1, 3)]

    def test_numpy_datetime64_bars_are_filtered_half_open(self):
        repo = WindowedMarketDataRepository(_RecordingPort(_DT64_BARS), window=_WINDOW)
        bars = repo.load("x")
        assert [str(b.time) for b in bars] == ["2024-01-02T00:00:00", "2024-01-03T00:00:00"]

    def test_start_boundary_is_inclusive(self):
        repo = WindowedMarketDataRepository(_RecordingPort(_EPOCH_BARS), window=_WINDOW)
        assert repo.load("x")[0].time == _epoch(2024, 1, 2)

    def test_end_boundary_is_exclusive(self):
        repo = WindowedMarketDataRepository(_RecordingPort(_EPOCH_BARS), window=_WINDOW)
        assert all(b.time < _epoch(2024, 1, 4) for b in repo.load("x"))

    def test_window_none_passes_every_bar_through(self):
        # 窓なしは素通し＝既定経路の byte 等価（合成しても差が出ないことの固定）。
        repo = WindowedMarketDataRepository(_RecordingPort(_EPOCH_BARS), window=None)
        assert repo.load("x") == _EPOCH_BARS

    def test_window_outside_data_yields_empty_list(self):
        far = (
            datetime(2030, 1, 1, tzinfo=timezone.utc),
            datetime(2030, 1, 2, tzinfo=timezone.utc),
        )
        repo = WindowedMarketDataRepository(_RecordingPort(_EPOCH_BARS), window=far)
        assert repo.load("x") == []


class TestBarsArePreservedNotRebuilt:
    """窓は「絞る」だけで写像しない（spread を含む全フィールドが無改変）。"""

    def test_kept_bars_are_the_same_instances(self):
        repo = WindowedMarketDataRepository(_RecordingPort(_EPOCH_BARS), window=_WINDOW)
        kept = repo.load("x")
        assert [id(b) for b in kept] == [id(_EPOCH_BARS[1]), id(_EPOCH_BARS[2])]

    def test_spread_is_preserved_and_not_collapsed_to_zero(self):
        # `MarketDataSourceRepository._candles_to_bars` は spread=0 固定（marketdata_source.py:51）。
        # 本デコレータへ寄せても spread 依存戦略の約定価格式が壊れないことを固定する。
        inner = _RecordingPort([_bar(_epoch(2024, 1, 2), spread=123)])
        repo = WindowedMarketDataRepository(inner, window=_WINDOW)
        assert [b.spread for b in repo.load("x")] == [123]

    def test_time_order_is_preserved(self):
        repo = WindowedMarketDataRepository(_RecordingPort(_EPOCH_BARS), window=_WINDOW)
        times = [b.time for b in repo.load("x")]
        assert times == sorted(times)


class TestUnsupportedTimeRepresentation:
    """推測で解釈しない（未対応の時刻表現は fail-stop）。

    ISSUE-411 スライス 3 以降、未対応の時刻表現は `Bar` の構築時点で `ConfigError` になる
    （`Bar.__post_init__` の契約表明）。したがって「`Bar` 型としては構築不能」の固定は
    `simulator/tests/unit/test_bar.py::TestBarTimeContract` が担う。

    本クラスが担うのは**デコレータ自身の**契約である。内側 port は `MarketDataPort`
    実装であれば何でもよく（LSP: 事前条件を強化しない）、`Bar` 型を返すとは限らない。
    そこで duck-typed stub を返させ、「窓比較が未対応の時刻表現を推測解釈せず
    `ConfigError` を送出する」ことをデコレータ単体で観測する。
    """

    def test_unknown_bar_time_type_raises_config_error(self):
        import types

        from simulator.domain.exceptions import ConfigError

        # Arrange: 内側 port が time だけを持つ duck-typed stub を返す（ISO 文字列は未対応）
        inner = _RecordingPort([types.SimpleNamespace(time="2024-01-02T00:00:00")])
        repo = WindowedMarketDataRepository(inner, window=_WINDOW)
        # Act / Assert
        with pytest.raises(ConfigError):
            repo.load("x")
