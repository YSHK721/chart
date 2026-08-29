"""§6 到達判定（交差）と到達時刻（定義 A）の**唯一の定義**。

§6.1 判定: 水準はバーごとに動くため固定値との比較では判定できない。各バー t で観測値
`value_t` と水準 `level_t` を突き合わせる。

    上側の水準（観測値より上にある水準）: reached_t := value_t >= level_t
    下側の水準（観測値より下にある水準）: reached_t := value_t <= level_t

（§13.1 の測定定義「1m high >= v（上）/ low <= v（下）」と同一。両側とも同値は到達に含む。）

§6.2 到達時刻（定義 A）:

    first_t := min{ s | reached_s = reached_now かつ ∀u∈[s, now]: reached_u = reached_now }

窓内の最古の到達（定義 B）は採らない。観測値が水準を離れて戻れば、戻った時点が新しい
始端になる。クライアント側で観測しながら積み上げてはならない（§6.3。開いた時刻に依存し、
開き直すたびに値が変わる）。本関数は履歴の突合だけで導出するため、いつ呼んでも同じ値を返す。
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from enum import Enum
from typing import Sequence


class LevelSide(Enum):
    """水準が観測値のどちら側にあるか（到達の向き）。"""

    ABOVE = "above"
    BELOW = "below"


def is_reached(value: float, level: float, side: LevelSide) -> "bool | None":
    """§6.1 の交差判定。観測値・水準のいずれかが非有限なら判定不能（None）。

    戻り値は必ず素の `bool`（numpy スカラを渡されても `np.bool_` を漏らさない。漏らすと
    `is True` 判定と JSON 直列化が壊れる）。
    """
    if not (math.isfinite(value) and math.isfinite(level)):
        return None
    if side is LevelSide.ABOVE:
        return bool(value >= level)
    return bool(value <= level)


@dataclass(frozen=True)
class ReachState:
    """現在の到達状態と、その状態が始まった時刻（定義 A）。

    Attributes:
        reached: 現在の到達状態。判定不能（水準なし）は None。
        since_time: 現在の状態が始まった時刻。判定不能なら None。
        truncated: 連続区間が履歴の先頭で切れている（＝始端が履歴外かもしれない）。
            True のとき `since_time` は「これ以上遡れない」ことしか意味しない。
    """

    reached: "bool | None"
    since_time: "int | None"
    truncated: bool


def reach_state(
    times: Sequence[int],
    values: Sequence[float],
    levels: Sequence[float],
    *,
    side: LevelSide,
) -> ReachState:
    """時刻で揃えた観測値系列・水準系列から現在の到達状態と到達時刻を導出する。

    Args:
        times/values/levels: 同一長・同一時刻で整列済みの 3 系列（整列は呼び出し側の責務）。
        side: 水準の向き（§6.1）。

    Raises:
        ValueError: 3 系列の長さが揃っていないとき。
    """
    if not (len(times) == len(values) == len(levels)):
        raise ValueError(
            "times / values / levels は同一長が必要です: "
            f"{len(times)} / {len(values)} / {len(levels)}"
        )
    if not times:
        return ReachState(reached=None, since_time=None, truncated=False)

    now = is_reached(values[-1], levels[-1], side)
    if now is None:
        return ReachState(reached=None, since_time=None, truncated=False)

    start = len(times) - 1
    while start > 0 and is_reached(values[start - 1], levels[start - 1], side) is now:
        start -= 1
    return ReachState(reached=now, since_time=int(times[start]), truncated=start == 0)
