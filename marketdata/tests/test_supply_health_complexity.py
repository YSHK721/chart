"""束ねた報告の**計算量テスト**（CLAUDE.md 絶対命令 §4.1・ISSUE-526 段 3）。

固定するのは出力の正しさではなく **無駄の不在**である。束ねる実装は出力（3 つの区別）が正しい
まま、いくらでも重くできる: 供給ごとに全系列の先端を読み直しても、系列ごとに全部の錠を読み
直しても、報告の中身は同じ値になる。状態検証では原理的に落ちないので、ここでは**発行した読みの
数と読んだバイト数**を数える。

ここで固定する不変量:
    1. 1 回の報告で、同じ対象を二度読まない（発行した読み − 読んだ対象 = 0）。
    2. M1 の行数を 2 桁変えても、発行回数も読んだバイト数も変わらない（末尾だけを読む）。
    3. 系列を 1 つ足しても、系列あたりの発行は増えない（供給 × 系列で二乗にならない）。
    4. 供給を 1 つ足しても、供給あたりの発行は増えない。
    5. 報告を n 回求めた発行は、n だけで決まる（1 回の中で観測を重ねない）。

**回数そのものは期待値に焼き込まない**（焼き込むと浪費が仕様へ昇格する）。固定するのは
「出力量だけで決まること」と「入力量で増えないこと」である。

継ぎ目の先例: marketdata/tests/test_supply_freshness_complexity.py:33-88

構造: Arrange-Act-Assert（AAA）。
"""
from __future__ import annotations

import os

import pandas as pd

from marketdata import dataset_registry, supply_health, tail_reader

_HEADER = "date,open,high,low,close,volume,up,dn"

#: 2 点の入力量（M1 の行数）。比は 100 倍＝2 桁。どちらも逆シークの読み取りブロックより大きい。
_SMALL_ROWS = 2_000
_LARGE_ROWS = 200_000

#: どのプロセスのコマンド行にも現れない語（在否の値は本検定の関心ではない）。
_FOREIGN_PROGRAM = "supply_health_complexity_no_such_program"

#: 2 点の系列数（同一銘柄の兄弟系列）。
_TWO_REFS = ("jp225_mt5", "jp225_mt5_spread")
_THREE_REFS = _TWO_REFS + ("jp225_tick",)


class _ReadSpy:
    """報告 1 回が**何を何回読み、何バイト受け取ったか**を数える（Test Spy）。

    継ぎ目は 2 つ。先端の末尾読み（marketdata.tail_reader の read_tail）と、書き手の在否
    （marketdata.supply_health が呼ぶ writer_presence）である。どちらも「対象ごとに 1 回」で
    足りるので、対象の集合と発行回数を別々に数えれば無駄が差として出る。
    """

    def __init__(self, monkeypatch) -> None:
        self.reset()
        real_read_tail = tail_reader.read_tail
        real_presence = supply_health.writer_presence
        real_open = open
        spy = self

        def counting_read_tail(path, n_rows):
            spy.tail_calls += 1
            spy.tail_targets.add(str(path))
            return real_read_tail(path, n_rows)

        def counting_presence(data_dir, *, filename, expect_cmdline_contains):
            spy.presence_calls += 1
            spy.presence_targets.add(filename)
            return real_presence(
                data_dir,
                filename=filename,
                expect_cmdline_contains=expect_cmdline_contains,
            )

        class _CountingFile:
            def __init__(self, handle) -> None:
                self._handle = handle

            def __enter__(self):
                self._handle.__enter__()
                return self

            def __exit__(self, *exc):
                return self._handle.__exit__(*exc)

            def read(self, *args):
                data = self._handle.read(*args)
                spy.read_bytes += len(data)
                return data

            def readline(self, *args):
                data = self._handle.readline(*args)
                spy.read_bytes += len(data)
                return data

            def seek(self, *args):
                return self._handle.seek(*args)

            def tell(self):
                return self._handle.tell()

        def counting_open(path, mode="r", *args, **kwargs):
            return _CountingFile(real_open(path, mode, *args, **kwargs))

        monkeypatch.setattr(tail_reader, "read_tail", counting_read_tail)
        monkeypatch.setattr(tail_reader, "open", counting_open, raising=False)
        monkeypatch.setattr(supply_health, "writer_presence", counting_presence)

    def reset(self) -> None:
        """次の観測を独立に数える（2 点を比べるため、点ごとに 0 から数える）。"""
        self.tail_calls = 0
        self.presence_calls = 0
        self.read_bytes = 0
        self.tail_targets: "set[str]" = set()
        self.presence_targets: "set[str]" = set()

    def waste(self) -> "tuple[int, int]":
        """（余分な先端読み, 余分な錠読み）。どちらも 0 が無駄の不在である。"""
        return (
            self.tail_calls - len(self.tail_targets),
            self.presence_calls - len(self.presence_targets),
        )


def _supplies(names) -> dict:
    """names の供給だけを持つ台帳（錠の名前は供給ごとに別）。"""
    return {
        name: supply_health.Supply(
            lock_filename=f"{name}_writer.lock", program=_FOREIGN_PROGRAM)
        for name in names
    }


def _write_lock(data_dir, filename: str) -> None:
    """錠ファイルを書き手と同じ書式で置く。"""
    (data_dir / filename).write_text(
        f"{os.getpid()} 2026-09-25T03:30:00.000000+00:00\n", encoding="utf-8")


