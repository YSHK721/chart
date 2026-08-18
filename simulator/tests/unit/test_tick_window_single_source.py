"""Tick 段の窓境界解釈が `datawindow.half_open` の規則と**同一**であることを固定する。

是正対象（ISSUE-402 の実測 / 是正前の状態）:
    窓境界 `[start, end)` の解釈規則が 2 系統に分かれていた。

      - Bar / Candle 段: `datawindow.half_open` が単一ソース。境界は
        `simulator.domain.bar_time.epoch_seconds` が epoch int / aware datetime /
        naive datetime（= UTC）/ ``numpy.datetime64`` の 4 表現を受理する。
      - Tick 段 (`adapter/repository/tick_parquet.py`): 保存 timestamp が naive UTC
        固定であることを理由に、境界も naive `pandas.Timestamp` のみが成立した。
        aware datetime は pandas の生 ``TypeError`` を ``DataError`` へ翻訳して失敗、
        epoch int は `_date_predicate` の ``start.year`` 参照で ``AttributeError`` →
        ``DataError`` で失敗した（是正前の実測）。

    すなわち Tick 段だけが「naive `Timestamp` 以外を受け付けない」という別規則を
    持っていた。本モジュールはこの**非対称の解消**を行動で固定する。

本モジュールが固定する契約（行動テストが主・同一性テストが従）:
  1. **同一表現の受理**: epoch int / aware datetime / naive datetime のいずれで窓を
     与えても、同じ瞬間を指す限り**同一の tick 集合**が返る。
  2. **naive = UTC**: naive 境界の意味はプロセスのローカル TZ に依存しない
     （既存合意 `simulator/tests/unit/test_bar_time_epoch.py` と同一）。
  3. **段をまたいだ規則の一致**: 同じ窓を Bar 段（`WindowedMarketDataRepository`）と
     Tick 段へ与えると、選ばれる時刻集合が一致する。
  4. **単一ソース**: 境界正規化と半開判定の実体は共有オブジェクト
     （`simulator.domain.bar_time.epoch_seconds` /
     `datawindow.half_open.HalfOpenEpochWindow`）であり、Tick 段はそれを読む。

契約 4 の同一性テストだけでは規則の食い違いを検出できない（import しつつ別の比較を
書けば通る）ため、1〜3 の行動テストを主に置く。
"""
from __future__ import annotations

import os
import time
from datetime import datetime, timedelta, timezone
from typing import Any

import pandas as pd
import pytest

from datawindow.half_open import HalfOpenEpochWindow
from simulator.adapter.repository import tick_parquet as tick_parquet_module
from simulator.adapter.repository.tick_parquet import ParquetTickRepository
from simulator.adapter.repository.windowed_market_data import WindowedMarketDataRepository
from simulator.domain.bar import Bar
from simulator.domain.bar_time import epoch_seconds
from simulator.usecase.ports import MarketDataPort

_JST = timezone(timedelta(hours=9))


def _epoch(*args: int) -> int:
    return int(datetime(*args, tzinfo=timezone.utc).timestamp())


#: 窓の内外にまたがる tick の UTC 時刻（1/9 22:00Z 〜 1/11 02:00Z）。
#: ローカル TZ を JST と誤解釈した窓（= UTC 窓の 9 時間前）と UTC 窓の双方に
#: 「入る」「入らない」が両方存在するように置く（差が観測可能であること）。
_TICK_TIMES = [
    datetime(2025, 1, 9, 22, 0),   # UTC 窓外 / JST 誤解釈窓内
    datetime(2025, 1, 10, 0, 0),   # UTC 窓の始端（含む）
    datetime(2025, 1, 10, 12, 0),  # 両方の窓内
    datetime(2025, 1, 10, 15, 0),  # UTC 窓内 / JST 誤解釈窓の終端（含まない）
    datetime(2025, 1, 10, 23, 0),  # UTC 窓内 / JST 誤解釈窓外
    datetime(2025, 1, 11, 2, 0),   # 両方の窓外
]

