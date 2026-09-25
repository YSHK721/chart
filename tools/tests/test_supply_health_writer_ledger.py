"""出荷の供給台帳が、実際の書き手と食い違わないことの検定（ISSUE-526 段 3）。

なぜ要るか: 束ねた報告は「どの錠が、どのプログラムの書き手か」を台帳で持つ。錠の名前と
プログラム名の権威は**書いている側**（供給常駐）に在るので、台帳はその写しである。写しは
必ず取り残される（同じ語を手書きで複製すれば、片方だけ直される日が来る）。食い違ったときに
起きるのは、在否が常に不在または常に判定不能になり、**監視が静かに死ぬ**ことである。

依存の向きは保つ: `marketdata` は `tools` を import しない（上位層への逆流になる）。写しを
置いたうえで、一致だけをここで機械的に突き合わせる。

構造: Arrange-Act-Assert（AAA）。
"""
from __future__ import annotations

from pathlib import Path

import tools.live_tick_watch as live_watch
import tools.mt5_tick_watch as mt5_watch
from marketdata import supply_health

#: 供給の名前（台帳のベンダ欄の値） → その供給を書いている常駐モジュール。
_WRITERS = {
    "dukascopy": live_watch,
    "mt5": mt5_watch,
}


def _declared_lock_filename(module) -> str:
    """その常駐が獲得する錠の名前（公開・非公開どちらの名前で持っていても引く）。"""
    return getattr(
        module, "WRITER_LOCK_FILENAME", getattr(module, "_WRITER_LOCK_FILENAME", ""))


def test_every_shipped_supply_names_the_lock_its_writer_actually_takes():
    """台帳の錠の名前が、その常駐が実際に獲得する錠と一致する。"""
    # Arrange / Act
    declared = {
        name: supply_health.SUPPLIES[name].lock_filename for name in _WRITERS
    }
    actual = {name: _declared_lock_filename(module) for name, module in _WRITERS.items()}

    # Assert
    assert declared == actual


def test_every_shipped_supply_names_the_program_its_writer_actually_runs():
    """台帳の期待プログラム名が、その常駐の実ファイル名と一致する。

    在否の照合はコマンド行にこの語が現れるかで決まる（PID 再利用への防護）。取り違えると、
    稼働中の常駐が不在と読まれる。
    """
    # Arrange / Act
    declared = {name: supply_health.SUPPLIES[name].program for name in _WRITERS}
    actual = {name: Path(module.__file__).name for name, module in _WRITERS.items()}

    # Assert
    assert declared == actual


def test_the_shipped_ledger_covers_every_writer_and_nothing_else():
    """台帳の供給と、実在する書き手が 1 対 1（増えた側だけ直す形にしない）。"""
    # Arrange / Act
    shipped = sorted(supply_health.SUPPLIES)

    # Assert
    assert shipped == sorted(_WRITERS)
