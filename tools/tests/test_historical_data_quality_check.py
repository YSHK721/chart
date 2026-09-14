from __future__ import annotations

import json

import pandas as pd
import pytest

from tools.historical_data_quality_check import (
    check_historical_csv,
    compare_close_series,
    compare_csv_to_dukascopy,
    compare_csv_to_dukascopy_by_hour,
)


REQUIRED_COLUMNS = ["日付", "時間", "始値", "高値", "安値", "終値", "出来高"]


def _write_csv(path, rows):
    df = pd.DataFrame(rows, columns=REQUIRED_COLUMNS)
    df.to_csv(path, index=False, encoding="utf-8")


def test_detects_ohlc_and_duplicate_issues(tmp_path):
    csv_path = tmp_path / "bad.csv"
    rows = [
        {"日付": "2024/12/30", "時間": "17:00", "始値": 100, "高値": 110, "安値": 95, "終値": 105, "出来高": 100},
        {"日付": "2024/12/30", "時間": "17:00", "始値": 103, "高値": 108, "安値": 98, "終値": 104, "出来高": 90},
        {"日付": "2024/12/30", "時間": "17:02", "始値": 105, "高値": 100, "安値": 99, "終値": 102, "出来高": 80},
        {"日付": "2024/12/30", "時間": "17:03", "始値": 101, "高値": 103, "安値": 101, "終値": 102, "出来高": 60},
    ]
    _write_csv(csv_path, rows)

    report = check_historical_csv(csv_path)

    assert report["valid"] is False
    assert "duplicate_timestamp" in report["issues"]
    assert "ohlc_violation" in report["issues"]


def test_missing_required_columns_fails(tmp_path):
    csv_path = tmp_path / "missing.csv"
    pd.DataFrame([{"日付": "2024/12/30", "時間": "17:00", "終値": 100}]).to_csv(
        csv_path, index=False, encoding="utf-8"
    )

    with pytest.raises(ValueError, match="required columns"):
        check_historical_csv(csv_path)


def test_compare_close_series_is_read_only_and_reports_correlation(tmp_path):
    left = tmp_path / "left.csv"
    right = tmp_path / "right.csv"
    rows1 = [
        {"日付": "2024/12/30", "時間": "17:00", "始値": 100, "高値": 105, "安値": 99, "終値": 101, "出来高": 10},
        {"日付": "2024/12/30", "時間": "17:01", "始値": 101, "高値": 106, "安値": 100, "終値": 104, "出来高": 12},
    ]
    rows2 = [
        {"日付": "2024/12/30", "時間": "17:00", "始値": 100, "高値": 105, "安値": 99, "終値": 101, "出来高": 10},
        {"日付": "2024/12/30", "時間": "17:01", "始値": 101, "高値": 106, "安値": 100, "終値": 104, "出来高": 12},
    ]
    _write_csv(left, rows1)
    _write_csv(right, rows2)

    before_left = left.read_text(encoding="utf-8")
    before_right = right.read_text(encoding="utf-8")

    report = compare_close_series(left, right)

    assert report["correlation"] == pytest.approx(1.0)
    assert report["matched_rows"] == 2
    assert left.read_text(encoding="utf-8") == before_left
    assert right.read_text(encoding="utf-8") == before_right
    assert json.loads(report["summary_json"]) == report["summary"]


def test_compare_csv_to_dukascopy_aligns_timezone_and_reads_only_local_csv(monkeypatch, tmp_path):
    csv_path = tmp_path / "local.csv"
    _write_csv(csv_path, [
        {"日付": "2024/12/30", "時間": "17:00", "始値": 100, "高値": 105, "安値": 99, "終値": 101, "出来高": 10},
        {"日付": "2024/12/30", "時間": "17:01", "始値": 101, "高値": 106, "安値": 100, "終値": 104, "出来高": 12},
    ])
    before = csv_path.read_text(encoding="utf-8")

    def fake_fetch(instrument, interval, offer_side, start, end):
        idx = pd.DatetimeIndex([
            pd.Timestamp("2024-12-30 08:00:00", tz="UTC"),
            pd.Timestamp("2024-12-30 08:01:00", tz="UTC"),
        ])
        return pd.DataFrame(
            {"open": [100.0, 101.0], "high": [105.0, 106.0], "low": [99.0, 100.0], "close": [101.0, 104.0], "volume": [10, 12]},
            index=idx,
        )

    monkeypatch.setattr("tools.historical_data_quality_check.dukascopy_python.fetch", fake_fetch)

    report = compare_csv_to_dukascopy(csv_path)

    assert report["correlation"] == pytest.approx(1.0)
    assert report["matched_rows"] == 2
    assert csv_path.read_text(encoding="utf-8") == before


def test_compare_csv_to_dukascopy_by_hour_returns_per_hour_correlation(monkeypatch, tmp_path):
    csv_path = tmp_path / "hourly.csv"
    rows = [
        {"日付": "2024/12/30", "時間": "17:00", "始値": 100, "高値": 101, "安値": 99, "終値": 100.5, "出来高": 10},
        {"日付": "2024/12/30", "時間": "17:01", "始値": 100.5, "高値": 101.5, "安値": 100, "終値": 101.0, "出来高": 11},
        {"日付": "2024/12/30", "時間": "17:02", "始値": 101.0, "高値": 102.0, "安値": 100.5, "終値": 101.5, "出来高": 12},
        {"日付": "2024/12/30", "時間": "18:00", "始値": 200, "高値": 201, "安値": 199, "終値": 200.5, "出来高": 20},
        {"日付": "2024/12/30", "時間": "18:01", "始値": 200.5, "高値": 201.5, "安値": 200, "終値": 201.0, "出来高": 21},
        {"日付": "2024/12/30", "時間": "18:02", "始値": 201.0, "高値": 202.0, "安値": 200.5, "終値": 201.5, "出来高": 22},
    ]
    _write_csv(csv_path, rows)
    before = csv_path.read_text(encoding="utf-8")

    def fake_fetch(instrument, interval, offer_side, start, end):
        idx = pd.DatetimeIndex([
            pd.Timestamp("2024-12-30 08:00:00", tz="UTC"),
            pd.Timestamp("2024-12-30 08:01:00", tz="UTC"),
            pd.Timestamp("2024-12-30 08:02:00", tz="UTC"),
            pd.Timestamp("2024-12-30 09:00:00", tz="UTC"),
            pd.Timestamp("2024-12-30 09:01:00", tz="UTC"),
            pd.Timestamp("2024-12-30 09:02:00", tz="UTC"),
        ])
        return pd.DataFrame(
            {
                "open": [100.0, 101.0, 101.5, 200.0, 201.0, 201.5],
                "high": [101.0, 102.0, 102.5, 201.0, 202.0, 202.5],
                "low": [99.0, 100.0, 101.0, 199.0, 200.0, 201.0],
                "close": [100.5, 101.0, 101.5, 200.5, 201.0, 201.5],
                "volume": [10, 11, 12, 20, 21, 22],
            },
            index=idx,
        )

    monkeypatch.setattr("tools.historical_data_quality_check.dukascopy_python.fetch", fake_fetch)

    report = compare_csv_to_dukascopy_by_hour(csv_path)

    assert report["hours"][0]["hour"] == "17"
    assert report["hours"][0]["correlation"] == pytest.approx(1.0)
    assert report["hours"][1]["hour"] == "18"
    assert report["hours"][1]["correlation"] == pytest.approx(1.0)
    assert csv_path.read_text(encoding="utf-8") == before
