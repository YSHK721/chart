"""MarketDataSourceRepository（MarketDataPort 実装・marketdata 委譲＋Candle→Bar 写像）テスト（S5）。

設計正典: MARKETDATA_TIMESERIES_BOUNDARY_DESIGN.md §2.3（Candle→Bar 写像規則）/ §3.3（委譲
adapter class/method 図・TimeOrderError ガード）/ §6 S5 行 / §10.1 C-2（source_ref=(start,end)
半開）/ §10.2 H-4（spread=0 は spread 非依存戦略のみ）/ §7.2「_candles_to_bars 写像」「統合:
MarketDataSourceRepository」。

検証対象:
  1. MarketDataSourceRepository が MarketDataPort 実装（DIP: usecase は abc のみ依存）。
  2. fake CandleSource 注入 → load が Candle→Bar 写像（time int 直渡し・float 化・volume=
     c.get("volume",0.0)・spread=0）。
  3. C-2: source_ref=(start,end) を fetch_candles(start, end) へそのまま委譲（半開窓伝播）。
  4. OHLC 不整合 Candle → OHLCInvalidError（domain.Bar __post_init__ 検証が写像経由でも走る）。
  5. 非昇順 Candle → TimeOrderError（写像 adapter 側ガード）。
  6. バイト一致 oracle（最重要・§7.3）: 同一 CSV を CsvCandleSource 委譲経由で読んだ Bar 列が、
     既存 CsvOHLCRepository で読んだ Bar 列とフィールド単位（time/open/high/low/close/volume/
     spread）で完全一致する（写像経路の等価性）。

回帰観点（memory bugfix-pair-with-regression-test）:
  - 写像が崩れたら（volume を 0 固定・spread に非 0・time 改変等）oracle が落ちる。
  - 非昇順検出を外したら TimeOrderError テストが落ちる。
"""
from __future__ import annotations

import abc
from datetime import datetime, timezone

import pytest

from simulator.adapter.repository.marketdata_source import MarketDataSourceRepository
from simulator.domain.bar import Bar
from simulator.domain.exceptions import OHLCInvalidError, TimeOrderError
from simulator.usecase.ports import MarketDataPort


class _FakeCandleSource:
    """注入する CandleSource の test double（記録した start/end を保持）。"""

    def __init__(self, candles, *, record=None):
        self._candles = candles
        self._record = record if record is not None else {}

    def fetch_candles(self, start, end):
        self._record["start"] = start
        self._record["end"] = end
        return list(self._candles)


def _candle(t, o=1.10, h=1.20, low=1.05, c=1.15, volume=100.0):
    return {"time": t, "open": o, "high": h, "low": low, "close": c, "volume": volume}


def test_repository_is_market_data_port_subclass():
    assert issubclass(MarketDataSourceRepository, MarketDataPort)
    assert issubclass(MarketDataPort, abc.ABC)
    repo = MarketDataSourceRepository(_FakeCandleSource([]))
    assert isinstance(repo, MarketDataPort)


def test_load_maps_candle_to_bar_fields():
    # Arrange: OHLC 整合 candle 1 本
    src = _FakeCandleSource([_candle(1_700_000_000, volume=42.0)])
    repo = MarketDataSourceRepository(src)
    window = (
        datetime(2023, 11, 14, tzinfo=timezone.utc),
        datetime(2023, 11, 15, tzinfo=timezone.utc),
    )
    # Act
    bars = repo.load(window, None, None)
    # Assert: 写像規則（§2.3）
    assert len(bars) == 1
    b = bars[0]
    assert isinstance(b, Bar)
    assert b.time == 1_700_000_000  # int 直渡し
    assert (b.open, b.high, b.low, b.close) == (1.10, 1.20, 1.05, 1.15)
    assert b.volume == 42.0
    assert b.spread == 0  # H-4: spread 非依存戦略向け既定 0


def test_load_defaults_volume_to_zero_when_candle_lacks_volume():
    # Arrange: volume キー無し candle（c.get("volume", 0.0)）
    c = {"time": 1, "open": 1.0, "high": 1.0, "low": 1.0, "close": 1.0}
    repo = MarketDataSourceRepository(_FakeCandleSource([c]))
    # Act
    bars = repo.load(
        (datetime(1970, 1, 1, tzinfo=timezone.utc), datetime(1970, 1, 2, tzinfo=timezone.utc)),
        None,
        None,
    )
    # Assert
    assert bars[0].volume == 0.0


