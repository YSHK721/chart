"""MarketDataSourceRepository（MarketDataPort 実装・marketdata 委譲＋Candle→Bar 写像）テスト（S5）。

設計正典: MARKETDATA_TIMESERIES_BOUNDARY_DESIGN.md §2.3（Candle→Bar 写像規則）/ §3.3（委譲
adapter class/method 図・TimeOrderError ガード）/ §6 S5 行 / §10.1 C-2（取得窓 (start,end)
半開）/ §10.2 H-4（spread=0 は spread 非依存戦略のみ）/ §7.2「_candles_to_bars 写像」「統合:
MarketDataSourceRepository」。

ISSUE-135（LSP 是正）: MarketDataPort.load の source_ref を path 系 3 実装（CSV/TSV/parquet）
と対称化する。取得窓 (start,end) は本実装固有の選択軸であり **構築時パラメータ（window）へ
隔離** する。これにより load の source_ref はアンパック不要（path 系と置換可能）になり、
composition root の load_source 型別作り分けが不要になる。例外契約も path 系 3 実装と対称化し、
構築時固定の永続実体不在（I/O 失敗）は生 I/O 例外でなく DataError へ翻訳する。

検証対象:
  1. MarketDataSourceRepository が MarketDataPort 実装（DIP: usecase は abc のみ依存）。
  2. fake CandleSource 注入 → load が Candle→Bar 写像（time int 直渡し・float 化・volume=
     c.get("volume",0.0)・spread=0）。
  3. C-2: 構築時 window=(start,end) を fetch_candles(start, end) へそのまま委譲（半開窓伝播）。
  4. OHLC 不整合 Candle → OHLCInvalidError（domain.Bar __post_init__ 検証が写像経由でも走る）。
  5. 非昇順 Candle → TimeOrderError（写像 adapter 側ガード）。
  6. バイト一致 oracle（最重要・§7.3）: 同一 CSV を CsvCandleSource 委譲経由で読んだ Bar 列が、
     既存 CsvOHLCRepository で読んだ Bar 列とフィールド単位（time/open/high/low/close/volume/
     spread）で完全一致する（写像経路の等価性）。
  7. LSP 対称性（ISSUE-135）: 取得窓は構築時固定・load の source_ref はアンパックされず path 系
     実装と同型（path 文字列）を受理する（置換可能性）。
  8. 例外契約対称性（ISSUE-135）: 構築時固定の永続実体が不在なら DataError（生 FileNotFoundError
     を漏らさない・cause を chain）。

回帰観点（memory bugfix-pair-with-regression-test）:
  - 写像が崩れたら（volume を 0 固定・spread に非 0・time 改変等）oracle が落ちる。
  - 非昇順検出を外したら TimeOrderError テストが落ちる。
  - load が source_ref をアンパックし直したら LSP 対称性テストが落ちる。
  - fetch の I/O 失敗翻訳を外したら例外契約対称性テストが落ちる。
"""
from __future__ import annotations

import abc
from datetime import datetime, timezone

import pytest

from simulator.adapter.repository.marketdata_source import MarketDataSourceRepository
from simulator.domain.bar import Bar
from simulator.domain.exceptions import DataError, OHLCInvalidError, TimeOrderError
from simulator.usecase.ports import MarketDataPort

_WINDOW = (
    datetime(2024, 1, 1, tzinfo=timezone.utc),
    datetime(2024, 1, 2, tzinfo=timezone.utc),
)


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
    repo = MarketDataSourceRepository(_FakeCandleSource([]), window=_WINDOW)
    assert isinstance(repo, MarketDataPort)


def test_load_maps_candle_to_bar_fields():
    # Arrange: OHLC 整合 candle 1 本
    src = _FakeCandleSource([_candle(1_700_000_000, volume=42.0)])
    repo = MarketDataSourceRepository(src, window=_WINDOW)
    # Act: source_ref は path 系と対称（未使用）。任意の値でよい。
    bars = repo.load(None, None, None)
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
    repo = MarketDataSourceRepository(_FakeCandleSource([c]), window=_WINDOW)
    # Act
    bars = repo.load(None, None, None)
    # Assert
    assert bars[0].volume == 0.0


