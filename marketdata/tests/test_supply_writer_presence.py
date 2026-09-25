"""稼働中の供給常駐の在否を、錠の残骸から**読むだけ**で言えるかの実測（ISSUE-526 段 2）。

読み取りのみ。1 バイトも書かない（錠も止めない・起動もしない）。

PID は起動ごとに変わるため、**値を焼き込まない**。錠ファイルから PID を読み、その PID の
プロセスのコマンド行に「その常駐のプログラム」が現れるかで照合する。照合を省くと、
たまたま同じ PID を得た無関係なプロセスを在と誤認する（PID 再利用）。

期待する側（どの錠が、どのプログラムの書き手か）は**呼出側が与える面**である
（錠を獲得する側が、錠の名前と拒否の案内文を呼出側から受けるのと同じ）。

構造: Arrange-Act-Assert（AAA）。
"""
from __future__ import annotations

from common.writer_lock import WRITER_PRESENT, writer_presence
from marketdata.paths import DATA_DIR

#: 錠の名前 → その錠を持つ常駐のプログラム（コマンド行に現れる語）。
#: 錠の名前は書き手側（供給常駐）が与えている面であり、ここはその読み手としての期待である。
_EXPECTED_WRITERS = {
    "mt5_tick_watch.lock": "mt5_tick_watch.py",
    "live_tick_watch.lock": "live_tick_watch.py",
}


def test_every_running_supply_writer_reads_as_present():
    """いま稼働している 2 本の供給常駐が、どちらも在と出る（合格の目印）。"""
    # Arrange / Act
    verdicts = {
        filename: writer_presence(
            DATA_DIR, filename=filename, expect_cmdline_contains=program)
        for filename, program in _EXPECTED_WRITERS.items()
    }

    # Assert
    assert verdicts == {
        filename: WRITER_PRESENT for filename in _EXPECTED_WRITERS
    }


def test_no_supply_writer_is_mistaken_for_another_program():
    """互いのプログラムを期待に据えると在にならない（照合が実際に効いている）。

    照合が名ばかりなら、錠を取り違えても在と出てしまう。
    """
    # Arrange
    swapped = {
        "mt5_tick_watch.lock": _EXPECTED_WRITERS["live_tick_watch.lock"],
        "live_tick_watch.lock": _EXPECTED_WRITERS["mt5_tick_watch.lock"],
    }

    # Act
    verdicts = {
        filename: writer_presence(
            DATA_DIR, filename=filename, expect_cmdline_contains=program)
        for filename, program in swapped.items()
    }

    # Assert
    assert WRITER_PRESENT not in set(verdicts.values())