def test_load_passes_start_end_window_to_fetch_candles():
    # Arrange: C-2 半開窓 (start, end) を fetch_candles へ伝播
    rec = {}
    src = _FakeCandleSource([_candle(1)], record=rec)
    repo = MarketDataSourceRepository(src)
    start = datetime(2024, 1, 1, tzinfo=timezone.utc)
    end = datetime(2024, 1, 2, tzinfo=timezone.utc)
    # Act
    repo.load((start, end), None, None)
    # Assert: source_ref=(start,end) がそのまま fetch_candles へ
    assert rec["start"] == start
    assert rec["end"] == end


def test_load_raises_ohlc_invalid_error_on_inconsistent_candle():
    # Arrange: high < low の不整合 candle（domain.Bar が __post_init__ で検証）
    bad = {"time": 1, "open": 1.10, "high": 1.00, "low": 1.20, "close": 1.15, "volume": 1.0}
    repo = MarketDataSourceRepository(_FakeCandleSource([bad]))
    # Act / Assert
    with pytest.raises(OHLCInvalidError):
        repo.load(
            (datetime(1970, 1, 1, tzinfo=timezone.utc), datetime(1970, 1, 2, tzinfo=timezone.utc)),
            None,
            None,
        )


def test_load_raises_time_order_error_when_candles_not_ascending():
    # Arrange: 写像 adapter 側の昇順ガード（§3.3）
    src = _FakeCandleSource([_candle(120), _candle(60)])  # 逆転
    repo = MarketDataSourceRepository(src)
    # Act / Assert
    with pytest.raises(TimeOrderError):
        repo.load(
            (datetime(1970, 1, 1, tzinfo=timezone.utc), datetime(1970, 1, 2, tzinfo=timezone.utc)),
            None,
            None,
        )


# --- バイト一致 oracle（最重要・§7.3）: 委譲経路 ≡ 既存 CsvOHLCRepository 経路 ---

def test_delegation_bars_byte_equal_to_csv_ohlc_repository(tmp_path):
    """同一 CSV を「CsvCandleSource 委譲」と「CsvOHLCRepository 直読み」で読み、Bar 列が
    フィールド単位で完全一致することを実証する（写像経路の等価性・report.json 再現性の土台）。
    """
    from marketdata.csv_source import CsvCandleSource
    from simulator.adapter.repository.ohlc_csv import CsvOHLCRepository

    # Arrange: time が UNIX 秒 int の comma 形式 CSV（spread 列含む＝CsvOHLCRepository 必須列）。
    t0 = int(datetime(2024, 1, 1, tzinfo=timezone.utc).timestamp())
    rows = [
        (t0 + 0, 1.10, 1.20, 1.05, 1.15, 100.0, 0),
        (t0 + 60, 1.15, 1.25, 1.10, 1.20, 110.0, 0),
        (t0 + 120, 1.20, 1.30, 1.18, 1.28, 120.0, 0),
    ]
    header = "time,open,high,low,close,volume,spread"
    csv = tmp_path / "oracle.csv"
    csv.write_text(
        "\n".join([header] + [",".join(str(c) for c in r) for r in rows]) + "\n",
        encoding="utf-8",
    )

    # Act: 既存経路（golden）と委譲経路
    expected = CsvOHLCRepository().load(csv, None, None)
    delegated = MarketDataSourceRepository(CsvCandleSource(csv)).load(
        (
            datetime(2024, 1, 1, tzinfo=timezone.utc),
            datetime(2024, 1, 2, tzinfo=timezone.utc),
        ),
        None,
        None,
    )

    # Assert: フィールド単位の完全一致（time/open/high/low/close/volume/spread）
    assert len(delegated) == len(expected) == 3
    for d, e in zip(delegated, expected):
        assert int(d.time) == int(e.time)
        assert d.open == e.open
        assert d.high == e.high
        assert d.low == e.low
        assert d.close == e.close
        assert d.volume == e.volume
        assert d.spread == e.spread
