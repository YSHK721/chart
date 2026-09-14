"""§6 到達判定（交差）と到達時刻の**唯一の定義**。

到達時刻は表で定義が異なる（依頼者裁定 2026-08-31）:
    第 2 表（オシレータ）… 定義 C（履歴全体での最初の接点・:func:`reach_state`）。
    第 1 表（価格ラダー）… 定義 D（**当該足の現在バー期間内**の最初の接触・
        :func:`period_first_touch`。D なら今日・W なら今週・1m ならこの 1 分間。
        期間が変われば**リセット**される）。定義 C をラダーへ使うと、頻繁に跨がれる水準
        （例: 1D の MA(5)）で初回接点が履歴の始端（2018 年）へ張り付き情報量が消える
        （依頼者指摘 2026-08-31）。

§6.1 判定: 水準はバーごとに動くため固定値との比較では判定できない。各バー t で観測値
`value_t` と水準 `level_t` を突き合わせる。

    上側の水準（観測値より上にある水準）: reached_t := value_t >= level_t
    下側の水準（観測値より下にある水準）: reached_t := value_t <= level_t

（§13.1 の測定定義「1m high >= v（上）/ low <= v（下）」と同一。両側とも同値は到達に含む。）

§6.2 到達時刻（定義 C＝最初の接点・依頼者指示 2026-08-31）:

    first_t := min{ s | reached_s = reached_now }

履歴の中で**現在の状態が最初に現れた時刻**。途中で観測値が水準を離れて戻っても、起点は
最初の接点のまま動かない（v0.9.21 までの定義 A「現在の連続区間の始端」は、反転のたびに
起点が若返るため置換した）。クライアント側で観測しながら積み上げてはならない（§6.3。
開いた時刻に依存し、開き直すたびに値が変わる）。本関数は履歴の突合だけで導出するため、
いつ呼んでも同じ値を返す。
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
    """現在の到達状態と、その状態が最初に現れた時刻（定義 C＝最初の接点）。

    Attributes:
        reached: 現在の到達状態。判定不能（水準なし）は None。
        since_time: 現在の状態が履歴で最初に現れた時刻。判定不能なら None。
        truncated: 最初の接点が履歴の先頭にある（＝真の最初は履歴外かもしれない）。
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

    # 先頭から最初の接点を探す（定義 C・第 2 表用）。最後の要素が `now` なので必ず見つかる。
    # 途中の判定不能（None）や反転は起点を動かさない——最初の接点より前に反対の状態
    # （または水準なし）を観測していれば、真の最初を見届けたことになる（truncated=False）。
    first = len(times) - 1
    for at in range(len(times)):
        if is_reached(values[at], levels[at], side) is now:
            first = at
            break
    return ReachState(reached=now, since_time=int(times[first]), truncated=first == 0)


def period_first_touch(
    times: Sequence[int],
    highs: Sequence[float],
    lows: Sequence[float],
    *,
    level: float,
    period_start: int,
) -> ReachState:
    """§6.2 定義 D（第 1 表・依頼者裁定 2026-08-31）: 現在バー期間内の最初の接触時刻。

    接触 := low <= level <= high（§13.1 の測定「1m high >= v（上）/ low <= v（下）」と同じ
    材料・両側を 1 つの式で覆う）。水準は**現在の値**で判定する（期間内の水準の動きまで
    遡って復元しない——細粒度の過去水準は保存されていない・発明しない）。

    Args:
        times/highs/lows: 細粒度（表示足＝1m）の足 3 系列（時刻昇順・同一長）。
        level: 判定する水準の現在値。
        period_start: 当該行の時間足の**現在バーの始端**（そのバーの time）。

    Returns:
        reached: 期間内に接触があったか。水準が非有限なら None。
        since_time: 最初に接触した細粒度バーの時刻。接触なしは None。
        truncated: 細粒度の履歴が期間の始端を覆っていない（＝それ以前の接触は観測不能）。
    """
    if not (len(times) == len(highs) == len(lows)):
        raise ValueError(
            "times / highs / lows は同一長が必要です: "
            f"{len(times)} / {len(highs)} / {len(lows)}"
        )
    if not math.isfinite(level):
        return ReachState(reached=None, since_time=None, truncated=False)
    truncated = bool(times) and int(times[0]) > int(period_start)
    for at in range(len(times)):
        if int(times[at]) < int(period_start):
            continue
        high, low = float(highs[at]), float(lows[at])
        if not (math.isfinite(high) and math.isfinite(low)):
            continue
        if low <= level <= high:
            return ReachState(reached=True, since_time=int(times[at]), truncated=truncated)
    return ReachState(reached=False, since_time=None, truncated=truncated)
