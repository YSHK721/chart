"""IntrabarWindowRepository の結線テスト（proto do_intraday 忠実・ISSUE-132: m1 は dataset 委譲）。

m1 は fake bridge の ``dataset.load_atom_window``（単一権威）委譲を検証し、tick は合成 parquet を
tmp に置いて実ティック mid（窓+外れ値除去）を検証する。
"""
from __future__ import annotations

from types import SimpleNamespace

import pandas as pd

from simulator.replay_ui.adapter.intrabar_window_repository import (
    IntrabarWindowRepository,
    _cap_m1_rows,
)


def _fake_bridge(df: pd.DataFrame):
    calls: dict = {}

    def load_atom_window(ref, start, end):
        calls["args"] = (ref, start, end)
        secs = df.index.values.astype("datetime64[s]").astype("int64")
        return df[(secs >= start) & (secs < end)]

    return SimpleNamespace(dataset=SimpleNamespace(load_atom_window=load_atom_window)), calls


def _m1_df():
    rows = [
        ("2020-01-01 00:00:00", 100.0, 105.0, 99.0, 101.0, 1.0),  # start=1577836800
        ("2020-01-01 00:01:00", 101.0, 106.0, 100.0, 102.0, 1.0),
        ("2020-01-01 00:02:00", 102.0, 107.0, 98.0, 103.0, 1.0),
    ]
    idx = pd.to_datetime([r[0] for r in rows])
    return pd.DataFrame(
        [list(r[1:]) for r in rows], index=idx,
        columns=["open", "high", "low", "close", "volume"],
    )


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


def test_load_m1_rows_delegates_to_dataset_atom_window(tmp_path):
    bridge, calls = _fake_bridge(_m1_df())
    repo = IntrabarWindowRepository(
        tick_root=tmp_path / "ticks", bridge_loader=lambda *a: bridge
    )
    # [00:00, 00:02) → 最初の 2 分のみ（窓抽出は dataset.load_atom_window＝単一権威へ委譲）。
    rows = repo.load_m1_rows("jp225_tick", _D1_00_00, _D1_00_00 + 120)
    assert calls["args"] == ("jp225_tick", _D1_00_00, _D1_00_00 + 120)
    assert rows == [[100.0, 105.0, 99.0, 101.0], [101.0, 106.0, 100.0, 102.0]]


def test_load_raw_ticks_returns_unfiltered_bid_ask(tmp_path):
    """ISSUE-031: Port は**素の観測値**を運ぶ（mid 算出・窓・外れ値除去はしない）。

    以前の ``load_ticks`` は domain E-4 を適用済みの ``(sec, mid)`` を返していた。本 adapter の
    責務は保管形式（parquet の日別レイアウト）→ 素の観測値の変換に閉じ、本質ルールの適用は
    usecase（:func:`~usecase.intrabar_window.intrabar_window`）が 1 か所で行う。
    """
    root = tmp_path / "ticks"
    _write_parquet(root)
    repo = IntrabarWindowRepository(tick_root=root)

    out = repo.load_raw_ticks(_D1_00_00, _D1_00_00 + 60)

    # 外れ値（30 秒の 200）も窓外（1:40）も**落とさない**＝整形しない契約。
    assert all(len(t) == 3 for t in out), "(sec, bid, ask) の 3 要素で返す"
    secs = [t[0] for t in out]
    assert _D1_00_00 + 30 in secs, "外れ値も adapter は落とさない（除去は usecase の責務）"


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
