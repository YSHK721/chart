"""ロールアップ末尾整合の機械的検査＋自己修復（ISSUE-488 根治）の検定。

背景（実測 2026-09-04）: live_tick_watch の二重起動が同一ロールアップ CSV へ競合書込し、
1M の 8 月バー・1D の 3 日ぶんが消えた。増分更新は state より古い行を二度と読まないため、
消えたバーは**出力が正しげなまま恒久欠落**する——状態検証では原理的に落ちない形
（ISSUE-450 と同型）。よって末尾整合を M1 再集計との突合で機械的に検査し、落ちた TF だけ
M1 から全件再構築して自己修復する。

計算量（CLAUDE.md 絶対命令 §4.1）: 検査は M1 末尾 probe（固定行数）に有界であり、
M1 全長に比例しない。probe の読み取りは 1 回で全 TF が共有する（発行 − 使用 = 0）。
回数そのものは焼き込まず、M1 長 2 点で読み取り・再集計の発行数が変わらないことを固定する。
"""

from __future__ import annotations

import csv as _csv
import json
from pathlib import Path

import pandas as pd
import pytest

from marketdata import rollup as rb
from marketdata import tail_reader


# --------------------------------------------------------------------------- #
# 合成 1 分足（決定論・実データを読まない）
# --------------------------------------------------------------------------- #
def _synthetic_m1(start: str, minutes: int) -> pd.DataFrame:
    idx = pd.date_range(start, periods=minutes, freq="1min")
    base = list(range(minutes))
    return pd.DataFrame(
        {
            "open": [100.0 + b % 50 for b in base],
            "high": [100.0 + b % 50 + 0.5 for b in base],
            "low": [100.0 + b % 50 - 0.5 for b in base],
            "close": [100.0 + b % 50 + 0.2 for b in base],
            "volume": [1.0 + (b % 7) for b in base],
        },
        index=idx,
    )


def _write_m1_csv(path: Path, df: pd.DataFrame) -> None:
    with open(path, "w", newline="") as fh:
        w = _csv.writer(fh)
        w.writerow(["date", "open", "high", "low", "close", "volume"])
        for ts, row in df.iterrows():
            w.writerow([
                ts.strftime("%Y-%m-%d %H:%M:%S"),
                row["open"], row["high"], row["low"], row["close"], row["volume"],
            ])


def _build(tmp_path: Path, *, days: int = 12, tfs=("1D", "1W")) -> "tuple[Path, Path]":
    m1 = tmp_path / "m1.csv"
    _write_m1_csv(m1, _synthetic_m1("2020-01-01 00:00:00", 60 * 24 * days))
    out = tmp_path / "rollups"
    out.mkdir()
    rb.stream_build(m1, list(tfs), out, "ref")
    return m1, out


def _drop_data_line(path: Path, *, offset_from_end: int) -> str:
    """末尾から ``offset_from_end`` 行目のデータ行を落とす（欠落の再現）。落とした行を返す。"""
    lines = path.read_text().splitlines(keepends=True)
    dropped = lines.pop(len(lines) - 1 - offset_from_end)
    path.write_text("".join(lines))
    return dropped


# --------------------------------------------------------------------------- #
# 検出と修復
# --------------------------------------------------------------------------- #
def test_a_missing_confirmed_bar_is_detected_and_rebuilt(tmp_path: Path) -> None:
    """ISSUE-488 の形そのもの: 中間の確定バーが消えても、検査が捉えて元どおりへ戻す。"""
    m1, out = _build(tmp_path)
    path = out / "ref_1D.csv"
    pristine = path.read_bytes()
    _drop_data_line(path, offset_from_end=3)
    assert path.read_bytes() != pristine

    healed = rb.heal_tail_gaps(m1, ["1D", "1W"], out, "ref", probe_rows=60 * 24 * 12)

    assert healed == ["1D"]
    assert path.read_bytes() == pristine        # M1 からの再構築で完全復元


def test_a_corrupted_bar_value_is_detected_and_rebuilt(tmp_path: Path) -> None:
    """欠落だけでなく**値の食い違い**（競合書込の破れ）も検査で捉える。"""
    m1, out = _build(tmp_path)
    path = out / "ref_1D.csv"
    pristine = path.read_bytes()
    lines = path.read_text().splitlines(keepends=True)
    fields = lines[-4].rstrip("\n").split(",")
    fields[4] = str(float(fields[4]) + 123.0)    # close を書き換える
    lines[-4] = ",".join(fields) + "\n"
    path.write_text("".join(lines))

    healed = rb.heal_tail_gaps(m1, ["1D"], out, "ref", probe_rows=60 * 24 * 12)

    assert healed == ["1D"]
    assert path.read_bytes() == pristine


