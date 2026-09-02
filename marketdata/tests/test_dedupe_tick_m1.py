"""marketdata.tools.dedupe_tick_m1 の検証（ISSUE-455 データ修復・TDD Red→Green）。

対象: 8 重連結した ``jp225_tick_m1.csv`` を date で重複除去（keep="last"＝up/dn 有りブロックを
採る・dataset.py:83 と同規則）し、date 昇順・一様 8 列ヘッダで原子的に書き直す単体スクリプト。

不変条件（合成データで固定）:
  - 重複除去: 出力は date 一意・昇順・8 列一様。値は keep="last"（最終出現）と一致。
  - 計算量: 出力行数 = 一意 date 数（捨てた数 = 総行数 − 一意数 が全部説明つく＝作って捨てる不在）。
  - 原子性: tmp を残さない。バックアップ（.dup8x.bak）を作る・既存 .bak を上書きしない。
  - 冪等: 既に一意なら重複除去 0・再バックアップしない・ファイルを触らない。
  - dry-run: 件数のみ報告し書き込まない。
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

from marketdata.tools import dedupe_tick_m1

_HEADER = "date,open,high,low,close,volume,up,dn"


def _bar(date: str, base: float, up: int, dn: int) -> str:
    return f"{date},{base},{base + 1},{base - 1},{base},3.0,{up},{dn}"


# 3 つの一意 date。各 date を 8 回（8 重連結）並べる。最終ブロックの値で keep="last"。
_DATES = ["2025-01-02 09:00:00", "2025-01-02 09:01:00", "2025-01-02 09:02:00"]


def _write_8x(path: Path) -> None:
    lines = [_HEADER]
    for block in range(8):  # 8 重連結（各ブロックが全 date を昇順で持つ）。
        for i, d in enumerate(_DATES):
            # ブロックごとに base を変え、最終ブロック（block=7）の値が keep="last" になる。
            lines.append(_bar(d, 100.0 + i + block * 10, up=block, dn=i))
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def test_dedupe_produces_unique_sorted_8col_keep_last(tmp_path: Path) -> None:
    p = tmp_path / "jp225_tick_m1.csv"
    _write_8x(p)

    res = dedupe_tick_m1.dedupe_file(p)

    df = pd.read_csv(p)
    # 一様 8 列ヘッダ。
    assert list(df.columns) == ["date", "open", "high", "low", "close", "volume", "up", "dn"]
    # date 一意・昇順。
    assert df["date"].is_unique
    assert list(df["date"]) == _DATES
    # 全 date に up/dn 有り（欠損なし）。
    assert df[["up", "dn"]].notna().all().all()
    # 値は keep="last"（最終ブロック block=7）と一致: open = 100 + i + 70。
    for i, d in enumerate(_DATES):
        row = df[df["date"] == d].iloc[0]
        assert row["open"] == 100.0 + i + 70
        assert row["up"] == 7  # 最終ブロックの up=block=7。


def test_output_rows_equal_unique_date_count(tmp_path: Path) -> None:
    # 計算量検定: 出力行数 = 一意 date 数。捨てた数 = 総 − 一意（8x−1x）が全て説明つく。
    p = tmp_path / "jp225_tick_m1.csv"
    _write_8x(p)
    res = dedupe_tick_m1.dedupe_file(p)
    assert res.total_rows_in == len(_DATES) * 8
    assert res.unique_rows_out == len(_DATES)
    assert res.removed == len(_DATES) * 8 - len(_DATES)
    assert len(pd.read_csv(p)) == len(_DATES)


def test_output_rows_depend_only_on_unique_not_dup_factor(tmp_path: Path) -> None:
    # オーダーの表明: 一意数を固定し重複度だけ変えても出力行数は不変（2 点）。
    def _dup(path: Path, factor: int) -> int:
        lines = [_HEADER]
        for _ in range(factor):
            for i, d in enumerate(_DATES):
                lines.append(_bar(d, 100.0 + i, up=1, dn=0))
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        return dedupe_tick_m1.dedupe_file(path).unique_rows_out

    out2 = _dup(tmp_path / "a.csv", 2)
    out5 = _dup(tmp_path / "b.csv", 5)
    assert out2 == out5 == len(_DATES)  # 出力は一意数だけで決まる（重複度に不感）。


def test_backup_created_with_original_content(tmp_path: Path) -> None:
    p = tmp_path / "jp225_tick_m1.csv"
    _write_8x(p)
    original = p.read_text(encoding="utf-8")
    res = dedupe_tick_m1.dedupe_file(p)
    bak = Path(str(p) + ".dup8x.bak")
    assert res.backup_path == bak
    assert bak.is_file()
    assert bak.read_text(encoding="utf-8") == original  # 退避は元の全内容（復元可能）。


def test_atomic_leaves_no_tmp(tmp_path: Path) -> None:
    p = tmp_path / "jp225_tick_m1.csv"
    _write_8x(p)
    dedupe_tick_m1.dedupe_file(p)
    leftovers = [q.name for q in tmp_path.iterdir() if q.name.endswith(".tmp")]
    assert leftovers == []  # tmp を残さない。


def test_backup_not_overwritten_if_exists(tmp_path: Path) -> None:
    p = tmp_path / "jp225_tick_m1.csv"
    _write_8x(p)
    bak = Path(str(p) + ".dup8x.bak")
    bak.write_text("PRECIOUS-EXISTING-BACKUP", encoding="utf-8")
    with pytest.raises(FileExistsError):
        dedupe_tick_m1.dedupe_file(p)
    assert bak.read_text(encoding="utf-8") == "PRECIOUS-EXISTING-BACKUP"  # 既存 .bak は無傷。


def test_idempotent_rerun_no_removal_no_rebackup(tmp_path: Path) -> None:
    p = tmp_path / "jp225_tick_m1.csv"
    _write_8x(p)
    dedupe_tick_m1.dedupe_file(p)              # 1 回目: 重複除去。
    bak = Path(str(p) + ".dup8x.bak")
    bak_mtime = bak.stat().st_mtime_ns
    after_first = p.read_text(encoding="utf-8")

    res2 = dedupe_tick_m1.dedupe_file(p)       # 2 回目: 既に一意 → no-op。
    assert res2.removed == 0
    assert res2.replaced is False
    assert res2.backup_path is None
    assert p.read_text(encoding="utf-8") == after_first  # 本体不変。
    assert bak.stat().st_mtime_ns == bak_mtime          # 再バックアップしない（既存 .bak 不変）。


def test_dry_run_reports_counts_without_writing(tmp_path: Path) -> None:
    p = tmp_path / "jp225_tick_m1.csv"
    _write_8x(p)
    before = p.read_text(encoding="utf-8")
    res = dedupe_tick_m1.dedupe_file(p, dry_run=True)
    assert res.total_rows_in == len(_DATES) * 8
    assert res.unique_rows_out == len(_DATES)
    assert res.removed == len(_DATES) * 8 - len(_DATES)
    assert res.replaced is False
    assert p.read_text(encoding="utf-8") == before          # 本体不変（書き込まない）。
    assert not Path(str(p) + ".dup8x.bak").exists()         # バックアップも作らない。


def test_old_six_col_rows_padded_to_uniform_eight(tmp_path: Path) -> None:
    # up/dn を持たない旧 6 列行が混ざっても、出力は一様 8 列（up/dn は空欄）に揃う。
    p = tmp_path / "jp225_tick_m1.csv"
    p.write_text(
        _HEADER + "\n"
        "2012-06-14 17:46:00,8568.89,8568.89,8568.89,8568.89,1.0\n"      # 旧 6 列。
        "2012-06-14 17:46:00,8568.89,8568.89,8568.89,8568.89,1.0\n"      # 重複（keep=last も 6 列）。
        "2025-01-02 09:00:00,100.0,101.0,99.0,100.0,3.0,2,1\n",          # 新 8 列。
        encoding="utf-8",
    )
    dedupe_tick_m1.dedupe_file(p)
    df = pd.read_csv(p)
    assert list(df.columns) == ["date", "open", "high", "low", "close", "volume", "up", "dn"]
    assert len(df) == 2  # 2 つの一意 date。
    # 旧行は 8 列へ整形され up/dn は NaN（空欄）。新行は up/dn を保持。
    old = df[df["date"] == "2012-06-14 17:46:00"].iloc[0]
    assert pd.isna(old["up"]) and pd.isna(old["dn"])
    new = df[df["date"] == "2025-01-02 09:00:00"].iloc[0]
    assert new["up"] == 2 and new["dn"] == 1


def test_overrun_row_raises(tmp_path: Path) -> None:
    # 列数超過（列ずれの破損）は黙って採用せず停止する。
    p = tmp_path / "jp225_tick_m1.csv"
    p.write_text(
        _HEADER + "\n2025-01-02 09:00:00,1,2,0,1,3,1,0,EXTRA\n",  # 9 フィールド。
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="列数超過"):
        dedupe_tick_m1.dedupe_file(p)
