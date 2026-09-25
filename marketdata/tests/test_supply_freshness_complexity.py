"""供給の鮮度判定に関する**計算量テスト**（CLAUDE.md 絶対命令 §4.1・ISSUE-526 段 1）。

固定するのは出力の正しさではなく **無駄の不在**である。鮮度の判定は出力（判定結果）が
正しいまま、いくらでも重くできる: 系列ごとに CSV を全読みしても、二度読んでも、判定は同じ
値になる。状態検証では原理的に落ちないので、ここでは**発行した読みの数とバイト数**を数える。

ここで固定する不変量:
    1. 1 回の判定で読んだ系列の数 − 判定に使った系列の数 = 0（余分に読まない）。
    2. M1 の行数を 2 桁変えても、発行回数も読んだバイト数も変わらない（末尾だけを読む）。
    3. 系列を 1 つ足しても、系列あたりの発行は増えない（系列間で二乗にならない）。

**回数そのものは期待値に焼き込まない**（焼き込むと浪費が仕様へ昇格する）。固定するのは
「出力量だけで決まること」と「入力量で増えないこと」である。

継ぎ目の先例: marketdata/tests/test_serving_cache_tail_complexity.py:36-48

構造: Arrange-Act-Assert（AAA）。
"""
from __future__ import annotations

import pandas as pd

from marketdata import dataset_registry, supply_freshness, tail_reader

_HEADER = "date,open,high,low,close,volume,up,dn"

#: 2 点の入力量（行数）。比は 100 倍＝2 桁。どちらも逆シークの読み取りブロックより大きいので、
#: 「末尾だけ読む」なら 2 点で読んだバイト数は一致する（全読みなら 100 倍差になる）。
_SMALL_ROWS = 2_000
_LARGE_ROWS = 200_000


class _TailSpy:
    """末尾読みの**発行回数**と**読んだバイト数**を数える（Test Spy）。

    回数は marketdata.tail_reader の read_tail の呼び出し数、バイト数はその内側で
    ファイルから受け取った実バイト数（ヘッダ行＋逆シークのブロック）である。
    """

    def __init__(self, monkeypatch) -> None:
        self.reset()
        real_read_tail = tail_reader.read_tail
        real_open = open
        spy = self

        def counting_read_tail(path, n_rows):
            spy.calls += 1
            return real_read_tail(path, n_rows)

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

    def reset(self) -> None:
        """次の観測を独立に数える（2 点を比べるため、点ごとに 0 から数える）。"""
        self.calls = 0
        self.read_bytes = 0


def _write_m1(data_dir, series: str, rows: int) -> None:
    """``<data_dir>/<series>_m1.csv`` を rows 行で書く（末尾は rows に依らず同じ分）。"""
    end = pd.Timestamp("2026-09-24 22:26:00")
    index = pd.date_range(end=end, periods=rows, freq="min")
    body = "\n".join(f"{ts:%Y-%m-%d %H:%M:%S},1,2,0.5,1.5,10,6,4" for ts in index)
    (data_dir / f"{series}_m1.csv").write_text(
        _HEADER + "\n" + body + "\n", encoding="utf-8")


def _write_torn_m1(data_dir, series: str, rows: int) -> None:
    """健全な rows 行のあとに、列数が崩れた 1 行を足した M1 を書く（追記途中の再現）。"""
    _write_m1(data_dir, series, rows)
    path = data_dir / f"{series}_m1.csv"
    path.write_text(
        path.read_text(encoding="utf-8")
        + "2026-09-24 22:30:00,1,2,0.5,1.5,10,6,4,9,9\n",
        encoding="utf-8",
    )


def _observe_once(data_dir, refs, spy) -> dict:
    """1 回だけ観測して判定結果を返す（spy は 0 から数え直す）。"""
    watch = supply_freshness.M1FreshnessWatch(refs, data_dir=data_dir)
    spy.reset()
    return watch.observe()


def test_one_judgement_reads_no_series_more_than_it_judges(tmp_path, monkeypatch):
    """発行した読み − 判定に使った系列 = 0（余分に読んでいない）。"""
    refs = ("jp225_mt5", "jp225_mt5_spread", "jp225_tick")
    for ref in refs:
        _write_m1(tmp_path, dataset_registry.series_of(ref), rows=10)

    spy = _TailSpy(monkeypatch)
    verdicts = _observe_once(tmp_path, refs, spy)

    assert spy.calls - len(verdicts) == 0


def test_the_reads_do_not_grow_with_the_number_of_m1_rows(tmp_path, monkeypatch):
    """入力（M1 の行数）を 2 桁変えても、発行回数も読んだバイト数も変わらない。"""
    refs = ("jp225_mt5", "jp225_mt5_spread")
    small_dir = tmp_path / "small"
    large_dir = tmp_path / "large"
    for directory, rows in ((small_dir, _SMALL_ROWS), (large_dir, _LARGE_ROWS)):
        directory.mkdir()
        for ref in refs:
            _write_m1(directory, dataset_registry.series_of(ref), rows=rows)

    spy = _TailSpy(monkeypatch)
    _observe_once(small_dir, refs, spy)
    small = (spy.calls, spy.read_bytes)
    _observe_once(large_dir, refs, spy)
    large = (spy.calls, spy.read_bytes)

    assert large == small


def test_a_torn_tail_costs_no_more_reads_than_a_healthy_tail(tmp_path, monkeypatch):
    """末尾が壊れていても、読みの発行もバイト数も健全な末尾と変わらない（読み直さない）。

    壊れた末尾を安全に扱う素直な誤りは「例外が出たら全読みでやり直す」である。出力
    （先端が読めないという値）は正しいまま、費用だけが 2 桁跳ねるので状態検証では落ちない。
    """
    ref = "jp225_mt5"
    series = dataset_registry.series_of(ref)
    healthy_dir = tmp_path / "healthy"
    torn_small_dir = tmp_path / "torn_small"
    torn_large_dir = tmp_path / "torn_large"
    healthy_dir.mkdir()
    _write_m1(healthy_dir, series, rows=_SMALL_ROWS)
    for directory, rows in ((torn_small_dir, _SMALL_ROWS), (torn_large_dir, _LARGE_ROWS)):
        directory.mkdir()
        _write_torn_m1(directory, series, rows=rows)

    spy = _TailSpy(monkeypatch)
    _observe_once(healthy_dir, (ref,), spy)
    healthy = (spy.calls, spy.read_bytes)
    _observe_once(torn_small_dir, (ref,), spy)
    torn_small = (spy.calls, spy.read_bytes)
    _observe_once(torn_large_dir, (ref,), spy)
    torn_large = (spy.calls, spy.read_bytes)

    assert (torn_small, torn_large) == (healthy, healthy)


def test_adding_a_series_does_not_raise_the_issuance_per_series(tmp_path, monkeypatch):
    """系列を 1 つ足しても、系列あたりの発行は増えない（系列間で二乗にならない）。"""
    two = ("jp225_mt5", "jp225_mt5_spread")
    three = two + ("jp225_tick",)
    for ref in three:
        _write_m1(tmp_path, dataset_registry.series_of(ref), rows=10)

    spy = _TailSpy(monkeypatch)
    _observe_once(tmp_path, two, spy)
    pair_calls = spy.calls
    _observe_once(tmp_path, three, spy)
    trio_calls = spy.calls

    assert pair_calls * len(three) == trio_calls * len(two)