def test_load_passes_constructor_window_to_fetch_candles():
    # Arrange: C-2 半開窓 (start, end) を構築時に固定 → fetch_candles へ伝播
    rec = {}
    src = _FakeCandleSource([_candle(1)], record=rec)
    start = datetime(2024, 1, 1, tzinfo=timezone.utc)
    end = datetime(2024, 1, 2, tzinfo=timezone.utc)
    repo = MarketDataSourceRepository(src, window=(start, end))
    # Act
    repo.load(None, None, None)
    # Assert: 構築時 window=(start,end) がそのまま fetch_candles へ
    assert rec["start"] == start
    assert rec["end"] == end


def test_load_ignores_source_ref_and_accepts_path_like(tmp_path):
    """ISSUE-135 LSP 対称性: load は source_ref をアンパックしない。path 系 3 実装と同じく
    path 文字列を受理し、取得窓は構築時窓から解決する（置換可能性）。"""
    rec = {}
    src = _FakeCandleSource([_candle(1)], record=rec)
    repo = MarketDataSourceRepository(src, window=_WINDOW)
    # Act: path 系実装が受ける path 文字列を source_ref に渡してもアンパックエラーにならない
    bars = repo.load("some/path.csv", None, None)
    # Assert: 取得窓は構築時窓・source_ref は写像に影響しない
    assert len(bars) == 1
    assert rec["start"] == _WINDOW[0]
    assert rec["end"] == _WINDOW[1]


def test_load_raises_ohlc_invalid_error_on_inconsistent_candle():
    # Arrange: high < low の不整合 candle（domain.Bar が __post_init__ で検証）
    bad = {"time": 1, "open": 1.10, "high": 1.00, "low": 1.20, "close": 1.15, "volume": 1.0}
    repo = MarketDataSourceRepository(_FakeCandleSource([bad]), window=_WINDOW)
    # Act / Assert
    with pytest.raises(OHLCInvalidError):
        repo.load(None, None, None)


def test_load_raises_time_order_error_when_candles_not_ascending():
    # Arrange: 写像 adapter 側の昇順ガード（§3.3）
    src = _FakeCandleSource([_candle(120), _candle(60)])  # 逆転
    repo = MarketDataSourceRepository(src, window=_WINDOW)
    # Act / Assert
    with pytest.raises(TimeOrderError):
        repo.load(None, None, None)


def test_load_translates_missing_source_io_failure_to_data_error(tmp_path):
    """ISSUE-135 例外契約対称化: 構築時固定の永続実体（CSV パス）が不在なら、生 I/O 例外
    （FileNotFoundError）でなく DataError を送出する（path 系 3 実装と対称・cause を chain）。"""
    from marketdata.csv_source import CsvCandleSource

    missing = tmp_path / "nope.csv"
    repo = MarketDataSourceRepository(CsvCandleSource(missing), window=_WINDOW)
    # Act / Assert: DataError へ翻訳され、元 I/O 例外を __cause__ に連結する
    with pytest.raises(DataError) as excinfo:
        repo.load(missing, None, None)
    assert excinfo.value.__cause__ is not None


def test_load_translates_invalid_candle_data_to_data_error():
    """ISSUE-135 例外契約対称化: fetch 段の不正データ（CandleSource の fail-fast ValueError）も
    path 系の read 例外と対称に DataError へ翻訳する（fetch 境界での翻訳・写像 domain 検証は対象外）。"""

    class _RaisingSource:
        def fetch_candles(self, start, end):
            raise ValueError("非 epoch 値")

    repo = MarketDataSourceRepository(_RaisingSource(), window=_WINDOW)
    with pytest.raises(DataError) as excinfo:
        repo.load(None, None, None)
    assert isinstance(excinfo.value.__cause__, ValueError)


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
    window = (
        datetime(2024, 1, 1, tzinfo=timezone.utc),
        datetime(2024, 1, 2, tzinfo=timezone.utc),
    )

    # Act: 既存経路（golden）と委譲経路（取得窓は構築時固定・source_ref は csv パス）
    expected = CsvOHLCRepository().load(csv, None, None)
    delegated = MarketDataSourceRepository(
        CsvCandleSource(csv), window=window
    ).load(csv, None, None)

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
