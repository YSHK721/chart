"""raw(Dukascopy)→canonical 変換と ingest 経路のテスト（ネットワーク非依存）。

設計（依頼確定）:
    - to_canonical_ticks(raw_df) -> DataFrame:
        bid=bidPrice / ask=askPrice / last=(bid+ask)/2（quote feed の last=mid 規約）
        / volume=bidVolume+askVolume / timestamp は tz-aware UTC を保持。
        出力は tick-store の TICK_COLUMNS 準拠。入力列欠損は明確なエラー。
    - ingest_raw_parquet(raw_path, store_root, symbol, mode="overwrite") -> 結果:
        raw parquet → to_canonical_ticks → ParquetTickRepository.write_ticks。
        ParquetTickRepository.load_ticks で round-trip 可能。

テストは全て小構成 tmp データ。大容量実データ(marketdata/ticks/*.parquet)は読まない。
ライブ Dukascopy fetch は叩かない（raw 列を明示構成した小 DataFrame を使う）。
"""
from __future__ import annotations

from datetime import datetime, timezone

import pandas as pd


# =========================================================================
# Section 1: to_canonical_ticks（raw→canonical 変換）
# =========================================================================

def _raw_3rows():
    """Dukascopy ネイティブ列を明示構成した 3 行の raw frame（tz-aware UTC）。"""
    return pd.DataFrame(
        {
            "timestamp": pd.to_datetime(
                [
                    datetime(2025, 1, 2, 0, 0, 0, tzinfo=timezone.utc),
                    datetime(2025, 1, 2, 0, 0, 1, tzinfo=timezone.utc),
                    datetime(2025, 1, 2, 0, 0, 2, tzinfo=timezone.utc),
                ]
            ),
            "bidPrice": [39000.0, 39010.0, 39020.0],
            "askPrice": [39005.0, 39015.0, 39025.0],
            "bidVolume": [1.0, 2.0, 3.0],
            "askVolume": [4.0, 5.0, 6.0],
        }
    )


def test_to_canonical_ticks_maps_columns_to_tick_columns():
    from simulator.adapter.repository._tick_frame import TICK_COLUMNS
    from simulator.tools.ingest_ticks import to_canonical_ticks

    raw = _raw_3rows()

    out = to_canonical_ticks(raw)

    # 出力列が tick-store の TICK_COLUMNS 準拠
    assert list(out.columns) == list(TICK_COLUMNS)


def test_to_canonical_ticks_computes_bid_ask_last_mid_and_volume_sum():
    from simulator.tools.ingest_ticks import to_canonical_ticks

    raw = _raw_3rows()

    out = to_canonical_ticks(raw)

    # bid=bidPrice / ask=askPrice
    assert out["bid"].tolist() == [39000.0, 39010.0, 39020.0]
    assert out["ask"].tolist() == [39005.0, 39015.0, 39025.0]
    # last=(bid+ask)/2（quote feed の last=mid 規約）
    assert out["last"].tolist() == [39002.5, 39012.5, 39022.5]
    # volume=bidVolume+askVolume
    assert out["volume"].tolist() == [5.0, 7.0, 9.0]


def test_to_canonical_ticks_normalizes_timestamp_to_naive_utc():
    # 🟡-1: store 契約は naive UTC 固定（synth_ticks 由来・load_ticks docstring 宣言）。
    #   raw が tz-aware UTC でも canonical は tz-naive datetime64 へ正規化する。
    #   全 UTC のため tz_localize(None) は情報損失なし（値は UTC 相当のまま）。
    from simulator.tools.ingest_ticks import to_canonical_ticks

    raw = _raw_3rows()  # tz-aware UTC 入力

    out = to_canonical_ticks(raw)

    # timestamp は tz-naive（dt.tz が None）であること。
    assert out["timestamp"].dt.tz is None
    # かつ UTC 相当の値が一致すること（tz を剥がしただけ＝値は不変）。
    expected_naive = raw["timestamp"].dt.tz_convert("UTC").dt.tz_localize(None)
    assert out["timestamp"].tolist() == expected_naive.tolist()


def test_to_canonical_ticks_raises_missing_bar_error_when_raw_column_absent():
    import pytest

    from simulator.domain.exceptions import MissingBarError
    from simulator.tools.ingest_ticks import to_canonical_ticks

    raw = _raw_3rows().drop(columns=["bidPrice"])  # raw 必須列欠損

    # 入力列欠損は明確なドメインエラー（生 KeyError ではない）。欠損列名を含む。
    with pytest.raises(MissingBarError) as excinfo:
        to_canonical_ticks(raw)
    assert "bidPrice" in str(excinfo.value)


