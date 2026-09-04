"""増分カーソル規約（domain B・**依存ゼロ**・stdlib のみ）。

供給の連続性を決める規約を 1 箇所に閉じる。要点は 2 つある。

窓の下端を含む理由:
    MT5 の ``time_msc`` は**同一 ms に複数ティック**が並ぶ。再開点を「保存済み最大 ms より後」に
    すると、その ms の残りを永久に取りこぼす。よって窓は ``[cursor_ms, ...)`` と下端を含め、
    重複して返る境界行を受け取り側で落とす。**境界 1 ms の再取得は無駄ではなく、
    正しさに必要な入力**である（計算量検定 CX-a はこの重複だけを許し、
    ポーリング回数・セッション長に比例しないことを固定する）。

一致しなければ止める理由:
    同一 ms 内の返却順序が安定かは**未検証**（V-2）。順序が揺れると「先頭 n 行を落とす」判断が
    別の行を落としうる。よって値まで一致した場合のみ落とし、不一致は :class:`CursorContractError`
    で Fail-Stop する。黙って続けると欠落・重複が静かに台帳へ入る。
"""
from __future__ import annotations

from typing import List, NamedTuple, Optional, Sequence, Tuple

#: ティック 1 行 = ``(サーバ時刻ラベル ms, bid, ask)``。
Row = Tuple[int, float, float]


class CursorContractError(RuntimeError):
    """カーソル規約が破れたことを表す（Fail-Stop）。取りこぼし・重複を黙認しない。"""


class Cursor(NamedTuple):
    """永続化済みの再開点。

    ``cursor_ms``
        永続化済みの最大サーバ時刻ラベル ms。
    ``boundary_rows``
        その ms に属する**全行**。次回応答の先頭で同じ並びが返ることを照合するために持つ。
    """

    cursor_ms: int
    boundary_rows: Tuple[Row, ...]


class AbsorbResult(NamedTuple):
    """``absorb`` の結果（``new_rows`` / ``dropped`` / ``next_cursor``）。"""

    new_rows: List[Row]
    dropped: int
    next_cursor: Cursor


def request_window(cursor: Cursor) -> "Tuple[int, Optional[int]]":
    """次に要求する窓 ``(from_ms, to_ms)`` を返す。**下端は含む**・上端は開く。"""
    return (cursor.cursor_ms, None)


def _rows_on(rows: "Sequence[Row]", ms: int) -> "Tuple[Row, ...]":
    """``rows`` のうち ``ms`` に属する行（``rows`` は昇順であることが前提）。"""
    return tuple(r for r in rows if r[0] == ms)


def absorb(cursor: Cursor, rows: "Sequence[Row]") -> AbsorbResult:
    """応答 ``rows`` から**新着だけ**を取り出し、次のカーソルを決める。

    前提（破れたら Fail-Stop）: ``rows`` は ms 昇順・``rows[0][0] >= cursor.cursor_ms``。
    ``cursor_ms`` に属する先頭 ``len(boundary_rows)`` 行が**値まで一致**したときだけ落とす。
    同じ ms に後から増えた行は新着として残す（取りこぼさない）。
    """
    rows = list(rows)
    if not rows:
        return AbsorbResult(new_rows=[], dropped=0, next_cursor=cursor)

    for prev, nxt in zip(rows, rows[1:]):
        if nxt[0] < prev[0]:
            raise CursorContractError(
                f"応答が ms 昇順ではありません: {prev[0]} の次に {nxt[0]}。"
                " 並べ替えて救わない（順序が崩れる原因を隠すため）。"
            )
    if rows[0][0] < cursor.cursor_ms:
        raise CursorContractError(
            f"窓の下端より前の行が含まれます: {rows[0][0]} < {cursor.cursor_ms}。"
        )

    expected = cursor.boundary_rows
    dropped = len(expected)
    if dropped:
        head = rows[:dropped]
        if len(head) < dropped or tuple(head) != expected:
            raise CursorContractError(
                "境界 ms の行が保存済みと一致しません"
                f"（期待 {expected!r} / 実際 {tuple(rows[:dropped])!r}）。"
                " 同一 ms 内の返却順序は未検証（V-2）のため、推測で落とさず停止する。"
            )
    new_rows = rows[dropped:]

    if not new_rows:
        return AbsorbResult(new_rows=[], dropped=dropped, next_cursor=cursor)

    last_ms = new_rows[-1][0]
    boundary = _rows_on(rows, last_ms)
    return AbsorbResult(
        new_rows=new_rows,
        dropped=dropped,
        next_cursor=Cursor(cursor_ms=last_ms, boundary_rows=boundary),
    )


def from_journal_tail(tail: "Sequence[Row]") -> "Optional[Cursor]":
    """ジャーナル末尾の行群から再開点を復元する（**復元の唯一経路**）。

    ジャーナルが正であり、他のどこからもカーソルを作らない。空なら ``None`` を返し、
    呼び出し側にコールドスタート（``--from`` 明示）を強制する。**暗黙既定を作らない**。
    """
    tail = list(tail)
    if not tail:
        return None
    last_ms = max(r[0] for r in tail)
    return Cursor(cursor_ms=last_ms, boundary_rows=_rows_on(tail, last_ms))
