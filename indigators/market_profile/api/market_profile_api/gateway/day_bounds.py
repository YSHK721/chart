"""day_bounds — UTC 暦日境界の算出（gateway 層の単一定義・ISSUE-183）。

``TickReaderPort.day_files`` の引数契約が UNIX 秒（int）になったことに伴い、「その秒を含む UTC 暦日の
始端」を求める規則を gateway 側の 1 箇所に置く。従来は各所で ``pd.Timestamp(sec, unit="s").normalize()``
を書いており、pandas 依存が compute 側のポート契約まで貫通する原因になっていた。

本モジュールは pandas を import しない（純整数演算）。UNIX 秒は閏秒を持たないため、
``floor(sec / 86400) * 86400`` は ``pd.Timestamp(sec, unit="s").normalize()`` と常に同値である
（負値でも Python の floor 除算が下方向丸めとなり ``normalize()`` の floor 挙動に一致する）。
"""

from __future__ import annotations

from typing import Any

#: 1 UTC 暦日の秒数（閏秒なし＝UNIX 時間の定義）。
SECONDS_PER_DAY = 86400


def utc_day_start(sec: Any) -> int:
    """UNIX 秒 ``sec`` を含む UTC 暦日の始端（00:00:00 UTC）を UNIX 秒 int で返す。"""
    return (int(sec) // SECONDS_PER_DAY) * SECONDS_PER_DAY


def next_utc_day_start(sec: Any) -> int:
    """UNIX 秒 ``sec`` を含む UTC 暦日の**翌日**始端を UNIX 秒 int で返す。"""
    return utc_day_start(sec) + SECONDS_PER_DAY
