"""ISSUE-186: 常駐 watch が追記中の CSV を読んでも壊れないこと。

背景（実測）:
    `tools/live_tick_watch.py --stream` / `tools/export_jp225_m1.py --watch` が常駐し、
    `jp225_tick_m1.csv`（276MB）/ M1 CSV（300MB）へ末尾追記している。読み手が**行の途中**を
    掴むと、`indigators/indicator_ui/api` の全スイートが 1 回だけ 13 failed / 405 passed を
    記録し、直後から 14 回連続 418 passed になった（再現性なし）。非決定的な失敗は回帰判定の
    信頼性を壊し、リファクタリングの挙動不変検証で偽陽性・偽陰性の両方を生む。

対策は書き手・読み手の両方:
    - 書き手: `marketdata.tick_m1._append_m1_csv` が本文をメモリで組み立てて **1 回の write**
      で流す（`DataFrame.to_csv(fh)` は行を複数回の write に分ける）。
    - 読み手: 本モジュールが「読取＋整形」を 1 単位として並行追記に耐える。

実測（3 秒間、読み手はループで読み続ける）:
    | 構成 | 失敗率 |
    |---|---|
    | 分割 write・対策なし | 32.61% |
    | 単一 write・対策なし | 0.07% |
    | 分割 write・対策あり | 0.00% |
    | 単一 write・対策あり | 0.00% |
"""

from __future__ import annotations

import sys
import threading
import time
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from marketdata.ohlc_csv_loader import (  # noqa: E402
    _file_size,
    _tail_is_incomplete,
    read_ohlc_csv_with_policy,
)

_HEADER = "date,open,high,low,close,volume\n"


def _seed(path: Path, n: int = 60) -> None:
    rows = "".join(f"2026-01-01 00:{i:02d}:00,1,2,0,1,10\n" for i in range(n))
    path.write_text(_HEADER + rows, encoding="utf-8")


# ---------------------------------------------------------------------------
# 判定部品（O(1) の証拠収集）
# ---------------------------------------------------------------------------

def test_tail_is_incomplete_detects_a_line_being_written(tmp_path):
    path = tmp_path / "m1.csv"
    _seed(path)
    assert not _tail_is_incomplete(path), "改行で終わる完全なファイルは「不完全」ではない"

    with open(path, "a", encoding="utf-8") as fh:      # 行の途中まで書いた状態
        fh.write("2026-01-01 01:00")
    assert _tail_is_incomplete(path), "改行で終わらない＝追記進行中"


def test_tail_is_incomplete_is_false_for_empty_file(tmp_path):
    """空ファイルは「追記進行中」とはしない（別種の異常として扱う）。"""
    path = tmp_path / "empty.csv"
    path.write_text("", encoding="utf-8")
    assert not _tail_is_incomplete(path)


def test_file_size_returns_minus_one_for_missing_path(tmp_path):
    assert _file_size(tmp_path / "nope.csv") == -1


# ---------------------------------------------------------------------------
# 本物のデータ異常を隠さないこと（最重要の非機能要件）
# ---------------------------------------------------------------------------

def test_genuine_bad_data_still_raises(tmp_path):
    """完結したファイル中の壊れた値は、再試行で握りつぶさずそのまま送出する。"""
    path = tmp_path / "bad.csv"
    path.write_text("date,open,high,low,close\nNOT_A_DATE,1,2,0,1\n", encoding="utf-8")

    with pytest.raises(ValueError):
        read_ohlc_csv_with_policy(path, {}, time_column="date")


def test_genuine_bad_data_is_not_retried(tmp_path, monkeypatch):
    """並行追記の証拠が無ければ**再試行しない**（無駄な再読取で 300MB を読み直さない）。"""
    path = tmp_path / "bad.csv"
    path.write_text("date,open,high,low,close\nNOT_A_DATE,1,2,0,1\n", encoding="utf-8")

    import marketdata.ohlc_csv_loader as loader

    calls = {"n": 0}
    original = loader.pd.read_csv

    def counting_read_csv(*args, **kwargs):
        calls["n"] += 1
        return original(*args, **kwargs)

    monkeypatch.setattr(loader.pd, "read_csv", counting_read_csv)
    with pytest.raises(ValueError):
        read_ohlc_csv_with_policy(path, {}, time_column="date")
    assert calls["n"] == 1, f"再試行してはいけない（実際 {calls['n']} 回）"


# ---------------------------------------------------------------------------
# 競合の再現と是正（本丸）
# ---------------------------------------------------------------------------

def _race(path: Path, writer, seconds: float = 1.5) -> tuple[int, int]:
    """`writer` が追記し続ける横で読み続け、(成功数, 失敗数) を返す。"""
    stop = threading.Event()
    thread = threading.Thread(target=writer, args=(path, stop), daemon=True)
    thread.start()
    ok = bad = 0
    deadline = time.time() + seconds
    try:
        while time.time() < deadline:
            try:
                read_ohlc_csv_with_policy(path, {}, time_column="date")
                ok += 1
            except Exception:                       # noqa: BLE001 - 失敗種別は問わず計数する
                bad += 1
    finally:
        stop.set()
        thread.join(timeout=2)
    return ok, bad


def _split_writer(path: Path, stop: threading.Event) -> None:
    """1 行を 2 回の write に分けて書く（`DataFrame.to_csv(fh)` 相当の最悪ケース）。"""
    i = 60
    while not stop.is_set():
        line = f"2026-01-01 01:{i % 60:02d}:00,1,2,0,1,10\n"
        with open(path, "a", encoding="utf-8") as fh:
            for chunk in (line[:12], line[12:]):
                fh.write(chunk)
                fh.flush()
                time.sleep(0.0007)
        i += 1
        time.sleep(0.001)


def _single_writer(path: Path, stop: threading.Event) -> None:
    """本文を組み立てて 1 回の write で流す（是正後の `_append_m1_csv` 相当）。"""
    i = 60
    while not stop.is_set():
        text = "".join(f"2026-01-01 01:{(i + k) % 60:02d}:00,1,2,0,1,10\n" for k in range(3))
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(text)
            fh.flush()
        i += 3
        time.sleep(0.001)


@pytest.mark.parametrize("writer", [_single_writer, _split_writer],
                         ids=["single_write", "split_write"])
def test_reading_while_appended_never_fails(tmp_path, writer):
    """追記の最中に読み続けても 1 度も失敗しないこと。

    `split_write` は `DataFrame.to_csv(fh)` 相当の最悪ケース（是正前はここで 32.61% 失敗した）。
    `single_write` は是正後の書き手（それでも 0.07% 残ったため読み手側でも守る）。
    """
    path = tmp_path / "m1.csv"
    _seed(path)

    ok, bad = _race(path, writer)

    assert ok > 0, "読取が 1 度も成立しないテストは空虚（競合以前の設定ミス）"
    assert bad == 0, f"追記中の読取が {bad} 回失敗した（成功 {ok} 回）"
