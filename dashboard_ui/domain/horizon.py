"""§4.3 地平（horizon）の唯一定義。

「次のターゲット」＝現在値より上（下）で最も近い水準は、**どの時間足の水準を数えるかで
変わる**。実測（§4.3）で距離・持続とも単調に増え段が重ならないことを確認した 3 段:

    短期 = すべて（1m 以上） / 中期 = 1h 以上 / 長期 = 1D 以上

§5.5.5 の価格セル背景の 3 分割も**同じ区分**を使う（新しい概念を増やさない＝認知負荷の
最小化）。したがって区分の定義は本モジュールだけが持つ。
"""
from __future__ import annotations

from enum import Enum
from typing import Mapping

#: 対象の時間足（短い順）。§3.4 の紐付けと同一の 8 本。
TIMEFRAME_ORDER: "tuple[str, ...]" = ("1m", "5m", "15m", "1h", "4h", "1D", "1W", "1M")


class Horizon(Enum):
    """地平 3 段（宣言順＝短い順）。"""

    SHORT = "short"
    MEDIUM = "medium"
    LONG = "long"


#: 各地平に含める最小の時間足（None ＝下限なし）。§4.3 の表がそのまま入る。
HORIZON_MIN_TIMEFRAME: "Mapping[Horizon, str | None]" = {
    Horizon.SHORT: None,
    Horizon.MEDIUM: "1h",
    Horizon.LONG: "1D",
}


def timeframe_rank(timeframe: str) -> int:
    """時間足の順位（短いほど小さい）。未知の足は ValueError（無言で短期へ紛れ込ませない）。"""
    try:
        return TIMEFRAME_ORDER.index(timeframe)
    except ValueError:
        raise ValueError(
            f"未知の時間足です: {timeframe!r}（対象は {TIMEFRAME_ORDER}）"
        ) from None


def includes(horizon: Horizon, timeframe: str) -> bool:
    """その地平がその時間足の水準を数えるか（下限は**以上**＝境界を含む）。"""
    rank = timeframe_rank(timeframe)
    minimum = HORIZON_MIN_TIMEFRAME[horizon]
    return True if minimum is None else rank >= timeframe_rank(minimum)


def horizons_of(timeframe: str) -> "tuple[Horizon, ...]":
    """その時間足の水準が属する地平すべて（短い順）。"""
    return tuple(h for h in Horizon if includes(h, timeframe))
