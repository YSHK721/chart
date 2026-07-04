"""IntrabarWindowRepository の結線テスト（proto do_intraday 忠実）。

合成 m1 CSV と合成 tick parquet を tmp に置き、m1 窓抽出/cap と実ティック mid（窓+外れ値除去）を検証。
"""
from __future__ import annotations

import pandas as pd

from simulator.replay_ui.adapter.intrabar_window_repository import (
    IntrabarWindowRepository,
    _cap_m1_rows,
)


def _write_m1(path):
    rows = [
        ("2020-01-01 00:00:00", 100.0, 105.0, 99.0, 101.0, 1.0),  # start=1577836800
        ("2020-01-01 00:01:00", 101.0, 106.0, 100.0, 102.0, 1.0),
        ("2020-01-01 00:02:00", 102.0, 107.0, 98.0, 103.0, 1.0),
    ]
    df = pd.DataFrame(rows, columns=["date", "open", "high", "low", "close", "volume"])
    df.to_csv(path, index=False)


def _write_parquet(root):
    # 2020-01-01（Y/M/D レイアウト）。timestamp は tz-aware UTC。
    p = root / "2020" / "01" / "01"
    p.mkdir(parents=True)
    ts = pd.to_datetime(
        [
            "2020-01-01 00:00:10",
            "2020-01-01 00:00:20",
            "2020-01-01 00:00:30",  # 外れ（bid/ask=200 → +100%）
            "2020-01-01 00:01:40",  # 窓外（>=第2分だが end 次第）
        ],
        utc=True,
    )
    df = pd.DataFrame(
        {
            "timestamp": ts,
            "bidPrice": [99.0, 101.0, 200.0, 100.0],
            "askPrice": [101.0, 103.0, 200.0, 102.0],
            "bidVolume": [1.0, 1.0, 1.0, 1.0],
            "askVolume": [1.0, 1.0, 1.0, 1.0],
        }
    )
    df.to_parquet(p / "JP225_ticks.parquet", index=False)


_D1_00_00 = 1577836800  # 2020-01-01 00:00:00 UTC


def test_load_m1_rows_windowed(tmp_path):
    csv = tmp_path / "m1.csv"
    _write_m1(csv)
    repo = IntrabarWindowRepository(
        tick_root=tmp_path / "ticks", tick_m1_csv=csv, m1_repair=False
    )
    # [00:00, 00:02) → 最初の 2 分のみ。
    rows = repo.load_m1_rows("jp225_tick", _D1_00_00, _D1_00_00 + 120)
    assert rows == [[100.0, 105.0, 99.0, 101.0], [101.0, 106.0, 100.0, 102.0]]


def test_load_ticks_window_and_outlier(tmp_path):
    root = tmp_path / "ticks"
    _write_parquet(root)
    repo = IntrabarWindowRepository(tick_root=root, tick_m1_csv=None)
    # 窓 [00:00, 00:01) → 00:00:10/20/30 の 3 点。中央値 mid ≈ 101、200 は +≈98% で除去。
    out = repo.load_ticks(_D1_00_00, _D1_00_00 + 60)
    secs = [s for s, _ in out]
    mids = [m for _, m in out]
    assert secs == [_D1_00_00 + 10, _D1_00_00 + 20]  # 外れ(30 秒)除去・窓外(1:40)除外
    assert mids == [100.0, 102.0]  # (99+101)/2, (101+103)/2


def test_cap_m1_rows_keeps_extremes_and_bounds():
    # 6 行を 3 行へ間引く。先頭/末尾＋高値最大/安値最小は必ず残る。
    rows = [
        [1.0, 2.0, 1.0, 1.0],
        [1.0, 9.0, 1.0, 1.0],  # high 最大
        [1.0, 2.0, 1.0, 1.0],
        [1.0, 2.0, 0.1, 1.0],  # low 最小
        [1.0, 2.0, 1.0, 1.0],
        [1.0, 2.0, 1.0, 5.0],
    ]
    out = _cap_m1_rows(rows, 3)
    assert rows[0] in out and rows[-1] in out
    assert [1.0, 9.0, 1.0, 1.0] in out  # high 最大
    assert [1.0, 2.0, 0.1, 1.0] in out  # low 最小


def test_cap_m1_rows_noop_when_within_limit():
    rows = [[1.0, 2.0, 1.0, 1.0], [1.0, 2.0, 1.0, 1.0]]
    assert _cap_m1_rows(rows, 1500) is rows
