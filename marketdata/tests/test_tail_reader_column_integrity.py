"""tail_reader の列数整合検査（ISSUE-455 再発防止・TDD Red→Green）。

背景（実測・ISSUE-455）: ``jp225_tick_m1.csv`` はヘッダ列数（6）とデータ行のフィールド数（8）が
食い違い、``read_tail`` が ``pd.read_csv`` にそのまま渡した結果、余剰フィールドが index へ回って
列がずれ、``date`` 列に価格値（high）が入った。``pd.to_datetime(67034.949)`` は数値を**ナノ秒**と
解釈して **1970-01-01** を返し、下流の resume ガード（``index > last_date``）が全履歴を再選択して
毎分 8 重連結を生んだ。

根本原因の除去: ``read_tail`` はヘッダ列数とデータ行のフィールド数の不一致を検出したら
**Fail-Stop（ValueError）** し、価格を黙って日付へ変換させない。
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

from marketdata import tail_reader


def _write(path: Path, text: str) -> None:
    path.write_text(text, encoding="utf-8")


def test_read_tail_raises_when_data_has_more_fields_than_header(tmp_path: Path) -> None:
    # Arrange: ヘッダ 6 列だが本体は 8 フィールド（up/dn がヘッダ未反映のまま本体だけ 8 列化）。
    p = tmp_path / "jp225_tick_m1.csv"
    _write(
        p,
        "date,open,high,low,close,volume\n"
        "2025-01-01 00:00:00,100.0,101.0,99.0,100.5,3.0,2,1\n"
        "2025-01-01 00:01:00,100.5,102.0,100.0,101.5,4.0,3,1\n",
    )
    # Act / Assert: 黙って 1970 へ誤読せず ValueError で止まる。
    with pytest.raises(ValueError, match="列数"):
        tail_reader.read_tail(p, 2)


def test_read_tail_does_not_raise_when_data_has_fewer_fields_than_header(tmp_path: Path) -> None:
    # 不足側（ヘッダ 8 列・本体 6 フィールド＝up/dn 欠落の旧行/torn 行）は列がずれず安全。
    # pandas は欠落末尾列を NaN で埋める。date/OHLC は正しく読め、過剰な Fail-Stop で正常な
    # 旧行を拒否しない（危険なのは列がずれる超過側だけ・ISSUE-455）。
    p = tmp_path / "jp225_tick_m1.csv"
    _write(
        p,
        "date,open,high,low,close,volume,up,dn\n"
        "2025-01-01 00:00:00,100.0,101.0,99.0,100.5,3.0\n"
        "2025-01-01 00:01:00,100.5,102.0,100.0,101.5,4.0\n",
    )
    df = tail_reader.read_tail(p, 2)
    # date は 1970 へ誤変換されず正しく読める。OHLC も所定の列に入る。
    assert list(df.index.astype(str)) == ["2025-01-01 00:00:00", "2025-01-01 00:01:00"]
    assert df.iloc[-1]["close"] == 101.5
    # 欠落した末尾列（up/dn）は NaN。
    assert pd.isna(df.iloc[-1]["up"]) and pd.isna(df.iloc[-1]["dn"])


def test_read_tail_passes_through_when_columns_are_consistent(tmp_path: Path) -> None:
    # 整合している通常ケースは従来どおり読める（回帰防止）。
    p = tmp_path / "ok.csv"
    _write(
        p,
        "date,open,high,low,close,volume,up,dn\n"
        "2025-01-01 00:00:00,100.0,101.0,99.0,100.5,3.0,2,1\n"
        "2025-01-01 00:01:00,100.5,102.0,100.0,101.5,4.0,3,1\n",
    )
    df = tail_reader.read_tail(p, 2)
    assert list(df.index.astype(str)) == ["2025-01-01 00:00:00", "2025-01-01 00:01:00"]
    assert df.iloc[-1]["close"] == 101.5
    assert df.iloc[-1]["up"] == 3