#: 検証窓（naive / aware UTC / aware JST / epoch int の 4 表現で同じ瞬間を指す）。
_NAIVE_WINDOW = (datetime(2025, 1, 10), datetime(2025, 1, 11))
_AWARE_WINDOW = (
    datetime(2025, 1, 10, tzinfo=timezone.utc),
    datetime(2025, 1, 11, tzinfo=timezone.utc),
)
_JST_WINDOW = (
    datetime(2025, 1, 10, 9, tzinfo=_JST),
    datetime(2025, 1, 11, 9, tzinfo=_JST),
)
_EPOCH_WINDOW = (_epoch(2025, 1, 10), _epoch(2025, 1, 11))

#: 上記窓が選ぶべき tick の epoch（半開 `[start, end)`）。
_EXPECTED_EPOCHS = [
    _epoch(2025, 1, 10, 0, 0),
    _epoch(2025, 1, 10, 12, 0),
    _epoch(2025, 1, 10, 15, 0),
    _epoch(2025, 1, 10, 23, 0),
]


def _tick_frame() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "timestamp": pd.to_datetime(_TICK_TIMES),
            "bid": [100.0 + i for i in range(len(_TICK_TIMES))],
            "ask": [100.5 + i for i in range(len(_TICK_TIMES))],
            "last": [100.25 + i for i in range(len(_TICK_TIMES))],
            "volume": [1] * len(_TICK_TIMES),
        }
    )


@pytest.fixture()
def written_repo(tmp_path):
    repo = ParquetTickRepository(root=tmp_path)
    repo.write_ticks("JP225", _tick_frame(), mode="overwrite")
    return repo


def _loaded_epochs(repo: ParquetTickRepository, window) -> "list[int]":
    frame = repo.load_ticks("JP225", window[0], window[1])
    # 解像度・tz 非依存の epoch 秒化（保存 dtype は datetime64[us] / [ns] / tz-aware の
    # いずれもありうる。`astype("int64") // 1e9` は ns 前提の誤変換になるため使わない）。
    ts = (
        pd.to_datetime(frame["timestamp"], utc=True)
        .dt.tz_localize(None)
        .astype("datetime64[s]")
        .astype("int64")
    )
    return [int(v) for v in ts.tolist()]


class _FixedBarsPort(MarketDataPort):
    """内側 port の代役（Bar 段の窓合成だけを測る）。"""

    def __init__(self, bars: "list[Bar]") -> None:
        self._bars = bars

    def load(self, source_ref: Any, timeframe: Any = None, period: Any = None) -> "list[Bar]":
        return list(self._bars)


def _bar_stage_epochs(window) -> "list[int]":
    bars = [
        Bar(time=_epoch_of(t), open=1.0, high=1.0, low=1.0, close=1.0, volume=1.0, spread=1)
        for t in _TICK_TIMES
    ]
    repo = WindowedMarketDataRepository(_FixedBarsPort(bars), window=window)
    return [bar.time for bar in repo.load("x")]


def _epoch_of(naive_utc: datetime) -> int:
    return int(naive_utc.replace(tzinfo=timezone.utc).timestamp())


@pytest.fixture()
def tokyo_local_timezone():
    """プロセスのローカル TZ を Asia/Tokyo に固定する（UTC との差 +9h）。"""
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


