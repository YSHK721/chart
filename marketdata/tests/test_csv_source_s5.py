"""TDD 記録 — marketdata S5（CsvCandleSource: CSV→Candle 取得 adapter・委譲先）。

設計正典: MARKETDATA_TIMESERIES_BOUNDARY_DESIGN.md §1.1（csv_source 新設）/ §3.3（委譲先）/
§2.1（Candle volume）/ §6 S5 行 / §10.1 C-2（source_ref=(start,end) 半開）。

確定仕様（YAGNI: 実需分のみ＝comma 形式 CSV→Candle）:
  1. ``marketdata.csv_source.CsvCandleSource`` が ``CandleSource`` プロトコルを満たす
     （``fetch_candles(start, end) -> list[Candle]``）。
  2. comma 形式 CSV（time,open,high,low,close,volume[,spread]）を読み、time 昇順の Candle list を返す。
     time は UNIX 秒 int（Candle 契約・§2.1）。volume は列があれば float、無ければ 0.0（§2.3）。
  3. ``[start, end)``（半開・C-2）で期間フィルタする。範囲外 candle は除外する。

回帰観点（memory bugfix-pair-with-regression-test）:
  - 半開区間の終端（end）が排他であること（end と等しい time の足を含めない）。
  - volume 列欠落 CSV で KeyError で落ちず 0.0 を補う。
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from marketdata.csv_source import CsvCandleSource
from marketdata.port import CandleSource


def _epoch(y, mo, d, h=0, mi=0) -> int:
    return int(datetime(y, mo, d, h, mi, tzinfo=timezone.utc).timestamp())


def _write_csv(path, rows, header="time,open,high,low,close,volume"):
    lines = [header] + [",".join(str(c) for c in r) for r in rows]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def _valid_rows():
    # time(UNIX秒 int), open, high, low, close, volume（昇順・OHLC 整合）
    t0 = _epoch(2024, 1, 1, 0, 0)
    return [
        (t0 + 0, 1.10, 1.20, 1.05, 1.15, 100.0),
        (t0 + 60, 1.15, 1.25, 1.10, 1.20, 110.0),
        (t0 + 120, 1.20, 1.30, 1.18, 1.28, 120.0),
    ]


def test_csv_candle_source_satisfies_candle_source_protocol(tmp_path):
    # Arrange / Act
    src = CsvCandleSource(_write_csv(tmp_path / "ohlc.csv", _valid_rows()))
    # Assert: runtime_checkable Protocol の構造的適合
    assert isinstance(src, CandleSource)


def test_fetch_candles_returns_unix_int_time_and_float_ohlcv(tmp_path):
    # Arrange
    src = CsvCandleSource(_write_csv(tmp_path / "ohlc.csv", _valid_rows()))
    # Act: 全期間を覆う窓
    candles = src.fetch_candles(
        datetime(2024, 1, 1, tzinfo=timezone.utc),
        datetime(2024, 1, 2, tzinfo=timezone.utc),
    )
    # Assert
    assert len(candles) == 3
    c0 = candles[0]
    assert c0["time"] == _epoch(2024, 1, 1, 0, 0)
    assert isinstance(c0["time"], int)
    assert (c0["open"], c0["high"], c0["low"], c0["close"]) == (1.10, 1.20, 1.05, 1.15)
    assert c0["volume"] == 100.0
    assert isinstance(c0["volume"], float)


def test_fetch_candles_returns_time_ascending(tmp_path):
    # Arrange
    src = CsvCandleSource(_write_csv(tmp_path / "ohlc.csv", _valid_rows()))
    # Act
    candles = src.fetch_candles(
        datetime(2024, 1, 1, tzinfo=timezone.utc),
        datetime(2024, 1, 2, tzinfo=timezone.utc),
    )
    # Assert
    times = [c["time"] for c in candles]
    assert times == sorted(times)


def test_fetch_candles_half_open_excludes_end(tmp_path):
    # Arrange: 3 本（t0/t0+60/t0+120）。end を 2 本目の time に置く＝[start, end) で 1 本のみ。
    src = CsvCandleSource(_write_csv(tmp_path / "ohlc.csv", _valid_rows()))
    t0 = _epoch(2024, 1, 1, 0, 0)
    # Act: [t0, t0+60) — 終端 t0+60 は排他（C-2 半開）。
    candles = src.fetch_candles(
        datetime.fromtimestamp(t0, tz=timezone.utc),
        datetime.fromtimestamp(t0 + 60, tz=timezone.utc),
    )
    # Assert: 1 本目のみ（終端排他の回帰の壁）
    assert [c["time"] for c in candles] == [t0]


def test_fetch_candles_filters_before_start(tmp_path):
    # Arrange
    src = CsvCandleSource(_write_csv(tmp_path / "ohlc.csv", _valid_rows()))
    t0 = _epoch(2024, 1, 1, 0, 0)
    # Act: [t0+60, end) — 1 本目を除外。
    candles = src.fetch_candles(
        datetime.fromtimestamp(t0 + 60, tz=timezone.utc),
        datetime(2024, 1, 2, tzinfo=timezone.utc),
    )
    # Assert: t0 は start より前で除外（包含開始）
    assert [c["time"] for c in candles] == [t0 + 60, t0 + 120]


def test_fetch_candles_empty_window_returns_empty_list(tmp_path):
    # Arrange
    src = CsvCandleSource(_write_csv(tmp_path / "ohlc.csv", _valid_rows()))
    # Act: データの存在しない期間
    candles = src.fetch_candles(
        datetime(2030, 1, 1, tzinfo=timezone.utc),
        datetime(2030, 1, 2, tzinfo=timezone.utc),
    )
    # Assert: 空 list（例外でない・CandleSource 契約）
    assert candles == []


def test_fetch_candles_raises_clear_error_on_non_epoch_time(tmp_path):
    # Arrange: Candle 契約（§2.1: time=UNIX 秒 int）に反する ISO 文字列 time。委譲経路へ
    # ISO 文字列 CSV を誤って流すと黙って report.json が乖離するのを防ぐ＝fail-fast で
    # 「time は UNIX 秒 int であるべき」と明示する（暗黙フォールバック禁止・dukascopy 同方針）。
    rows = ["2024-01-01T00:00:00,1.10,1.20,1.05,1.15,100.0"]
    csv = tmp_path / "iso.csv"
    csv.write_text(
        "time,open,high,low,close,volume\n" + "\n".join(rows) + "\n", encoding="utf-8"
    )
    src = CsvCandleSource(csv)
    # Act / Assert: 契約違反を ValueError + 明示メッセージ（"UNIX" 文言）で通知する。
    with pytest.raises(ValueError, match="UNIX"):
        src.fetch_candles(
            datetime(2024, 1, 1, tzinfo=timezone.utc),
            datetime(2024, 1, 2, tzinfo=timezone.utc),
        )


def test_fetch_candles_defaults_volume_to_zero_when_column_absent(tmp_path):
    # Arrange: volume 列なし CSV（回帰の壁・KeyError 禁止）
    t0 = _epoch(2024, 1, 1, 0, 0)
    rows = [(t0, 1.10, 1.20, 1.05, 1.15)]
    src = CsvCandleSource(
        _write_csv(tmp_path / "novol.csv", rows, header="time,open,high,low,close")
    )
    # Act
    candles = src.fetch_candles(
        datetime(2024, 1, 1, tzinfo=timezone.utc),
        datetime(2024, 1, 2, tzinfo=timezone.utc),
    )
    # Assert: 0.0 を補う（§2.3）
    assert candles[0]["volume"] == 0.0
