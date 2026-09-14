"""S3: export_jp225_m1 の dukascopy 直呼び → DukascopyCandleSource 委譲化の検証（TDD）。

設計正典: ``.doc/MARKETDATA_TIMESERIES_BOUNDARY_DESIGN.md`` §6 S3 行・§2.1（volume 経路）。

検証する不変条件（S3 確定仕様）:
  1. **CSV バイト一致**: dukascopy raw（fetch 戻り DataFrame）を fixture 固定した状態で、
     委譲後 ``stream_to_csv`` 出力が「委譲前 ``_df_to_rows`` ロジックで生成した golden」と
     ``filecmp.cmp(shallow=False)`` でバイト一致する（列順 date,open,high,low,close,volume・
     date 書式 %Y-%m-%d %H:%M:%S・チャンク境界重複除去・float repr すべて不変）。
  2. **vendor 隔離完成（回帰）**: ``export_jp225_m1.py`` に ``dukascopy_python`` の直接 import が
     復活したら落ちる（marketdata 経由＝vendor 隔離が export_jp225_m1 から外れたら検出する）。
     memory ``bugfix-pair-with-regression-test``: 「直 import 復活」という間違いを禁止する 1 本。

決定論性（F.I.R.S.T）: 実 Dukascopy へのネットワークアクセスは行わない。
``dukascopy_python.fetch`` を marketdata.dukascopy_source 越しに mock し決定論化する。
"""

from __future__ import annotations

import csv
import filecmp
import re
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import pytest

import export_jp225_m1 as mod

_EXPORT_SRC = Path(mod.__file__)


# --------------------------------------------------------------------------- #
# 補助: dukascopy raw fetch 戻り DataFrame（fixture）と golden 生成
# --------------------------------------------------------------------------- #
def _make_fetch_df(timestamps, base=100.0):
    """実 ``dukascopy_python.fetch`` 戻り値の契約（tz-aware UTC index・OHLCV 列）を再現する。"""
    idx = pd.to_datetime(list(timestamps), utc=True)
    n = len(timestamps)
    return pd.DataFrame(
        {
            "open": [base + i * 0.1 for i in range(n)],
            "high": [base + 1 + i * 0.1 for i in range(n)],
            "low": [base - 1 + i * 0.1 for i in range(n)],
            "close": [base + 0.5 + i * 0.1 for i in range(n)],
            "volume": [10.0 + i for i in range(n)],
        },
        index=idx,
    )


def _golden_rows_from_pre_delegation_logic(df: pd.DataFrame):
    """委譲前 ``_df_to_rows`` と同一ロジック（DataFrame index 直 strftime）で golden 行を作る。

    委譲後実装はこのロジックを通らず Candle.time(UNIX秒)→datetime 経由になるため、
    両者がバイト一致することが S3 の核心 oracle（date 書式・float repr 不変）になる。
    """
    rows = []
    for ts, o, h, low, c, v in zip(
        df.index, df["open"], df["high"], df["low"], df["close"], df["volume"]
    ):
        date_str = ts.to_pydatetime().replace(tzinfo=None).strftime("%Y-%m-%d %H:%M:%S")
        rows.append([date_str, float(o), float(h), float(low), float(c), float(v)])
    return rows


