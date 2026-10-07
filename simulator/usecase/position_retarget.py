"""戦略が名乗る「保有中の玉の SL/TP を動かす口」を 1 回だけ読む（2026-10-07・依頼者承認）。

口（`simulator.usecase.ports.PositionRetargetPort`）を持たない戦略は ``None``。エンジンは run の
始まりに 1 回だけ読み、判定する足で呼ぶ（足ごとに属性を引き直さない）。
"""
from __future__ import annotations

from typing import Any, Callable


def declared_position_retarget(strategy: Any) -> "Callable[..., Any] | None":
    """戦略が名乗る SL/TP を動かすメソッド（無ければ ``None``）。"""
    retarget = getattr(strategy, "retarget_positions", None)
    return retarget if callable(retarget) else None