class TestTickStageAcceptsTheSameTimeRepresentations:
    """契約 1: epoch int / aware / naive のいずれでも同一 tick 集合（是正前は前 2 者が失敗）。"""

    def test_naive_datetime_bounds_select_the_window(self, written_repo):
        # 是正前から成立していた唯一の形（回帰の固定）。
        assert _loaded_epochs(written_repo, _NAIVE_WINDOW) == _EXPECTED_EPOCHS

    def test_naive_pandas_timestamp_bounds_select_the_window(self, written_repo):
        # 既存の本番呼出（`main._bar_period` / `run_scan_contacts_cli`）が渡す形。
        window = (pd.Timestamp(_NAIVE_WINDOW[0]), pd.Timestamp(_NAIVE_WINDOW[1]))
        assert _loaded_epochs(written_repo, window) == _EXPECTED_EPOCHS

    def test_aware_utc_datetime_bounds_select_the_same_window(self, written_repo):
        # 是正前: pandas の生 TypeError を DataError へ翻訳して失敗していた。
        assert _loaded_epochs(written_repo, _AWARE_WINDOW) == _EXPECTED_EPOCHS

    def test_aware_non_utc_datetime_bounds_select_the_same_window(self, written_repo):
        # aware は自身の offset で解釈する（JST 09:00 = UTC 00:00）。
        assert _loaded_epochs(written_repo, _JST_WINDOW) == _EXPECTED_EPOCHS

    def test_epoch_int_bounds_select_the_same_window(self, written_repo):
        # 是正前: `_date_predicate` の `start.year` 参照で AttributeError → DataError。
        # comma 形式 CSV 経路の `main._bar_period` は epoch int を返すためこの形が来る。
        assert _loaded_epochs(written_repo, _EPOCH_WINDOW) == _EXPECTED_EPOCHS

    def test_all_representations_are_byte_identical_frames(self, written_repo):
        base = written_repo.load_ticks("JP225", *_NAIVE_WINDOW)
        assert len(base) == len(_EXPECTED_EPOCHS)  # 空一致で通る当たりを塞ぐ
        for window in (_AWARE_WINDOW, _JST_WINDOW, _EPOCH_WINDOW):
            other = written_repo.load_ticks("JP225", *window)
            pd.testing.assert_frame_equal(base, other)

    def test_columns_pushdown_is_identical_across_representations(self, written_repo):
        # columns 指定は timestamp を読み足す別枝を通る。窓表現に依らず同一であること。
        base = written_repo.load_ticks("JP225", *_NAIVE_WINDOW, columns=["bid", "ask"])
        assert list(base.columns) == ["bid", "ask"]
        assert len(base) == len(_EXPECTED_EPOCHS)
        for window in (_AWARE_WINDOW, _JST_WINDOW, _EPOCH_WINDOW):
            other = written_repo.load_ticks("JP225", *window, columns=["bid", "ask"])
            pd.testing.assert_frame_equal(base, other)


class TestNaiveBoundsAreUtcRegardlessOfLocalTimezone:
    """契約 2: naive 境界の意味が実行環境の TZ に依存しない（原因の除去）。"""

    def test_naive_equals_aware_under_tokyo_local_timezone(
        self, written_repo, tokyo_local_timezone
    ):
        assert _loaded_epochs(written_repo, _NAIVE_WINDOW) == _EXPECTED_EPOCHS

    @pytest.mark.parametrize("tz_name", ["UTC", "Asia/Tokyo", "America/New_York"])
    def test_result_is_identical_under_any_local_timezone(self, written_repo, tz_name):
        saved = os.environ.get("TZ")
        os.environ["TZ"] = tz_name
        time.tzset()
        try:
            got = _loaded_epochs(written_repo, _NAIVE_WINDOW)
            got_epoch_bounds = _loaded_epochs(written_repo, _EPOCH_WINDOW)
        finally:
            if saved is None:
                os.environ.pop("TZ", None)
            else:
                os.environ["TZ"] = saved
            time.tzset()
        assert got == got_epoch_bounds == _EXPECTED_EPOCHS


