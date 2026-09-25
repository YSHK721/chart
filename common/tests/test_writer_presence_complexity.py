"""書き手の在否を読む口の**計算量テスト**（CLAUDE.md 絶対命令 §4.1・ISSUE-526 段 2）。

固定するのは出力の正しさではなく **無駄の不在**である。在否の判定は出力（在／不在／判定不能）が
正しいまま、いくらでも重くできる: 錠ファイルを全読みしても、書き手ごとに他の書き手の錠まで
読み直しても、答えは同じ値になる。状態検証では原理的に落ちないので、ここでは**発行した読みの
数と読んだバイト数**を数える。

ここで固定する不変量:
    1. 錠ファイルは**先頭だけ**読む。中身を 4 桁大きくしても読んだバイト数は変わらない
       （全読みなら 4 桁差になる）。
    2. 書き手を 1 本から 2 本へ増やしても、**書き手あたりの発行は増えない**
       （書き手どうしで二乗にならない）。

**回数そのものは期待値に焼き込まない**（焼き込むと浪費が仕様へ昇格する）。固定するのは
「出力量だけで決まること」と「入力量で増えないこと」である。

継ぎ目の先例: common/tests/test_package_surface_purity.py の計算量テスト。

構造: Arrange-Act-Assert（AAA）。
"""
from __future__ import annotations

import os
from pathlib import Path

from common import writer_lock
from common.writer_lock import writer_presence

#: 2 点の入力量（錠ファイルへ足す残骸の長さ・バイト）。比は 2,000 倍＝3 桁。
#:
#: 小さい側も読み口が読む先頭の量より大きくしておく。上限より短い中身だと「読んだ量＝中身の
#: 長さ」になり、2 点の比較が入力量の効果を測らなくなる。
_SMALL_PADDING = 100
_LARGE_PADDING = 200_000

#: どのプロセスのコマンド行にも現れない語（在否の値は本検定の関心ではない）。
_FOREIGN_MARKER = "writer_presence_complexity_no_such_program"


class _ReadSpy:
    """在否の観測が**何回開いて何バイト読んだか**を数える（Test Spy）。

    読み口が開く先は錠ファイルとプロセスのコマンド行の 2 種である。どちらも同じ ``open`` を
    通るため、1 つの Spy で発行回数（開いた数）と受け取った実バイト数を数えられる。
    """

    def __init__(self, monkeypatch) -> None:
        self.reset()
        real_open = open
        spy = self

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
            spy.opens += 1
            return _CountingFile(real_open(path, mode, *args, **kwargs))

        monkeypatch.setattr(writer_lock, "open", counting_open, raising=False)

    def reset(self) -> None:
        """次の観測を独立に数える（2 点を比べるため、点ごとに 0 から数える）。"""
        self.opens = 0
        self.read_bytes = 0


def _write_lock_file(data_dir: Path, filename: str, padding: int) -> None:
    """錠ファイルを書き手と同じ書式で置き、その後ろへ padding バイトの残骸を足す。"""
    head = f"{os.getpid()} 2026-09-25T03:30:00.000000+00:00\n"
    (data_dir / filename).write_text(head + "x" * padding, encoding="utf-8")


def _observe(data_dir: Path, filenames, spy: _ReadSpy) -> "list[str]":
    """錠ごとに 1 回ずつ観測する（spy は 0 から数え直す）。"""
    spy.reset()
    return [
        writer_presence(
            data_dir, filename=name, expect_cmdline_contains=_FOREIGN_MARKER)
        for name in filenames
    ]


def test_one_observation_reads_only_the_head_of_the_lock_file(
    tmp_path: Path, monkeypatch,
) -> None:
    """錠ファイルの中身を 4 桁大きくしても、開いた数も読んだバイト数も変わらない。"""
    # Arrange
    small_dir = tmp_path / "small"
    large_dir = tmp_path / "large"
    for directory, padding in ((small_dir, _SMALL_PADDING), (large_dir, _LARGE_PADDING)):
        directory.mkdir()
        _write_lock_file(directory, "one_writer.lock", padding)

    # Act
    spy = _ReadSpy(monkeypatch)
    _observe(small_dir, ("one_writer.lock",), spy)
    small = (spy.opens, spy.read_bytes)
    _observe(large_dir, ("one_writer.lock",), spy)
    large = (spy.opens, spy.read_bytes)

    # Assert
    assert min(small) > 0, "Spy が何も数えていない（計算量の検定が恒真に退化している）"
    assert large == small


def test_adding_a_writer_does_not_raise_the_issuance_per_writer(
    tmp_path: Path, monkeypatch,
) -> None:
    """書き手を 1 本から 2 本へ増やしても、書き手あたりの発行は増えない。"""
    # Arrange
    one = ("first_writer.lock",)
    two = one + ("second_writer.lock",)
    for name in two:
        _write_lock_file(tmp_path, name, _SMALL_PADDING)

    # Act
    spy = _ReadSpy(monkeypatch)
    _observe(tmp_path, one, spy)
    single = spy.opens
    _observe(tmp_path, two, spy)
    pair = spy.opens

    # Assert
    assert single > 0, "Spy が何も数えていない（計算量の検定が恒真に退化している）"
    assert single * len(two) == pair * len(one)
