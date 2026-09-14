"""増分カーソル規約の検定（ISSUE-447 段階 1 / 検定 N-2・B-1・E-6）。

供給の連続性は「最後に保存したティック以降を引き続く」ことで成立する。MT5 の ``time_msc`` は
**同一 ms に複数ティック**が並びうるため、再開点を「ms より後」にすると同一 ms の残りを取り
こぼす。よって窓の下端は ``cursor_ms`` を**含み**、重複して返る境界行は
「値まで一致した分だけ」落とす。一致しない場合は静かに続けず Fail-Stop する
（同一 ms 内の返却順序の安定性は未検証＝V-2 のため、仮定を置かない）。
"""
from __future__ import annotations

import pytest

from marketdata.mt5_ticks import cursor as cur
from marketdata.mt5_ticks.cursor import Cursor, CursorContractError


def _rows(*specs):
    """``(ms, bid, ask)`` 行の並びを作る小道具。"""
    return [(int(ms), float(bid), float(ask)) for ms, bid, ask in specs]


# =====================================================================
# request_window: 下端は含む
# =====================================================================

def test_request_window_includes_the_cursor_millisecond_itself():
    """下端を含むのは「同一 ms の残り」を取りこぼさないため（正しさに必要な入力）。"""
    c = Cursor(cursor_ms=1_700_000_000_000, boundary_rows=())
    assert cur.request_window(c) == (1_700_000_000_000, None)


def test_request_window_has_no_upper_bound():
    """上端は開いている（端末が持つ最新まで取る）。"""
    _, to_ms = cur.request_window(Cursor(cursor_ms=1, boundary_rows=()))
    assert to_ms is None


# =====================================================================
# N-2 正常系
# =====================================================================

def test_absorb_returns_only_the_new_rows_and_advances_to_the_last_row():
    """N-2: 新着のみ返り、``next_cursor`` が最終行を指す。"""
    # Arrange: 1000 を保存済み。応答は境界行 1 本 + 新着 2 本。
    c = Cursor(cursor_ms=1000, boundary_rows=((1000, 1.0, 2.0),))
    rows = _rows((1000, 1.0, 2.0), (1001, 1.1, 2.1), (1002, 1.2, 2.2))
    # Act
    got = cur.absorb(c, rows)
    # Assert
    assert got.new_rows == _rows((1001, 1.1, 2.1), (1002, 1.2, 2.2))
    assert got.dropped == 1
    assert got.next_cursor.cursor_ms == 1002
    assert got.next_cursor.boundary_rows == ((1002, 1.2, 2.2),)


def test_absorb_from_a_cold_start_cursor_keeps_every_row():
    """コールドスタート（境界行なし）は 1 行も落とさない。"""
    c = Cursor(cursor_ms=1000, boundary_rows=())
    got = cur.absorb(c, _rows((1000, 1.0, 2.0), (1001, 1.1, 2.1)))
    assert got.dropped == 0
    assert len(got.new_rows) == 2


def test_absorb_on_an_empty_response_leaves_the_cursor_untouched():
    """0 行応答でカーソルが動かない（巻き戻しも前進もしない）。"""
    c = Cursor(cursor_ms=1000, boundary_rows=((1000, 1.0, 2.0),))
    got = cur.absorb(c, [])
    assert got.new_rows == []
    assert got.dropped == 0
    assert got.next_cursor == c


def test_absorb_keeps_the_cursor_when_only_the_boundary_rows_come_back():
    """新着ゼロ（境界行だけ返る）でもカーソルは同じ位置に留まる。"""
    c = Cursor(cursor_ms=1000, boundary_rows=((1000, 1.0, 2.0),))
    got = cur.absorb(c, _rows((1000, 1.0, 2.0)))
    assert got.new_rows == []
    assert got.dropped == 1
    assert got.next_cursor.cursor_ms == 1000
    assert got.next_cursor.boundary_rows == ((1000, 1.0, 2.0),)


def test_cursor_is_never_rewound():
    """カーソル ms は単調非減少（巻き戻し禁止）。"""
    c = Cursor(cursor_ms=1000, boundary_rows=((1000, 1.0, 2.0),))
    for rows in ([], _rows((1000, 1.0, 2.0)), _rows((1000, 1.0, 2.0), (1005, 1.0, 2.0))):
        assert cur.absorb(c, rows).next_cursor.cursor_ms >= c.cursor_ms