class TestTickStageMatchesBarStage:
    """契約 3: 同じ窓に対し Bar 段と Tick 段が同じ時刻集合を選ぶ（段をまたいだ規則一致）。"""

    @pytest.mark.parametrize(
        "window", [_NAIVE_WINDOW, _AWARE_WINDOW, _JST_WINDOW, _EPOCH_WINDOW]
    )
    def test_both_stages_select_the_same_instants(self, written_repo, window):
        assert _loaded_epochs(written_repo, window) == _bar_stage_epochs(window)

    def test_both_stages_agree_under_tokyo_local_timezone(
        self, written_repo, tokyo_local_timezone
    ):
        assert _loaded_epochs(written_repo, _NAIVE_WINDOW) == _bar_stage_epochs(_NAIVE_WINDOW)


class TestHalfOpenBoundaryIsFixed:
    """半開 `[start, end)`: 始端の tick は含み、終端の tick は含まない。"""

    def test_start_boundary_tick_is_included_and_end_boundary_tick_is_excluded(
        self, written_repo
    ):
        window = (_epoch(2025, 1, 10, 12, 0), _epoch(2025, 1, 10, 23, 0))
        assert _loaded_epochs(written_repo, window) == [
            _epoch(2025, 1, 10, 12, 0),
            _epoch(2025, 1, 10, 15, 0),
        ]

    def test_empty_window_returns_empty_frame(self, written_repo):
        window = (_epoch(2025, 1, 10, 12, 0), _epoch(2025, 1, 10, 12, 0))
        assert _loaded_epochs(written_repo, window) == []

    def test_inverted_window_returns_empty_frame(self, written_repo):
        # `HalfOpenEpochWindow` は start > end を空窓として扱う（例外にしない）。
        window = (_epoch(2025, 1, 10, 23, 0), _epoch(2025, 1, 10, 12, 0))
        assert _loaded_epochs(written_repo, window) == []

    def test_empty_window_frame_keeps_the_tick_columns(self, written_repo):
        # 空窓は part を 1 つも読まない（是正で変わった唯一の点）。行数 0・列名は不変。
        from simulator.adapter.repository._tick_frame import TICK_COLUMNS

        frame = written_repo.load_ticks(
            "JP225", _epoch(2025, 1, 10, 12, 0), _epoch(2025, 1, 10, 12, 0)
        )
        assert len(frame) == 0
        assert list(frame.columns) == list(TICK_COLUMNS)


class TestStoredColumnTimezoneDoesNotChangeTheResult:
    """保存列が naive / tz-aware のいずれでも同じ UTC epoch として判定される。

    保存契約は naive UTC だが、判定式が tz の有無で分岐すると規則が再び 2 つになる
    （実測: aware 列へ ``astype("datetime64[s]")`` を直接当てると pandas が
    ``TypeError``）。tz の有無に依存しない変換であることを行動で固定する。
    """

    def test_tz_aware_stored_column_yields_the_same_window(self, tmp_path):
        frame = _tick_frame()
        frame["timestamp"] = pd.to_datetime(frame["timestamp"]).dt.tz_localize("UTC")
        repo = ParquetTickRepository(root=tmp_path)
        repo.write_ticks("JP225", frame, mode="overwrite")

        assert _loaded_epochs(repo, _AWARE_WINDOW) == _EXPECTED_EPOCHS
        assert _loaded_epochs(repo, _NAIVE_WINDOW) == _EXPECTED_EPOCHS
        assert _loaded_epochs(repo, _EPOCH_WINDOW) == _EXPECTED_EPOCHS


class TestNumpyDatetime64Bounds:
    """`bar.time` の 4 表現目（``numpy.datetime64``）も同じ窓解釈で動く。"""

    def test_numpy_datetime64_bounds_select_the_same_window(self, written_repo):
        import numpy as np

        window = (
            np.datetime64("2025-01-10T00:00:00", "s"),
            np.datetime64("2025-01-11T00:00:00", "s"),
        )
        assert _loaded_epochs(written_repo, window) == _EXPECTED_EPOCHS