def test_a_monthly_gap_like_issue_488_is_detected(tmp_path: Path) -> None:
    """1M の中間月バー消失（実障害の形）を、月周期を覆う probe で検出する。"""
    m1, out = _build(tmp_path, days=75, tfs=("1M",))
    path = out / "ref_1M.csv"
    pristine = path.read_bytes()
    _drop_data_line(path, offset_from_end=1)     # 中間の確定月（末尾は形成中の月）

    healed = rb.heal_tail_gaps(m1, ["1M"], out, "ref", probe_rows=60 * 24 * 75)

    assert healed == ["1M"]
    assert path.read_bytes() == pristine


def test_intact_rollups_are_left_untouched(tmp_path: Path, monkeypatch) -> None:
    """健全なファイルへは再構築を 1 回も発行しない（誤検出＝毎回全再構築の浪費を作らない）。"""
    m1, out = _build(tmp_path)
    rebuilds = []
    original = rb.stream_build
    monkeypatch.setattr(rb, "stream_build", lambda *a, **k: rebuilds.append(a) or original(*a, **k))
    before = {p.name: p.read_bytes() for p in out.glob("*.csv")}

    healed = rb.heal_tail_gaps(m1, ["1D", "1W"], out, "ref", probe_rows=60 * 24 * 12)

    assert healed == []
    assert rebuilds == []
    assert {p.name: p.read_bytes() for p in out.glob("*.csv")} == before


def test_the_forming_last_bar_is_not_compared(tmp_path: Path) -> None:
    """末尾（形成中）バーは probe で完結を断定できない＝食い違っても不一致と呼ばない。"""
    m1, out = _build(tmp_path)
    path = out / "ref_1D.csv"
    lines = path.read_text().splitlines(keepends=True)
    fields = lines[-1].rstrip("\n").split(",")
    fields[4] = str(float(fields[4]) + 9.0)
    lines[-1] = ",".join(fields) + "\n"
    path.write_text("".join(lines))

    assert rb.heal_tail_gaps(m1, ["1D"], out, "ref", probe_rows=60 * 24 * 12) == []


def test_an_uncoverable_timeframe_is_not_judged(tmp_path: Path) -> None:
    """probe が確定 period を 1 本も覆えない TF は判定しない（検査不能≠合格・誤修復もしない）。"""
    m1, out = _build(tmp_path, days=12, tfs=("1M",))
    path = out / "ref_1M.csv"
    _drop_data_line(path, offset_from_end=1)
    broken = path.read_bytes()

    # 12 日ぶんの probe では月周期の確定バーを覆えない → 触らない。
    assert rb.heal_tail_gaps(m1, ["1M"], out, "ref", probe_rows=60 * 24 * 12) == []
    assert path.read_bytes() == broken


def test_healing_does_not_advance_the_shared_incremental_state(tmp_path: Path) -> None:
    """自己修復は state（全 TF 共通の増分カーソル）を進めない。

    進めると、同じループでまだ増分処理していない他 TF の新規行が「処理済み」と見なされ、
    ISSUE-488 と同型の恒久欠落を自己修復自身が作る。
    """
    m1, out = _build(tmp_path)
    state_path = out / "rollup_state.json"
    state_path.write_text(json.dumps({"last_processed_ts": "2020-01-05 00:00:00"}))
    _drop_data_line(out / "ref_1D.csv", offset_from_end=3)

    rb.heal_tail_gaps(m1, ["1D"], out, "ref", probe_rows=60 * 24 * 12)

    assert json.loads(state_path.read_text()) == {"last_processed_ts": "2020-01-05 00:00:00"}


# --------------------------------------------------------------------------- #
# 計算量（絶対命令 §4.1）
# --------------------------------------------------------------------------- #
def test_the_probe_is_read_once_and_bounded_by_probe_rows(tmp_path: Path, monkeypatch) -> None:
    """probe の読み取りは 1 回・要求行数は M1 全長に依存しない（2 点固定）。"""
    reads_by_days = {}
    original = tail_reader.read_tail

    for days in (12, 24):
        base = tmp_path / f"d{days}"
        base.mkdir()
        m1, out = _build(base, days=days)
        reads: "list[int]" = []

        def counted(path, rows, *a, _reads=reads, **k):
            _reads.append(int(rows))
            return original(path, rows, *a, **k)

        monkeypatch.setattr(rb.tail_reader, "read_tail", counted)
        healed = rb.heal_tail_gaps(m1, ["1D", "1W"], out, "ref", probe_rows=3000)
        monkeypatch.setattr(rb.tail_reader, "read_tail", original)

        assert healed == []
        # probe（M1）の読みは 1 回（全 TF 共有）。残りはロールアップ末尾の突合読み（TF ごと）。
        probe_reads = [rows for rows in reads if rows == 3000]
        assert len(probe_reads) == 1
        reads_by_days[days] = reads

    assert reads_by_days[12] == reads_by_days[24]   # M1 が伸びても発行は変わらない
