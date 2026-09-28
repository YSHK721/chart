"""MarketdataCsvOHLCRepository の spread 列の読取（ISSUE-511 段階 2）の検定。

固定する不変条件:
    1. spread 列を持つ marketdata CSV は、その値を Bar.spread（int）へ写す。
       列が無いときは 0（既存契約・``test_ohlc_marketdata_csv.py``）。
    2. 窓はフレーム段で先に効く（窓外の行の spread を読まない）。
    3. 負の spread は domain.Bar が OHLCInvalidError で拒否する。
    4. 計算量: Bar にした行数 − 採用 Bar 数 = 0（窓外の行の spread を読まない）。行数は
       ファイル総行数に依存しない（2 ファイル比較）。列が無ければ spread 列を読む形式を選ばない。
       観測は `_ohlc_frame` が宣言する観測口（`set_observer`）だけを使う（内部名の
       monkeypatch はしない・絶対命令 2026-09-25。旧版は私有関数 `_spread_of` を差し替えて
       数えており、列ごとの一括変換（ISSUE-551 段 2）で張り付く先を失った）。
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from marketdata.csv_schema import SPREAD_COLUMN
from simulator.adapter.repository import _ohlc_frame
from simulator.adapter.repository.ohlc_marketdata_csv import MarketdataCsvOHLCRepository
from simulator.domain.exceptions import OHLCInvalidError

#: 5 行の marketdata 形式（任意列 up/dn と spread 付き）。
_CSV = """date,open,high,low,close,volume,up,dn,spread
2024-01-08 00:00:00,100.0,101.0,99.0,100.5,10.0,1.0,0.0,71
2024-01-08 00:01:00,100.5,102.0,100.0,101.0,11.0,2.0,0.0,72
2024-01-09 00:00:00,101.0,103.0,101.0,102.0,12.0,3.0,0.0,70
2024-01-10 00:00:00,102.0,104.0,102.0,103.0,13.0,4.0,0.0,100
2024-01-11 00:00:00,103.0,105.0,103.0,104.0,14.0,5.0,0.0,65
"""

#: spread 列を持たない同じ 5 行。
_CSV_NO_SPREAD = """date,open,high,low,close,volume,up,dn
2024-01-08 00:00:00,100.0,101.0,99.0,100.5,10.0,1.0,0.0
2024-01-08 00:01:00,100.5,102.0,100.0,101.0,11.0,2.0,0.0
2024-01-09 00:00:00,101.0,103.0,101.0,102.0,12.0,3.0,0.0
2024-01-10 00:00:00,102.0,104.0,102.0,103.0,13.0,4.0,0.0
2024-01-11 00:00:00,103.0,105.0,103.0,104.0,14.0,5.0,0.0
"""


def _utc(y, m, d):
    return datetime(y, m, d, tzinfo=timezone.utc)


def _write(tmp_path, name, text):
    path = tmp_path / name
    path.write_text(text, encoding="utf-8")
    return str(path)


def _with_rows_outside_the_window(n: int) -> str:
    """``_CSV`` の後ろに窓外（2024-02）の行を ``n`` 行足した CSV 本文。"""
    extra = "".join(
        f"2024-02-01 {i // 60:02d}:{i % 60:02d}:00,105.0,106.0,104.0,105.0,1.0,0.0,0.0,80\n"
        for i in range(n)
    )
    return _CSV + extra


# --- 1. 状態検証 -----------------------------------------------------------------


def test_the_spread_column_becomes_bar_spread(tmp_path):
    bars = MarketdataCsvOHLCRepository().load(_write(tmp_path, "s.csv", _CSV))
    assert [b.spread for b in bars] == [71, 72, 70, 100, 65]


def test_bar_spread_is_a_python_int(tmp_path):
    bars = MarketdataCsvOHLCRepository().load(_write(tmp_path, "s.csv", _CSV))
    assert [(type(b.spread), b.spread) for b in bars] == [
        (int, 71), (int, 72), (int, 70), (int, 100), (int, 65),
    ]


def test_only_spreads_inside_the_window_are_mapped(tmp_path):
    bars = MarketdataCsvOHLCRepository(
        window=(_utc(2024, 1, 9), _utc(2024, 1, 11))
    ).load(_write(tmp_path, "s.csv", _CSV))
    assert [b.spread for b in bars] == [70, 100]


def test_a_negative_spread_is_refused(tmp_path):
    text = _CSV.replace("2024-01-09 00:00:00,101.0,103.0,101.0,102.0,12.0,3.0,0.0,70",
                        "2024-01-09 00:00:00,101.0,103.0,101.0,102.0,12.0,3.0,0.0,-1")
    with pytest.raises(OHLCInvalidError):
        MarketdataCsvOHLCRepository().load(_write(tmp_path, "neg.csv", text))


# --- 2. 計算量テスト（規約: 読んだ spread − 採用 Bar = 0・ファイル総行数に非依存）------


@pytest.fixture
def conversions():
    """`frame_to_bars` の観測口へ繋いだ記録（(Bar にした行数, 宣言した必須列) の列）。"""
    seen: "list[tuple[int, tuple[str, ...]]]" = []
    _ohlc_frame.set_observer(lambda rows, required: seen.append((rows, required)))
    yield seen
    _ohlc_frame.set_observer(None)


def _spread_reads(seen) -> int:
    return sum(rows for rows, required in seen if SPREAD_COLUMN in required)


def test_no_spread_is_read_outside_the_window(conversions, tmp_path):
    bars = MarketdataCsvOHLCRepository(
        window=(_utc(2024, 1, 9), _utc(2024, 1, 11))
    ).load(_write(tmp_path, "s.csv", _CSV))
    assert len(bars) > 0  # 空振り防止
    assert _spread_reads(conversions) - len(bars) == 0


def test_spread_reads_scale_with_the_window_not_the_file(conversions, tmp_path):
    # 2 点で固定: 同じ窓に対し、窓外 45 行を足しても読取は増えない。
    window = (_utc(2024, 1, 8), _utc(2024, 1, 12))
    results = []
    for name, text in (("five.csv", _CSV), ("fifty.csv", _with_rows_outside_the_window(45))):
        conversions.clear()
        bars = MarketdataCsvOHLCRepository(window=window).load(_write(tmp_path, name, text))
        results.append((_spread_reads(conversions), len(bars)))
    (small_read, small_used), (large_read, large_used) = results
    assert small_used > 0 and large_used > 0  # 空振り防止
    assert small_read - small_used == 0
    assert large_read - large_used == 0
    assert large_read == small_read


def test_no_spread_is_read_when_the_column_is_absent(conversions, tmp_path):
    bars = MarketdataCsvOHLCRepository().load(_write(tmp_path, "n.csv", _CSV_NO_SPREAD))
    used = 0  # 列が無い＝spread は 0 固定で、読み取った値は 1 つも使わない
    assert len(conversions) > 0  # 空振り防止（観測口が繋がっている）
    assert all(b.spread == 0 for b in bars)
    assert _spread_reads(conversions) - used == 0
