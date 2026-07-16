"""CandleSource 契約の対称性ガード（ISSUE-098 🟡-3・🟡-4）。

`marketdata/port.py` の :class:`CandleSource` Protocol が両実装（CSV / Dukascopy）に対して
課す契約を明文化し、両実装が同一契約に一致することを固定する回帰テスト。

固定する契約（port.py docstring と一致）:
  - 🟡-3 一意性: 返す candles は ``time`` **厳密昇順・一意**。同一 ``time`` は**後勝ち**で
    一意化する（重複バーの二重計上・消費側の index 重複／TimeOrderError を防ぐ）。
    Dukascopy は ``_to_candles`` が従来より後勝ち一意化（`dukascopy_source.py`）。CSV も
    本契約へ一致させる（従来は append+sort のみで重複残留＝ISSUE-098 🟡-3 の非対称）。
  - 🟡-4 例外: ``time`` を UNIX 秒 int として解釈できない不正データは ``ValueError`` を
    fail-fast 送出する。範囲内にデータが無い場合（データ不在）は空 list を返し例外を送出
    しない（データ不在は誤りではない）。両実装とも本契約に一致する。

実測根拠（挙動保存の裏付け）: 本番/テストで使う実 OHLC CSV は全ファイル time 一意（重複 0）
であり、CSV 後勝ち一意化は実データ上 no-op（出力 byte 不変）。重複 time は潜在的差分のみ。
"""

from __future__ import annotations

from datetime import datetime, timezone

import pandas as pd
import pytest

from marketdata.csv_source import CsvCandleSource
from marketdata.dukascopy_source import DukascopyCandleSource, _to_candles
from marketdata.port import Candle, CandleSource


def _epoch(y, mo, d, h=0, mi=0) -> int:
    return int(datetime(y, mo, d, h, mi, tzinfo=timezone.utc).timestamp())


def _write_csv(path, rows, header="time,open,high,low,close,volume"):
    lines = [header] + [",".join(str(c) for c in r) for r in rows]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def _dukascopy_df(rows):
    """実ライブラリ戻り相当（UTC DatetimeIndex + OHLCV 列）の DataFrame を組む。

    rows: ``(epoch_seconds, open, high, low, close, volume)`` の列。
    """
    idx = pd.to_datetime([r[0] for r in rows], unit="s", utc=True)
    return pd.DataFrame(
        {
            "open": [r[1] for r in rows],
            "high": [r[2] for r in rows],
            "low": [r[3] for r in rows],
            "close": [r[4] for r in rows],
            "volume": [r[5] for r in rows],
        },
        index=idx,
    )


# ---------------------------------------------------------------------------
# 🟡-3 一意性（重複 time 後勝ち一意化）— 両実装の対称性
# ---------------------------------------------------------------------------

def test_csv_dedups_duplicate_time_last_wins(tmp_path):
    # Arrange: 同一 time を持つ 2 行（後勝ち＝close=20.0 が採用されるべき）＋別 time 1 行。
    t0 = _epoch(2024, 1, 1)
    rows = [
        (t0, 1.0, 2.0, 0.5, 10.0, 5.0),
        (t0, 2.0, 3.0, 1.0, 20.0, 6.0),   # 同一 time・後勝ち
        (t0 + 60, 3.0, 4.0, 2.0, 30.0, 7.0),
    ]
    src = CsvCandleSource(_write_csv(tmp_path / "dup.csv", rows))
    # Act
    candles = src.fetch_candles(
        datetime.fromtimestamp(t0, tz=timezone.utc),
        datetime.fromtimestamp(t0 + 120, tz=timezone.utc),
    )
    # Assert: 重複 time は 1 本へ一意化・後勝ち（close=20.0）・厳密昇順。
    assert [(c["time"], c["close"]) for c in candles] == [(t0, 20.0), (t0 + 60, 30.0)]


def test_dukascopy_dedups_duplicate_time_last_wins():
    # Arrange: 同一 time を持つ 2 行（後勝ち＝close=20.0）。
    t0 = _epoch(2024, 1, 1)
    df = _dukascopy_df(
        [
            (t0, 1.0, 2.0, 0.5, 10.0, 5.0),
            (t0, 2.0, 3.0, 1.0, 20.0, 6.0),
            (t0 + 60, 3.0, 4.0, 2.0, 30.0, 7.0),
        ]
    )
    # Act
    candles = _to_candles(df)
    # Assert: CSV と同一の後勝ち一意化・厳密昇順。
    assert [(c["time"], c["close"]) for c in candles] == [(t0, 20.0), (t0 + 60, 30.0)]


