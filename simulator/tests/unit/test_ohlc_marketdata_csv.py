"""MarketdataCsvOHLCRepository / detect_ohlc_form の単体検定（2026-09-06・全期間データ結線）。

固定する不変条件:
    1. marketdata 形式（`date,open,high,low,close,volume[,up,dn]`）が domain.Bar 列になる
       （time は datetime64・spread は 0 固定＝H-4）。
    2. 形式判定はヘッダ実測（mt5_tab / comma / marketdata / 不明）。
    3. 窓はフレーム段で先に効く（窓外の行から Bar を**作らない**）。
    4. 計算量: Bar 構築数 − 採用 Bar 数 = 0（Test Spy）。構築数は窓の行数で決まり、
       ファイル総行数に依存しない（オーダーの表明・2 窓比較）。
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from simulator.adapter.repository import _ohlc_frame
from simulator.adapter.repository.ohlc_marketdata_csv import (
    MarketdataCsvOHLCRepository,
    detect_ohlc_form,
)
from simulator.domain.exceptions import MissingBarError

#: 5 行の marketdata 形式（任意列 up/dn 付き・実物 `data/marketdata/*.csv` と同形）。
_CSV = """date,open,high,low,close,volume,up,dn
2024-01-08 00:00:00,100.0,101.0,99.0,100.5,10.0,1.0,0.0
2024-01-08 00:01:00,100.5,102.0,100.0,101.0,11.0,2.0,0.0
2024-01-09 00:00:00,101.0,103.0,101.0,102.0,12.0,3.0,0.0
2024-01-10 00:00:00,102.0,104.0,102.0,103.0,13.0,4.0,0.0
2024-01-11 00:00:00,103.0,105.0,103.0,104.0,14.0,5.0,0.0
"""


@pytest.fixture
def csv_path(tmp_path):
    path = tmp_path / "jp225_marketdata.csv"
    path.write_text(_CSV, encoding="utf-8")
    return path


def _utc(y, m, d):
    return datetime(y, m, d, tzinfo=timezone.utc)


# --- 1. 形式の読取 -------------------------------------------------------------


def test_marketdata_rows_become_bars_with_zero_spread(csv_path):
    bars = MarketdataCsvOHLCRepository().load(str(csv_path))
    assert len(bars) == 5
    assert bars[0].open == 100.0 and bars[0].close == 100.5 and bars[0].volume == 10.0
    assert all(bar.spread == 0 for bar in bars), "spread 列が無い形式は 0 固定（H-4）"
    assert str(bars[0].time).startswith("2024-01-08T00:00:00")   # datetime64（精度表記は環境差）


def test_a_missing_required_column_fails_stop(tmp_path):
    path = tmp_path / "broken.csv"
    path.write_text("date,open,high,low,volume\n2024-01-08 00:00:00,1,1,1,1\n")
    with pytest.raises(MissingBarError):
        MarketdataCsvOHLCRepository().load(str(path))


# --- 2. 形式判定（ヘッダ実測）---------------------------------------------------


def test_detect_ohlc_form_measures_the_header(tmp_path, csv_path):
    mt5 = tmp_path / "mt5.csv"
    mt5.write_text("<DATE>\t<TIME>\t<OPEN>\t<HIGH>\t<LOW>\t<CLOSE>\t<TICKVOL>\t<VOL>\t<SPREAD>\n")
    comma = tmp_path / "comma.csv"
    comma.write_text("time,open,high,low,close,volume,spread\n")
    garbage = tmp_path / "garbage.csv"
    garbage.write_text("foo,bar\n")
    assert detect_ohlc_form(str(mt5)) == "mt5_tab"
    assert detect_ohlc_form(str(comma)) == "comma"
    assert detect_ohlc_form(str(csv_path)) == "marketdata"
    assert detect_ohlc_form(str(garbage)) == ""
    assert detect_ohlc_form(str(tmp_path / "no_such.csv")) == ""
    assert detect_ohlc_form(None) == ""   # データ非供給の modelling（Model=3）は data_path=None


# --- 3. 窓のフレーム段適用 ------------------------------------------------------


def test_the_window_filters_rows_before_bars_are_built(csv_path):
    bars = MarketdataCsvOHLCRepository(
        window=(_utc(2024, 1, 9), _utc(2024, 1, 11))
    ).load(str(csv_path))
    assert [str(b.time)[:10] for b in bars] == ["2024-01-09", "2024-01-10"]


# --- 4. 計算量テスト（規約: 発行した構築 − 採用 = 0・ファイル総行数に非依存）------


def _counting_bar(monkeypatch):
    real = _ohlc_frame.Bar
    calls = {"built": 0}

    def counting(**kwargs):
        calls["built"] += 1
        return real(**kwargs)

    monkeypatch.setattr(_ohlc_frame, "Bar", counting)
    return calls


def test_no_bar_is_built_outside_the_window(monkeypatch, csv_path):
    calls = _counting_bar(monkeypatch)
    bars = MarketdataCsvOHLCRepository(
        window=(_utc(2024, 1, 9), _utc(2024, 1, 11))
    ).load(str(csv_path))
    # 発行した構築 − 採用した Bar = 0（窓外の行から作って捨てていない）
    assert calls["built"] - len(bars) == 0
    assert len(bars) == 2  # 空振り防止（ファイルは 5 行）


def test_builds_scale_with_the_window_not_the_file(monkeypatch, csv_path):
    # 2 点で固定: 同じ 5 行のファイルに対し、窓を広げた分だけしか構築が増えない。
    counts = []
    for end_day in (9, 11):
        calls = _counting_bar(monkeypatch)
        bars = MarketdataCsvOHLCRepository(
            window=(_utc(2024, 1, 8), _utc(2024, 1, end_day))
        ).load(str(csv_path))
        counts.append((calls["built"], len(bars)))
    assert counts[0] == (2, 2)   # 1/8 の 2 行のみ
    assert counts[1] == (4, 4)   # + 1/9・1/10