# =====================================================================
# B-1 境界: 同一 ms に複数ティック
# =====================================================================

def test_three_ticks_on_the_same_millisecond_are_dropped_and_only_new_ones_remain():
    """B-1: 同一 ms に 3 行 → 境界再取得で 3 行落ち、新着だけ残る。"""
    # Arrange: 1000 ms に 3 本すべて保存済み。
    saved = ((1000, 1.0, 2.0), (1000, 1.1, 2.1), (1000, 1.2, 2.2))
    c = Cursor(cursor_ms=1000, boundary_rows=saved)
    rows = _rows(*saved, (1001, 1.3, 2.3))
    # Act
    got = cur.absorb(c, rows)
    # Assert
    assert got.dropped == 3
    assert got.new_rows == _rows((1001, 1.3, 2.3))


def test_extra_ticks_arriving_on_the_boundary_millisecond_are_kept_as_new():
    """境界 ms に**後から**増えた行は新着として残す（取りこぼさない）。"""
    c = Cursor(cursor_ms=1000, boundary_rows=((1000, 1.0, 2.0),))
    got = cur.absorb(c, _rows((1000, 1.0, 2.0), (1000, 1.5, 2.5)))
    assert got.dropped == 1
    assert got.new_rows == _rows((1000, 1.5, 2.5))
    # 境界行は「その ms の全行」なので 2 本に育つ。
    assert got.next_cursor.boundary_rows == ((1000, 1.0, 2.0), (1000, 1.5, 2.5))


# =====================================================================
# E-6 異常系: Fail-Stop
# =====================================================================

def test_boundary_row_value_mismatch_is_fail_stop():
    """E-6: 境界行の値が一致しない → ``CursorContractError``（黙って続けない）。"""
    c = Cursor(cursor_ms=1000, boundary_rows=((1000, 1.0, 2.0),))
    with pytest.raises(CursorContractError):
        cur.absorb(c, _rows((1000, 9.9, 9.9), (1001, 1.1, 2.1)))


def test_missing_boundary_rows_are_fail_stop():
    """保存済みより境界行が少ない応答は契約違反（欠落を黙認しない）。"""
    c = Cursor(cursor_ms=1000, boundary_rows=((1000, 1.0, 2.0), (1000, 1.1, 2.1)))
    with pytest.raises(CursorContractError):
        cur.absorb(c, _rows((1000, 1.0, 2.0), (1001, 1.2, 2.2)))


def test_rows_below_the_cursor_are_fail_stop():
    """窓の下端より前の行が混ざるのは契約違反。"""
    c = Cursor(cursor_ms=1000, boundary_rows=())
    with pytest.raises(CursorContractError):
        cur.absorb(c, _rows((999, 1.0, 2.0)))


def test_non_ascending_rows_are_fail_stop():
    """昇順でない応答は契約違反（並べ替えて救わない）。"""
    c = Cursor(cursor_ms=1000, boundary_rows=())
    with pytest.raises(CursorContractError):
        cur.absorb(c, _rows((1001, 1.0, 2.0), (1000, 1.0, 2.0)))


# =====================================================================
# 復元は「ジャーナルが正」の一経路のみ
# =====================================================================

def test_from_journal_tail_restores_the_max_ms_and_all_rows_on_it():
    """``from_journal_tail`` が最大 ms とその ms の**全行**を復元する。"""
    tail = _rows((999, 0.9, 1.9), (1000, 1.0, 2.0), (1000, 1.1, 2.1))
    c = cur.from_journal_tail(tail)
    assert c.cursor_ms == 1000
    assert c.boundary_rows == ((1000, 1.0, 2.0), (1000, 1.1, 2.1))


def test_from_journal_tail_on_an_empty_journal_has_no_implicit_default():
    """空ジャーナルからは復元できない（``now-30 分`` 等の暗黙既定を作らない）。"""
    assert cur.from_journal_tail([]) is None


def test_the_module_has_no_time_based_default_cursor():
    """暗黙既定を生む余地（時刻の参照）を持たない＝コールドスタートは ``--from`` 必須。"""
    source = (cur.__file__)
    text = open(source, encoding="utf-8").read()
    code = "\n".join(
        line for line in text.splitlines() if not line.strip().startswith("#")
    )
    assert "import time" not in code
    assert "datetime.now" not in code
    assert "utcnow" not in code
