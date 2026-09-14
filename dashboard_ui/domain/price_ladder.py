"""§4 第 1 表＝価格ラダーの構築（価格を唯一の軸に置く）。

形（§4.1）:
    行 = 水準 1 本（指標インスタンス × 系列）。**束ねない**（依頼者裁定 2026-08-29:
        束ねると大枠の水準しか把握できない。実測でも「次のターゲット」の持続は伸びない）。
    列 = 距離 / 価格 / 時間足 / 水準ラベル。**時間足の列は持たない**（§4.2: 価格という
        単一の比較軸を 8 列へ切り刻むと、現在値の直上の水準が列をまたいで交互に現れる）。
    並び = 価格降順。現在値は独立行として価格順の位置に入る。

重なりは束ねる代わりに「直前行（1 つ上の行）との価格差」を各行へ添えて読み取る（§4.7）。
地平 3 段（§4.3）の直上・直下には「次のターゲット」の印を付ける。
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Iterable, Sequence

from dashboard_ui.domain.horizon import Horizon, horizons_of, timeframe_rank


class DuplicateRowLabelError(ValueError):
    """行の識別子（ラベル × 時間足）が衝突した（§11-2 の是正を機械的に固定する）。"""


@dataclass(frozen=True)
class LevelInput:
    """ラダーへ載せる水準 1 本。`label` はパラメータまで含めた一意名（例 `MA ema24 hlc3`）。"""

    price: float
    timeframe: str
    label: str

    @property
    def row_key(self) -> "tuple[str, str]":
        return (self.label, self.timeframe)


@dataclass(frozen=True)
class LadderRow:
    """ラダー 1 行（§4.7 の版面の 1 行に対応する）。"""

    price: float
    timeframe: str
    label: str
    distance: float
    gap_to_previous: "float | None"
    horizon_marks: "frozenset[Horizon]"

    @property
    def is_above_current(self) -> bool:
        return self.distance > 0.0


@dataclass(frozen=True)
class PriceLadder:
    """価格降順に並んだラダー全体。

    Attributes:
        current_index: 現在値の独立行が入る位置（`rows[current_index]` の直前）。
            現在値と同値の水準行は現在値行の**後ろ**に来る。
    """

    current_price: float
    rows: "tuple[LadderRow, ...]"
    current_index: int

    def next_target(self, horizon: Horizon, *, above: bool) -> "LadderRow | None":
        """その地平の「次のターゲット」＝印の付いた直上（直下）の行。無ければ None。"""
        for row in self.rows if above else reversed(self.rows):
            if horizon in row.horizon_marks and row.is_above_current is above:
                return row
        return None


def _require_finite_price(name: str, value: float) -> float:
    number = float(value)
    if not math.isfinite(number):
        raise ValueError(f"{name} は有限値が必要です（水準なしはラダーへ載せない）: {value!r}")
    return number


def build_ladder(levels: Iterable[LevelInput], *, current_price: float) -> PriceLadder:
    """水準の集まりから価格ラダーを構築する。

    Raises:
        ValueError: 価格が非有限、または時間足が未知のとき。
        DuplicateRowLabelError: ラベル × 時間足が重複したとき。
    """
    current = _require_finite_price("current_price", current_price)
    items: "list[LevelInput]" = []
    seen: "set[tuple[str, str]]" = set()
    for level in levels:
        _require_finite_price(f"level[{level.label}].price", level.price)
        timeframe_rank(level.timeframe)          # 未知の足はここで弾く
        if level.row_key in seen:
            raise DuplicateRowLabelError(
                f"行の識別子が重複しています: label={level.label!r}, "
                f"timeframe={level.timeframe!r}（パラメータまで含めて一意にすること）"
            )
        seen.add(level.row_key)
        items.append(level)

    # 価格降順。同値は (時間足, ラベル) で決定的に並べる（同じ入力は必ず同じ並び）。
    items.sort(key=lambda lv: (-lv.price, timeframe_rank(lv.timeframe), lv.label))
    marks = _horizon_marks(items, current)

    rows: "list[LadderRow]" = []
    for index, level in enumerate(items):
        rows.append(
            LadderRow(
                price=level.price,
                timeframe=level.timeframe,
                label=level.label,
                distance=level.price - current,
                gap_to_previous=None if index == 0 else items[index - 1].price - level.price,
                horizon_marks=marks[index],
            )
        )
    current_index = sum(1 for level in items if level.price > current)
    return PriceLadder(current_price=current, rows=tuple(rows), current_index=current_index)


def _horizon_marks(
    items: Sequence[LevelInput], current: float
) -> "list[frozenset[Horizon]]":
    """各行に付く地平の印（その地平の直上／直下＝「次のターゲット」）を決める。

    現在値と**同値**の水準は「現在値より上（下）」ではないため、どちらの印も付かない（§4.3）。
    """
    marked: "list[set[Horizon]]" = [set() for _ in items]
    for horizon in Horizon:
        nearest_above = nearest_below = None
        for index, level in enumerate(items):
            if horizon not in horizons_of(level.timeframe):
                continue
            if level.price > current:
                nearest_above = index          # 降順なので上側は最後に見たものが最も近い
            elif level.price < current and nearest_below is None:
                nearest_below = index          # 下側は最初に見たものが最も近い
        for index in (nearest_above, nearest_below):
            if index is not None:
                marked[index].add(horizon)
    return [frozenset(entry) for entry in marked]