# =========================================================================
# Section 2: ingest_raw_parquet（raw parquet → 変換 → write_ticks → round-trip）
# =========================================================================

def _raw_2days():
    """2025-01-02..03 にまたがる小 raw frame（各日 2 tick・tz-aware UTC）。"""
    return pd.DataFrame(
        {
            "timestamp": pd.to_datetime(
                [
                    datetime(2025, 1, 2, 9, 0, 0, tzinfo=timezone.utc),
                    datetime(2025, 1, 2, 9, 0, 1, tzinfo=timezone.utc),
                    datetime(2025, 1, 3, 9, 0, 0, tzinfo=timezone.utc),
                    datetime(2025, 1, 3, 9, 0, 1, tzinfo=timezone.utc),
                ]
            ),
            "bidPrice": [39000.0, 39001.0, 39100.0, 39101.0],
            "askPrice": [39005.0, 39006.0, 39105.0, 39106.0],
            "bidVolume": [1.0, 1.0, 1.0, 1.0],
            "askVolume": [1.0, 1.0, 1.0, 1.0],
        }
    )


def test_ingest_raw_parquet_round_trips_written_rows_via_load_ticks(tmp_path):
    from simulator.adapter.repository.tick_parquet import ParquetTickRepository
    from simulator.tools.ingest_ticks import ingest_raw_parquet

    raw = _raw_2days()
    raw_path = tmp_path / "JP225_raw.parquet"
    raw.to_parquet(raw_path, index=False)
    store_root = tmp_path / "store"

    result = ingest_raw_parquet(raw_path, store_root, "JP225", mode="overwrite")

    # 結果は書込日数・行数を報告する（TickWriteResult 準拠）。
    assert result.rows_written == len(raw)
    assert result.days_written == 2

    # round-trip: 書込行数 == [2025-01-02, 2025-01-04) で読み出した行数。
    # 🟡-1 で canonical は naive UTC へ正規化されたため、load_ticks の bounds も
    # naive UTC で渡す（store 契約＝naive UTC 固定に統一。tz-aware bounds は
    # naive store との比較で DataError になる：tick_parquet.py load_ticks 契約）。
    repo = ParquetTickRepository(root=store_root)
    out = repo.load_ticks(
        "JP225",
        datetime(2025, 1, 2),
        datetime(2025, 1, 4),
    )
    assert len(out) == len(raw)
    # 日分割が生成されている（2 日分の part.parquet）
    parts = list((store_root / "JP225").rglob("part.parquet"))
    assert len(parts) == 2

    # 🟡-2: 行数一致だけではトートロジー（列取り違えを検出できない）。round-trip 結果を
    #   raw 列から独立に導出した期待値と照合する（to_canonical_ticks 出力との比較は同一
    #   実装で両辺が相殺しトートロジーになるため使わない）。raw は bidPrice≠askPrice 構成
    #   なので bid/ask 取り違えを検出可能。timestamp 昇順で raw と整列して値照合する。
    out_sorted = out.sort_values("timestamp").reset_index(drop=True)
    raw_sorted = raw.sort_values("timestamp").reset_index(drop=True)

    # bid=bidPrice / ask=askPrice（取り違えを検出する非対称照合）。
    assert out_sorted["bid"].tolist() == raw_sorted["bidPrice"].tolist()
    assert out_sorted["ask"].tolist() == raw_sorted["askPrice"].tolist()
    # last=(bid+ask)/2 / volume=bidVolume+askVolume。
    expected_last = ((raw_sorted["bidPrice"] + raw_sorted["askPrice"]) / 2.0).tolist()
    expected_volume = (raw_sorted["bidVolume"] + raw_sorted["askVolume"]).tolist()
    assert out_sorted["last"].tolist() == expected_last
    assert out_sorted["volume"].tolist() == expected_volume
    # timestamp は naive UTC 値（store 契約）で raw の UTC 相当と一致する。
    expected_ts = raw_sorted["timestamp"].dt.tz_convert("UTC").dt.tz_localize(None)
    assert out_sorted["timestamp"].tolist() == expected_ts.tolist()