def test_both_sources_yield_strictly_ascending_unique_time(tmp_path):
    # Arrange: 同一 time 重複を含む同値入力を両実装へ与える。
    t0 = _epoch(2024, 1, 1)
    raw = [
        (t0 + 60, 2.0, 3.0, 1.0, 20.0, 6.0),
        (t0, 1.0, 2.0, 0.5, 10.0, 5.0),
        (t0, 9.0, 9.0, 9.0, 99.0, 9.0),   # 同一 time 重複
    ]
    csv_candles = CsvCandleSource(_write_csv(tmp_path / "u.csv", raw)).fetch_candles(
        datetime.fromtimestamp(t0, tz=timezone.utc),
        datetime.fromtimestamp(t0 + 120, tz=timezone.utc),
    )
    duk_candles = _to_candles(_dukascopy_df(raw))
    # Act
    csv_times = [c["time"] for c in csv_candles]
    duk_times = [c["time"] for c in duk_candles]
    # Assert: 両者とも厳密昇順・一意（隣接重複なし）で一致。
    for times in (csv_times, duk_times):
        assert times == sorted(times)
        assert len(times) == len(set(times))
    assert csv_times == duk_times == [t0, t0 + 60]


# ---------------------------------------------------------------------------
# 🟡-4 例外契約（不正データ＝ValueError / データ不在＝空 list）— 両実装の対称性
# ---------------------------------------------------------------------------

def test_csv_raises_value_error_on_non_epoch_time(tmp_path):
    # Arrange: time 列が ISO 文字列（非 epoch）＝不正データ。
    csv = tmp_path / "iso.csv"
    csv.write_text(
        "time,open,high,low,close,volume\n2024-01-01T00:00:00,1.1,1.2,1.0,1.15,100\n",
        encoding="utf-8",
    )
    # Act / Assert: ValueError を fail-fast（契約 🟡-4）。
    with pytest.raises(ValueError):
        CsvCandleSource(csv).fetch_candles(
            datetime(2024, 1, 1, tzinfo=timezone.utc),
            datetime(2024, 1, 2, tzinfo=timezone.utc),
        )


def test_dukascopy_raises_value_error_on_invalid_time():
    # Arrange: time が NaT（解釈不能）＝不正データ。
    df = pd.DataFrame(
        {"open": [1.0], "high": [1.0], "low": [1.0], "close": [1.0], "volume": [1.0]},
        index=pd.DatetimeIndex([pd.NaT]),
    )
    # Act / Assert: CSV と同一の ValueError 契約（🟡-4 対称）。
    with pytest.raises(ValueError):
        _to_candles(df)


def test_csv_returns_empty_list_on_no_data(tmp_path):
    # Arrange: 範囲外の窓＝データ不在（誤りではない）。
    t0 = _epoch(2024, 1, 1)
    rows = [(t0, 1.0, 2.0, 0.5, 10.0, 5.0)]
    src = CsvCandleSource(_write_csv(tmp_path / "d.csv", rows))
    # Act
    candles = src.fetch_candles(
        datetime(2030, 1, 1, tzinfo=timezone.utc),
        datetime(2030, 1, 2, tzinfo=timezone.utc),
    )
    # Assert: 例外でなく空 list（契約 🟡-4）。
    assert candles == []


def test_dukascopy_returns_empty_list_on_no_data(monkeypatch):
    # Arrange: ライブラリが None / 空 DataFrame を返す＝データ不在。
    import dukascopy_python

    src = DukascopyCandleSource()
    for empty in (None, pd.DataFrame()):
        monkeypatch.setattr(dukascopy_python, "fetch", lambda *a, **k: empty)
        # Act
        candles = src.fetch_candles(
            datetime(2030, 1, 1, tzinfo=timezone.utc),
            datetime(2030, 1, 2, tzinfo=timezone.utc),
        )
        # Assert: CSV と同一の空 list 契約（🟡-4 対称）。
        assert candles == []


def test_both_sources_satisfy_candle_source_protocol(tmp_path):
    # Arrange
    t0 = _epoch(2024, 1, 1)
    src = CsvCandleSource(_write_csv(tmp_path / "p.csv", [(t0, 1.0, 2.0, 0.5, 1.5, 1.0)]))
    # Assert: runtime_checkable Protocol の構造的適合（両具象）。
    assert isinstance(src, CandleSource)
    assert isinstance(DukascopyCandleSource(), CandleSource)
