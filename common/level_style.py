"""σ水準線（horizontal_line）の線幅の単一ソース。

各指標 lwc_chart.py が `chart.horizontal_line(..., width=LEVEL_LINE_WIDTH)` で
参照する共有定数を提供する。線幅を変更する場合はここ 1 箇所のみを変更する。
common は numpy のみの純粋層という方針に沿い、外部依存を足さない（純粋な int 定数）。
"""

from __future__ import annotations

LEVEL_LINE_WIDTH: int = 2
"""σ水準線（horizontal_line）の既定線幅（px）。他のライン（系列線=幅1）と同幅で視認性が
悪かったため 2px に設定し、水準線を判別しやすくする。"""
