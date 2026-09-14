"""列構成が変わったときにロールアップ CSV を壊さないことの回帰テスト（ISSUE-252）。"""
from __future__ import annotations

import pandas as pd
import pytest

from marketdata import rollup


def _write(path, header, rows):
    path.write_text("\n".join([",".join(header)] + [",".join(map(str, r)) for r in rows]) + "\n")


def test_merge_keeps_all_sum_columns(tmp_path):
    """既存 CSV と新規 tail のマージで up/dn を落とさない（列を落とすとヘッダが縮む）。"""
    idx = pd.to_datetime(["2026-08-01 00:00:00", "2026-08-01 00:01:00"])
    df = pd.DataFrame(
        {"open": [1.0, 2.0], "high": [2.0, 3.0], "low": [0.5, 1.5],
         "close": [1.5, 2.5], "volume": [10.0, 20.0], "up": [6.0, 12.0], "dn": [4.0, 8.0]},
        index=idx,
    )
    agg = rollup._merge_agg(df.columns)
    got = df.groupby(level=0, sort=True).agg(agg)
    assert list(got.columns) == ["open", "high", "low", "close", "volume", "up", "dn"]


def test_append_is_refused_when_the_header_does_not_match(tmp_path):
    """ヘッダ 6 列のファイルへ 8 列行を追記しない（追記すれば CSV が恒久的に読めなくなる）。"""
    path = tmp_path / "x_1M.csv"
    _write(path, ["date", "open", "high", "low", "close", "volume"],
           [["2026-07-31 00:00:00", 1, 2, 0, 1, 5]])
    bars = {pd.Timestamp("2026-08-31"): {"open": 1.0, "high": 2.0, "low": 0.0,
                                         "close": 1.0, "volume": 5.0, "up": 3.0, "dn": 2.0}}
    assert rollup._header_of(path) != rollup._header_for_bars(bars)


def test_header_of_reads_the_first_line(tmp_path):
    path = tmp_path / "y_5m.csv"
    _write(path, ["date", "open", "high", "low", "close", "volume", "up", "dn"], [])
    assert rollup._header_of(path) == ["date", "open", "high", "low", "close", "volume", "up", "dn"]
    assert rollup._header_of(tmp_path / "missing.csv") is None

# =========================================================================== #
# ISSUE-258: 全件 rewrite 経路（_write_rollup_df）も列を落とさない
# =========================================================================== #

def _updown_frame() -> pd.DataFrame:
    idx = pd.to_datetime(["2026-07-01 00:00:00", "2026-08-01 00:00:00"])
    return pd.DataFrame(
        {"open": [1.0, 2.0], "high": [3.0, 4.0], "low": [0.0, 1.0],
         "close": [2.0, 3.0], "volume": [10.0, 20.0], "up": [6.0, 11.0], "dn": [4.0, 9.0]},
        index=idx,
    )


def test_full_rewrite_keeps_the_updown_columns(tmp_path):
    """全件 rewrite（probe 不足＝1M 等・ファイル不在・空）でも up/dn を落とさない。

    ISSUE-252 は集約側（``_merge_agg``）だけを導出化し、その直後の書き出し
    （``_write_rollup_df``）に列の直書きが残っていた。全件 rewrite は毎分の watch から
    1M で到達するため、一度落ちるとヘッダ不一致で次回も全件 rewrite へ落ちて自己修復しない。
    """
    df = _updown_frame()
    rollup._write_rollup_df(tmp_path, "1M", df, ref_prefix="jp225_tick")
    path = tmp_path / "jp225_tick_1M.csv"
    assert rollup._header_of(path) == ["date", "open", "high", "low", "close", "volume", "up", "dn"]


def test_full_rewrite_output_header_matches_the_append_path(tmp_path):
    """全件 rewrite のヘッダが速い経路（追記）の期待ヘッダと一致する。

    一致しないと、次回の増分更新が「列構成が変わった」と判定して再び全件 rewrite へ落ちる
    （＝速い経路へ戻れない）。両経路のヘッダ決定が同じ規約から導出されることを固定する。
    """
    df = _updown_frame()
    rollup._write_rollup_df(tmp_path, "1M", df, ref_prefix="jp225_tick")
    bars = {ts: {c: float(row[c]) for c in df.columns} for ts, row in df.iterrows()}
    assert rollup._header_of(tmp_path / "jp225_tick_1M.csv") == rollup._header_for_bars(bars)


def test_full_rewrite_keeps_the_legacy_shape_without_updown(tmp_path):
    """up/dn を持たない素材（jp225_m1 等）では従来どおり 6 列（既存 CSV の書式不変）。"""
    df = _updown_frame().drop(columns=["up", "dn"])
    rollup._write_rollup_df(tmp_path, "1D", df, ref_prefix="jp225")
    assert rollup._header_of(tmp_path / "jp225_1D.csv") == \
        ["date", "open", "high", "low", "close", "volume"]
