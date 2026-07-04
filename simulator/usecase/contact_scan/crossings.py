"""crossings — レベルに対する系列の sign 変化（クロス）検出（純・stdlib のみ）。

参照実装 prototype_260626-01/contact_scan/crossings.py と bit 一致。``detect_crossings`` は
時系列 ``series = [(t, val), ...]`` を走査し、``level`` に対する符号（+1=上 / -1=下 / 0=タッチ）
の非ゼロ符号が反転した位置を 1 接点イベントとして列挙する。

固定する規約（テストで固定）:
  - 符号: ``val > level`` → +1, ``val < level`` → -1, ``val == level`` → 0（タッチ）。
  - イベント発火: 直近の非ゼロ符号と異なる非ゼロ符号が現れた点。``direction`` は新符号が
    +1 なら "up"（下→上）、-1 なら "down"（上→下）。
  - ``==level`` タッチ（符号 0）: 直近非ゼロ符号を更新しない。タッチ後に同じ側へ戻れば非接点、
    反対側へ抜ければその到達点で 1 接点。
  - 連続フラット（複数の ==level）: 何も発火させない（基準符号を保持）。
  - 開始時上下: 最初の非ゼロ符号は基準を確立するのみ（イベントなし）。
  - ``prev_time`` / ``prev_price`` は接点点の直前の系列要素（``series[j-1]``）。
"""
from __future__ import annotations


def _sign(value: float, level: float) -> int:
    if value > level:
        return 1
    if value < level:
        return -1
    return 0


def detect_crossings(series, level: float):
    """``series`` 内で ``level`` を跨ぐ（sign 反転する）接点イベントの list を返す。

    各イベント: ``{"index", "time", "price", "prev_time", "prev_price", "direction"}``。
    """
    events = []
    last_sign = 0
    for j, (t, v) in enumerate(series):
        sign = _sign(v, level)
        if sign == 0:
            continue
        if last_sign != 0 and sign != last_sign:
            pt, pv = series[j - 1]
            events.append({
                "index": j,
                "time": t,                            # 接点到達点の時刻
                "price": v,
                "prev_time": pt,
                "prev_price": pv,
                "direction": "up" if sign == 1 else "down",
            })
        last_sign = sign
    return events