class TestUnsupportedRepresentationRaisesConfigError:
    """未対応の時刻表現は `epoch_seconds` の契約どおり `ConfigError`（Bar 段と対称）。"""

    def test_string_bounds_raise_config_error(self, written_repo):
        from simulator.domain.exceptions import ConfigError

        with pytest.raises(ConfigError):
            written_repo.load_ticks("JP225", "2025-01-10", "2025-01-11")


class TestSingleSourceOfTheRule:
    """契約 4（従）: 正規化・半開判定の実体が Bar / Candle 段と同一オブジェクトである。"""

    def test_tick_stage_reads_the_shared_window_type(self):
        assert tick_parquet_module.HalfOpenEpochWindow is HalfOpenEpochWindow

    def test_tick_stage_reads_the_shared_normalizer(self):
        assert tick_parquet_module.epoch_seconds is epoch_seconds

    def test_the_normalizer_chain_reaches_datawindow(self):
        # Tick 段 → `bar_time.epoch_seconds` → `datawindow.half_open` の 3 者が
        # 1 本の鎖でつながっていること（鎖のどこかに複製が入ると落ちる）。
        from datawindow.half_open import epoch_seconds_of_datetime
        from simulator.domain.bar_time import EPOCH_CONVERTERS

        assert epoch_seconds_of_datetime in [convert for _matches, convert in EPOCH_CONVERTERS]
        assert tick_parquet_module.epoch_seconds is epoch_seconds

    def test_bar_stage_and_tick_stage_read_the_same_objects(self):
        # 段ごとに別の実体を掴んでいないこと（Bar 段との直接比較）。
        from simulator.adapter.repository import windowed_market_data as windowed_module

        assert tick_parquet_module.HalfOpenEpochWindow is windowed_module.HalfOpenEpochWindow
        assert tick_parquet_module.epoch_seconds is windowed_module.epoch_seconds

    def test_tick_stage_predicate_is_the_shared_contains(self):
        # Tick 段が用いる述語は `HalfOpenEpochWindow.contains` そのものである。
        window = HalfOpenEpochWindow(*_EPOCH_WINDOW)
        assert window.contains(_EXPECTED_EPOCHS[0])
        assert not window.contains(_EPOCH_WINDOW[1])


class TestDatePredicateEnumeratesUtcDays:
    """partition プルーニングの日列挙も epoch 秒・UTC 基準で行う。"""

    def test_enumerates_days_covering_the_half_open_epoch_range(self):
        from simulator.adapter.repository._tick_frame import _date_predicate

        days = _date_predicate(_epoch(2024, 3, 1), _epoch(2024, 3, 4))
        assert days == [(2024, 3, 1), (2024, 3, 2), (2024, 3, 3)]

    def test_end_at_day_boundary_does_not_enumerate_the_end_day(self):
        from simulator.adapter.repository._tick_frame import _date_predicate

        assert _date_predicate(_epoch(2024, 3, 1), _epoch(2024, 3, 2)) == [(2024, 3, 1)]

    def test_one_second_past_midnight_enumerates_the_end_day(self):
        from simulator.adapter.repository._tick_frame import _date_predicate

        days = _date_predicate(_epoch(2024, 3, 1), _epoch(2024, 3, 2) + 1)
        assert days == [(2024, 3, 1), (2024, 3, 2)]

    def test_days_are_enumerated_in_utc_not_local_time(self, tokyo_local_timezone):
        from simulator.adapter.repository._tick_frame import _date_predicate

        # UTC で 3/1 00:00 〜 3/2 00:00 は 3/1 のみ。JST 解釈なら 2 日にまたがる。
        assert _date_predicate(_epoch(2024, 3, 1), _epoch(2024, 3, 2)) == [(2024, 3, 1)]

    def test_empty_window_enumerates_no_days(self):
        from simulator.adapter.repository._tick_frame import _date_predicate

        assert _date_predicate(_epoch(2024, 3, 2), _epoch(2024, 3, 2)) == []
