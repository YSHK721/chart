"""IntrabarWindowRepository の結線テスト（proto do_intraday 忠実・ISSUE-132: m1 は dataset 委譲）。

m1 は fake bridge の ``dataset.load_atom_window``（単一権威）委譲を検証し、tick は合成 parquet を
tmp に置いて実ティック mid（窓+外れ値除去）を検証する。
"""
from __future__ import annotations

from types import SimpleNamespace

import pandas as pd
import pytest

from marketdata import tf_meta, tick_day_source, tick_tree
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


def test_a_mid_ref_gets_unfiltered_midpoints(tmp_path):
    """jp225_tick（台帳の基準は mid）は ``(sec, mid)`` を窓・外れ値除去なしで受け取る。

    窓フィルタと外れ値除去は usecase の責務（ISSUE-031）であり、adapter は落とさない。
    価格の畳み方（mid）は台帳と marketdata/tick_m1.py の 1 箇所が決める（ISSUE-512 段階 4 の前提）。
    """
    root = tmp_path / "ticks"
    _write_parquet(root)
    repo = IntrabarWindowRepository(tick_root=root)

    out = repo.load_tick_prices("jp225_tick", _D1_00_00, _D1_00_00 + 60)

    assert out == [
        (_D1_00_00 + 10, 100.0), (_D1_00_00 + 20, 102.0),
        (_D1_00_00 + 30, 200.0), (_D1_00_00 + 100, 101.0),
    ], "外れ値も窓外も adapter は落とさない（除去は usecase の責務）"


def test_an_mt5_ref_reads_its_own_tree_at_bid(tmp_path):
    """jp225_mt5 は MT5 自身の木を bid で読む（Dukascopy の木を読まない・mid にしない）。

    以前は ref を受け取らず、どの ref でも Dukascopy の木を読んでいた（ISSUE-512 段階 4 の前提）。
    """
    root = tmp_path / "ticks"
    _write_parquet(root)                                     # Dukascopy の木（JP225）
    token = tf_meta.tick_tree_token("jp225_mt5")
    path = tick_tree.day_parquet_path("2020-01-01", symbol=token, data_dir=tmp_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame({
        "timestamp": pd.to_datetime(["2020-01-01 00:00:15"], utc=True),
        "bidPrice": [500.0], "askPrice": [510.0],
    }).to_parquet(path, index=False)
    repo = IntrabarWindowRepository(tick_root=root)

    out = repo.load_tick_prices("jp225_mt5", _D1_00_00, _D1_00_00 + 60)

    assert out == [(_D1_00_00 + 15, 500.0)]


def test_a_ref_without_a_tick_tree_has_no_ticks(tmp_path):
    """ティック木を持たない ref は足内ティックを持たない（他の ref の木を読まない）。"""
    root = tmp_path / "ticks"
    _write_parquet(root)
    repo = IntrabarWindowRepository(tick_root=root)

    assert repo.load_tick_prices("jp225_m1", _D1_00_00, _D1_00_00 + 60) == []


@pytest.mark.parametrize("days", [1, 2])
def test_only_the_days_in_the_window_are_read(monkeypatch, tmp_path, days):
    """計算量: 読んだ日数 − 窓と重なる日数 = 0（窓の日数 1/2 の 2 点で固定）。"""
    root = tmp_path / "ticks"
    for d in ("2019-12-31", "2020-01-01", "2020-01-02", "2020-01-03"):
        p = tick_tree.day_parquet_path(d, symbol="JP225", data_dir=tmp_path)
        p.parent.mkdir(parents=True, exist_ok=True)
        pd.DataFrame({
            "timestamp": pd.to_datetime([f"{d} 12:00:00"], utc=True),
            "bidPrice": [1.0], "askPrice": [1.0],
        }).to_parquet(p, index=False)
    read = []
    real = tick_day_source.read_day_ticks
    monkeypatch.setattr(tick_day_source, "read_day_ticks",
                        lambda p, c: (read.append(p), real(p, c))[1])
    repo = IntrabarWindowRepository(tick_root=root)

    repo.load_tick_prices("jp225_tick", _D1_00_00, _D1_00_00 + days * 86400)

    assert len(read) - days == 0, f"{days} 日の窓で {len(read)} 日を読んだ"


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
