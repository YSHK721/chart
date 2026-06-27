"""marketdata.tick_m1 のテスト — 生ティック → M1 OHLC 集計と loader 互換 CSV 出力。

検証対象:
- ticks_to_m1: mid 基準 OHLC・分床・volume=ティック数・時刻順での open/close 確定・tz 正規化・
  空入力・必須列欠落（fail-fast）。
- パス解決: tick_root / m1_csv_path / day_parquet_files（data_dir 注入・実在日のみ）。
- build_m1_from_ticks: parquet 読み→CSV 出力（rollup loader 互換ヘッダ/date 書式）・期間0件で fail-fast。
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

from marketdata import resample, tick_m1


def _ticks(rows: list[tuple[str, float, float]], tz: str | None = "UTC") -> pd.DataFrame:
    ts = pd.to_datetime([r[0] for r in rows])
    if tz is not None:
        ts = ts.tz_localize(tz)
    return pd.DataFrame(
        {
            "timestamp": ts,
            "bidPrice": [r[1] for r in rows],
            "askPrice": [r[2] for r in rows],
        }
    )


def test_ticks_to_m1_ohlc_mid_and_tick_count_volume() -> None:
    # 1 分目に 3 tick、2 分目に 1 tick。mid=(bid+ask)/2。
    df = _ticks(
        [
            ("2025-01-01 00:00:05", 100.0, 102.0),  # mid 101
            ("2025-01-01 00:00:30", 110.0, 110.0),  # mid 110 (high)
            ("2025-01-01 00:00:55", 98.0, 100.0),   # mid 99  (low, close)
            ("2025-01-01 00:01:10", 103.0, 105.0),  # mid 104
        ]
    )
    m1 = tick_m1.ticks_to_m1(df)

    assert list(m1.index) == [
        pd.Timestamp("2025-01-01 00:00:00"),
        pd.Timestamp("2025-01-01 00:01:00"),
    ]
    assert m1.index.name == "date"
    first = m1.iloc[0]
    assert (first["open"], first["high"], first["low"], first["close"]) == (101.0, 110.0, 99.0, 99.0)
    assert first["volume"] == 3.0  # ティック数（出来高ではない）。
    second = m1.iloc[1]
    assert (second["open"], second["close"], second["volume"]) == (104.0, 104.0, 1.0)


def test_ticks_to_m1_open_close_follow_time_order_when_unsorted() -> None:
    # 入力が時刻逆順でも open=最早 tick・close=最遅 tick になる（集計前に安定ソート）。
    df = _ticks(
        [
            ("2025-01-01 00:00:55", 200.0, 200.0),  # 最遅 → close
            ("2025-01-01 00:00:05", 100.0, 100.0),  # 最早 → open
        ]
    )
    m1 = tick_m1.ticks_to_m1(df)
    row = m1.iloc[0]
    assert row["open"] == 100.0
    assert row["close"] == 200.0


def test_ticks_to_m1_tz_aware_floored_to_utc_minute() -> None:
    # tz-aware UTC は naive UTC の分床へ正規化される。
    naive = tick_m1.ticks_to_m1(_ticks([("2025-01-01 23:59:30", 10.0, 12.0)], tz=None))
    aware = tick_m1.ticks_to_m1(_ticks([("2025-01-01 23:59:30", 10.0, 12.0)], tz="UTC"))
    assert list(naive.index) == list(aware.index) == [pd.Timestamp("2025-01-01 23:59:00")]
    assert getattr(aware.index, "tz", None) is None


def test_ticks_to_m1_empty_returns_empty_ohlcv() -> None:
    out = tick_m1.ticks_to_m1(_ticks([]))
    assert out.empty
    assert list(out.columns) == ["open", "high", "low", "close", "volume"]
    assert out.index.name == "date"


def test_ticks_to_m1_missing_column_raises() -> None:
    bad = pd.DataFrame({"timestamp": pd.to_datetime(["2025-01-01"]), "bidPrice": [1.0]})
    with pytest.raises(ValueError, match="askPrice"):
        tick_m1.ticks_to_m1(bad)


def test_path_helpers_respect_injected_data_dir(tmp_path: Path) -> None:
    assert tick_m1.tick_root(tmp_path) == tmp_path / "ticks"
    assert tick_m1.m1_csv_path(ref="jp225_tick", data_dir=tmp_path) == tmp_path / "jp225_tick_m1.csv"


def test_day_parquet_files_lists_only_existing_days(tmp_path: Path) -> None:
    root = tmp_path / "ticks" / "2025" / "01" / "02"
    root.mkdir(parents=True)
    pq = root / "JP225_ticks.parquet"
    _ticks([("2025-01-02 00:00:01", 1.0, 1.0)]).to_parquet(pq)

    found = tick_m1.day_parquet_files("2025-01-01", "2025-01-03", data_dir=tmp_path)
    assert found == [pq]  # 実在する 01/02 のみ（01/01・01/03 は欠損でスキップ）。


def test_build_m1_from_ticks_writes_loader_compatible_csv(tmp_path: Path) -> None:
    day = tmp_path / "ticks" / "2025" / "01" / "02"
    day.mkdir(parents=True)
    _ticks(
        [
            ("2025-01-02 09:00:10", 100.0, 102.0),
            ("2025-01-02 09:00:50", 104.0, 106.0),
            ("2025-01-02 09:01:10", 108.0, 110.0),
        ]
    ).to_parquet(day / "JP225_ticks.parquet")

    out = tick_m1.build_m1_from_ticks("2025-01-02", "2025-01-02", ref="jp225_tick", data_dir=tmp_path)
    assert out == tmp_path / "jp225_tick_m1.csv"

    # rollup loader 互換: ヘッダ date,open,high,low,close,volume・date は "%Y-%m-%d %H:%M:%S"。
    raw = out.read_text(encoding="utf-8").splitlines()
    assert raw[0] == "date,open,high,low,close,volume"
    assert raw[1].startswith("2025-01-02 09:00:00,")

    # marketdata.resample が読める（date 列を index 化して 5m へ集計できる）。
    df = pd.read_csv(out, parse_dates=["date"]).set_index("date")
    up = resample.resample_ohlc(df, resample.TIMEFRAME_RULES["5m"])
    assert up.iloc[0]["open"] == 101.0  # 最初の tick の mid。
    assert up.iloc[0]["volume"] == 3.0  # 5m に 3 tick 合算。


def test_build_m1_from_ticks_no_files_raises(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError, match="ティック parquet が見つかりません"):
        tick_m1.build_m1_from_ticks("2025-01-01", "2025-01-01", data_dir=tmp_path)


def test_csv_format_is_single_truth_with_rollup_loader() -> None:
    # 回帰: rollup loader 互換は _HEADER/_DATE_FMT のパリティに依存する。片側変更を検出する。
    from marketdata import rollup

    assert tick_m1._HEADER == rollup._HEADER
    assert tick_m1._DATE_FMT == rollup._DATE_FMT


@pytest.mark.parametrize("bad_ref", ["../evil", "a/b", "jp225 tick", "..", ""])
def test_build_m1_rejects_unsafe_ref(tmp_path: Path, bad_ref: str) -> None:
    # データ保全: パス区切り・".." 等を含む ref は DATA_DIR 外書込／既存破壊を招くため拒否。
    day = tmp_path / "ticks" / "2025" / "01" / "02"
    day.mkdir(parents=True)
    _ticks([("2025-01-02 00:00:01", 1.0, 1.0)]).to_parquet(day / "JP225_ticks.parquet")
    with pytest.raises(ValueError, match="ref"):
        tick_m1.build_m1_from_ticks("2025-01-02", "2025-01-02", ref=bad_ref, data_dir=tmp_path)


def _put_day(data_dir: Path, ymd: tuple[int, int, int], rows: list[tuple[str, float, float]]) -> None:
    d = data_dir / "ticks" / f"{ymd[0]:04d}" / f"{ymd[1]:02d}" / f"{ymd[2]:02d}"
    d.mkdir(parents=True)
    _ticks(rows).to_parquet(d / "JP225_ticks.parquet")


def test_append_m1_falls_back_to_full_when_no_existing(tmp_path: Path) -> None:
    _put_day(tmp_path, (2025, 1, 2), [("2025-01-02 09:00:10", 100.0, 100.0)])
    out = tick_m1.append_m1_from_ticks("2025-01-01", "2025-01-02", data_dir=tmp_path)
    assert out.is_file()
    assert len(pd.read_csv(out)) == 1  # 初回は全構築フォールバック。


def test_append_m1_appends_only_new_days_and_equals_full(tmp_path: Path) -> None:
    # day2 で初回フル → day3 追加で増分追記 → 全構築（全日一括）と完全一致。
    _put_day(tmp_path, (2025, 1, 2), [
        ("2025-01-02 09:00:10", 100.0, 100.0), ("2025-01-02 09:01:10", 102.0, 102.0),
    ])
    tick_m1.append_m1_from_ticks("2025-01-01", "2025-01-02", data_dir=tmp_path)  # 初回=フル
    _put_day(tmp_path, (2025, 1, 3), [("2025-01-03 09:00:10", 200.0, 200.0)])
    out = tick_m1.append_m1_from_ticks("2025-01-01", "2025-01-03", data_dir=tmp_path)  # 増分

    got = pd.read_csv(out, parse_dates=["date"]).set_index("date")
    expected = tick_m1.ticks_to_m1(_ticks([
        ("2025-01-02 09:00:10", 100.0, 100.0), ("2025-01-02 09:01:10", 102.0, 102.0),
        ("2025-01-03 09:00:10", 200.0, 200.0),
    ]))
    pd.testing.assert_frame_equal(got, expected, check_names=True)


def test_last_m1_date_variants(tmp_path: Path) -> None:
    p = tmp_path / "jp225_tick_m1.csv"
    assert tick_m1.last_m1_date(p) is None  # 不在。
    p.write_text("date,open,high,low,close,volume\n", encoding="utf-8")
    assert tick_m1.last_m1_date(p) is None  # ヘッダのみ。
    p.write_text(
        "date,open,high,low,close,volume\n2025-01-02 09:00:00,1,2,0,1,3\n", encoding="utf-8"
    )
    assert tick_m1.last_m1_date(p) == pd.Timestamp("2025-01-02 09:00:00")  # 単一行。


def test_append_m1_self_heals_torn_last_line(tmp_path: Path) -> None:
    # 非原子追記のクラッシュを模した「末尾 torn 行（列欠落）」を全構築フォールバックで自己修復する。
    _put_day(tmp_path, (2025, 1, 2), [
        ("2025-01-02 09:00:10", 100.0, 100.0), ("2025-01-02 09:01:10", 102.0, 102.0),
    ])
    out = tick_m1.append_m1_from_ticks("2025-01-01", "2025-01-02", data_dir=tmp_path)  # 初回=フル
    with open(out, "a", encoding="utf-8") as fh:
        fh.write("2025-01-02 09:02:00,108.0\n")  # torn: open のみ・他列欠落。
    assert not tick_m1._is_healthy_m1_row(tick_m1._read_last_m1_row(out))  # 不健全を検出。

    out2 = tick_m1.append_m1_from_ticks("2025-01-01", "2025-01-02", data_dir=tmp_path)  # 自己修復
    df = pd.read_csv(out2)
    assert len(df) == 2  # torn 行は除去され完成済み 2 行へ復元。
    assert df[["open", "high", "low", "close", "volume"]].notna().all().all()


def test_append_m1_noop_when_no_new_days(tmp_path: Path) -> None:
    _put_day(tmp_path, (2025, 1, 2), [("2025-01-02 09:00:10", 100.0, 100.0)])
    tick_m1.append_m1_from_ticks("2025-01-01", "2025-01-02", data_dir=tmp_path)
    out = tick_m1.m1_csv_path(data_dir=tmp_path)
    before = out.read_text(encoding="utf-8")
    # 同範囲を再実行しても新しい日が無いので不変（再追記しない）。
    tick_m1.append_m1_from_ticks("2025-01-01", "2025-01-02", data_dir=tmp_path)
    assert out.read_text(encoding="utf-8") == before


def test_build_m1_per_day_concat_matches_whole_aggregation(tmp_path: Path) -> None:
    # メモリ有界化（日別集約）の数値同一性: 2 日分を跨いでも全件一括集計と一致する。
    rows_d1 = [("2025-01-02 09:00:10", 100.0, 100.0), ("2025-01-02 09:00:50", 102.0, 102.0)]
    rows_d2 = [("2025-01-03 09:00:10", 200.0, 200.0)]
    for ymd, rows in (("02", rows_d1), ("03", rows_d2)):
        day = tmp_path / "ticks" / "2025" / "01" / ymd
        day.mkdir(parents=True)
        _ticks(rows).to_parquet(day / "JP225_ticks.parquet")

    out = tick_m1.build_m1_from_ticks("2025-01-02", "2025-01-03", data_dir=tmp_path)
    got = pd.read_csv(out, parse_dates=["date"]).set_index("date")
    expected = tick_m1.ticks_to_m1(_ticks(rows_d1 + rows_d2))
    pd.testing.assert_frame_equal(got, expected, check_names=True)