def _write_m1(data_dir, ref: str, rows: int) -> None:
    """ref の M1 CSV を rows 行で書く（末尾は rows に依らず同じ分）。"""
    end = pd.Timestamp("2026-09-24 22:26:00")
    index = pd.date_range(end=end, periods=rows, freq="min")
    body = "\n".join(f"{ts:%Y-%m-%d %H:%M:%S},1,2,0.5,1.5,10,6,4" for ts in index)
    series = dataset_registry.series_of(ref)
    (data_dir / f"{series}_m1.csv").write_text(
        _HEADER + "\n" + body + "\n", encoding="utf-8")


def _arrange(data_dir, names, refs, rows: int):
    """names の供給と refs の M1 を置いた watch を返す。"""
    supplies = _supplies(names)
    for supply in supplies.values():
        _write_lock(data_dir, supply.lock_filename)
    for ref in refs:
        _write_m1(data_dir, ref, rows)
    return supply_health.SupplyHealthWatch(
        supplies=supplies, refs=refs, data_dir=data_dir)


def _report_once(watch, spy: _ReadSpy) -> None:
    """1 回だけ報告を求める（spy は 0 から数え直す）。"""
    spy.reset()
    watch.report()


def test_one_report_reads_no_target_more_than_once(tmp_path, monkeypatch):
    """発行した読み − 読んだ対象 = 0（同じ先端も同じ錠も二度読まない）。"""
    # Arrange
    watch = _arrange(tmp_path, ("dukascopy", "mt5"), _THREE_REFS, rows=10)

    # Act
    spy = _ReadSpy(monkeypatch)
    _report_once(watch, spy)

    # Assert
    assert min(spy.tail_calls, spy.presence_calls) > 0, (
        "Spy が何も数えていない（計算量の検定が恒真に退化している）")
    assert spy.waste() == (0, 0)


def test_the_reads_do_not_grow_with_the_number_of_m1_rows(tmp_path, monkeypatch):
    """入力（M1 の行数）を 2 桁変えても、発行回数も読んだバイト数も変わらない。"""
    # Arrange
    small_dir = tmp_path / "small"
    large_dir = tmp_path / "large"
    small_dir.mkdir()
    large_dir.mkdir()
    small_watch = _arrange(
        small_dir, ("dukascopy", "mt5"), _THREE_REFS, rows=_SMALL_ROWS)
    large_watch = _arrange(
        large_dir, ("dukascopy", "mt5"), _THREE_REFS, rows=_LARGE_ROWS)

    # Act
    spy = _ReadSpy(monkeypatch)
    _report_once(small_watch, spy)
    small = (spy.tail_calls, spy.presence_calls, spy.read_bytes)
    _report_once(large_watch, spy)
    large = (spy.tail_calls, spy.presence_calls, spy.read_bytes)

    # Assert
    assert min(small) > 0, "Spy が何も数えていない（計算量の検定が恒真に退化している）"
    assert large == small


def test_adding_a_series_does_not_raise_the_issuance_per_series(tmp_path, monkeypatch):
    """系列を 1 つ足しても、系列あたりの先端読みは増えない。"""
    # Arrange
    two_dir = tmp_path / "two"
    three_dir = tmp_path / "three"
    two_dir.mkdir()
    three_dir.mkdir()
    two_watch = _arrange(two_dir, ("dukascopy", "mt5"), _TWO_REFS, rows=10)
    three_watch = _arrange(three_dir, ("dukascopy", "mt5"), _THREE_REFS, rows=10)

    # Act
    spy = _ReadSpy(monkeypatch)
    _report_once(two_watch, spy)
    pair_calls = spy.tail_calls
    _report_once(three_watch, spy)
    trio_calls = spy.tail_calls

    # Assert
    assert pair_calls > 0, "Spy が何も数えていない（計算量の検定が恒真に退化している）"
    assert pair_calls * len(_THREE_REFS) == trio_calls * len(_TWO_REFS)


def test_adding_a_supply_does_not_raise_the_issuance_per_supply(tmp_path, monkeypatch):
    """供給を 1 つ足しても、供給あたりの錠読みは増えない。"""
    # Arrange
    one_dir = tmp_path / "one"
    two_dir = tmp_path / "two"
    one_dir.mkdir()
    two_dir.mkdir()
    one_watch = _arrange(one_dir, ("mt5",), _THREE_REFS, rows=10)
    two_watch = _arrange(two_dir, ("dukascopy", "mt5"), _THREE_REFS, rows=10)

    # Act
    spy = _ReadSpy(monkeypatch)
    _report_once(one_watch, spy)
    single = spy.presence_calls
    _report_once(two_watch, spy)
    pair = spy.presence_calls

    # Assert
    assert single > 0, "Spy が何も数えていない（計算量の検定が恒真に退化している）"
    assert single * 2 == pair * 1


def test_the_issuance_is_determined_only_by_how_many_reports_are_asked(
    tmp_path, monkeypatch,
):
    """報告を 3 回求めた発行は 1 回ぶんの 3 倍（1 回の中で観測を重ねない）。"""
    # Arrange
    watch = _arrange(tmp_path, ("dukascopy", "mt5"), _THREE_REFS, rows=10)

    # Act
    spy = _ReadSpy(monkeypatch)
    _report_once(watch, spy)
    once = (spy.tail_calls, spy.presence_calls)
    spy.reset()
    watch.report()
    watch.report()
    watch.report()
    thrice = (spy.tail_calls, spy.presence_calls)

    # Assert
    assert min(once) > 0, "Spy が何も数えていない（計算量の検定が恒真に退化している）"
    assert (once[0] * 3, once[1] * 3) == thrice