def _write_golden_csv(rows, path: Path):
    """golden CSV を委譲前と同一の writer（csv.writer・newline=""）で物理生成する。"""
    with open(path, "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(["date", "open", "high", "low", "close", "volume"])
        w.writerows(rows)


# --------------------------------------------------------------------------- #
# oracle 1: 委譲後 stream_to_csv 出力が golden とバイト一致（単一チャンク）
# --------------------------------------------------------------------------- #
def test_stream_to_csv_delegation_byte_matches_golden_single_chunk(
    tmp_path: Path, monkeypatch
):
    # Arrange: dukascopy raw を fixture 固定（marketdata.dukascopy_source 越しに mock）。
    import marketdata.dukascopy_source as dsrc

    df = _make_fetch_df(
        ["2026-06-15 09:00:00", "2026-06-15 09:01:00", "2026-06-15 09:02:00"]
    )
    monkeypatch.setattr(dsrc.dukascopy_python, "fetch", lambda *a, **k: df)

    golden = tmp_path / "golden.csv"
    _write_golden_csv(_golden_rows_from_pre_delegation_logic(df), golden)

    out = tmp_path / "jp225_m1.csv"
    # Act: 委譲後 stream_to_csv（repair=False で外れ値補正の影響を排除し純粋に委譲経路を検査）。
    total = mod.stream_to_csv(
        datetime(2026, 6, 15), datetime(2026, 6, 16), out, repair=False
    )

    # Assert: 行数一致＋バイト一致（date 書式・列順・float repr すべて golden と同一）。
    assert total == 3
    assert filecmp.cmp(golden, out, shallow=False), (
        "委譲後 CSV が golden（委譲前ロジック）とバイト一致しない:\n"
        f"golden=\n{golden.read_text()}\nout=\n{out.read_text()}"
    )


# --------------------------------------------------------------------------- #
# oracle 2: 複数チャンク境界の重複除去が委譲後も不変（境界で last_ts 超過のみ採用）
# --------------------------------------------------------------------------- #
def test_stream_to_csv_delegation_dedup_at_chunk_boundary_byte_matches(
    tmp_path: Path, monkeypatch
):
    # Arrange: chunk_months=1 で 2 チャンク。境界（前チャンク末 09:02 == 次チャンク頭）を重複させ、
    #   委譲後も「last_ts 超過行のみ採用」で重複除去されることを golden 比較で固定する。
    import marketdata.dukascopy_source as dsrc

    chunk1 = _make_fetch_df(["2026-06-15 23:58:00", "2026-06-15 23:59:00"])
    # 次チャンク頭が前チャンク末（23:59）と重複し、それ以降の新規行を持つ。
    chunk2 = _make_fetch_df(
        ["2026-06-15 23:59:00", "2026-07-01 00:00:00", "2026-07-01 00:01:00"], base=200.0
    )
    seq = iter([chunk1, chunk2])
    monkeypatch.setattr(dsrc.dukascopy_python, "fetch", lambda *a, **k: next(seq))

    # golden: 委譲前と同じ重複除去（last_ts=23:59 超過のみ）を適用した行列を作る。
    last_ts = None
    golden_rows = []
    for df in (chunk1, chunk2):
        if last_ts is not None:
            df = df[df.index > last_ts]
        if df.empty:
            continue
        last_ts = df.index.max()
        golden_rows.extend(_golden_rows_from_pre_delegation_logic(df))
    golden = tmp_path / "golden.csv"
    _write_golden_csv(golden_rows, golden)

    out = tmp_path / "jp225_m1.csv"
    # Act: 2 チャンクにまたがる期間。
    total = mod.stream_to_csv(
        datetime(2026, 6, 15), datetime(2026, 7, 2), out, chunk_months=1, repair=False
    )

    # Assert: 重複 23:59 が二重採用されず（=4 行）golden とバイト一致。
    assert total == 4
    assert filecmp.cmp(golden, out, shallow=False), (
        "チャンク境界重複除去が委譲後に不変でない:\n"
        f"golden=\n{golden.read_text()}\nout=\n{out.read_text()}"
    )


# --------------------------------------------------------------------------- #
# oracle 3: append_incremental（増分）も委譲後にバイト一致（date 書式・重複除去）
# --------------------------------------------------------------------------- #
def test_append_incremental_delegation_byte_matches_golden(tmp_path: Path, monkeypatch):
    # Arrange: 既存末尾 09:01。fetch は 09:01（重複）+09:02 を返す → 09:02 のみ追記されるべき。
    import marketdata.dukascopy_source as dsrc

    existing = (
        "date,open,high,low,close,volume\n"
        "2026-06-15 09:00:00,100.0,101.0,99.0,100.5,10.0\n"
        "2026-06-15 09:01:00,100.5,102.0,100.0,101.5,12.0\n"
    )
    df = _make_fetch_df(["2026-06-15 09:01:00", "2026-06-15 09:02:00"])
    monkeypatch.setattr(dsrc.dukascopy_python, "fetch", lambda *a, **k: df)

    # golden: 既存内容 + （last_ts=09:01 超過＝09:02 のみ）を委譲前ロジックで追記した姿。
    last_ts = datetime(2026, 6, 15, 9, 1, 0)
    df_norm = df.tz_convert("UTC").tz_localize(None)
    df_new = df_norm[df_norm.index > last_ts]
    golden = tmp_path / "golden.csv"
    golden.write_text(existing, encoding="utf-8")
    with open(golden, "a", encoding="utf-8", newline="") as f:
        csv.writer(f).writerows(_golden_rows_from_pre_delegation_logic(df_new))

    out = tmp_path / "jp225_m1.csv"
    out.write_text(existing, encoding="utf-8")
    # Act
    written = mod.append_incremental(
        out, now=datetime(2026, 6, 15, 12, 0, 0), lag_minutes=3,
        default_start=datetime(2026, 6, 15), repair=False,
    )

    # Assert: 09:02 のみ追記（1 行）かつ golden とバイト一致。
    assert written == 1
    assert filecmp.cmp(golden, out, shallow=False), (
        "委譲後 append_incremental がバイト一致しない:\n"
        f"golden=\n{golden.read_text()}\nout=\n{out.read_text()}"
    )


# --------------------------------------------------------------------------- #
# 回帰: dukascopy_python の直接 import 不在（vendor 隔離完成・S3 の核心ゴール）
#   memory bugfix-pair-with-regression-test: 直 import 復活で落ちる 1 本。
# --------------------------------------------------------------------------- #
def test_export_jp225_m1_has_no_direct_dukascopy_import():
    # Arrange: ソースを読み、import 文（コメント・docstring を除く実コード）を検査する。
    src = _EXPORT_SRC.read_text(encoding="utf-8")
    # ``import dukascopy_python`` / ``from dukascopy_python ...`` の実 import 行を検出する。
    #   行頭（任意の空白）から始まる import 文のみを対象とし、コメント内言及は除外する。
    direct_imports = [
        ln
        for ln in src.splitlines()
        if re.match(r"\s*(import\s+dukascopy_python|from\s+dukascopy_python)\b", ln)
    ]
    # Assert: 直接 import は 0 件（vendor 隔離は marketdata 経由に完成している）。
    assert direct_imports == [], (
        "export_jp225_m1.py に dukascopy_python の直接 import が存在する"
        f"（marketdata 経由＝vendor 隔離違反）: {direct_imports}"
    )


def test_export_jp225_m1_delegates_via_marketdata_candle_source():
    # Arrange/Act: 委譲先（marketdata の DukascopyCandleSource）を import している。
    src = _EXPORT_SRC.read_text(encoding="utf-8")
    # Assert: marketdata から DukascopyCandleSource を取り込んでいる（委譲の配線が存在する）。
    assert "DukascopyCandleSource" in src, (
        "export_jp225_m1.py が marketdata.DukascopyCandleSource へ委譲していない"
    )
