"""E-SLTP: 点数固定の SL/TP を絶対価格へ換算する単一ソース（domain 層・Phase 6 F-8）。

TBD-11: SL/TP は点数固定のみ（インジケーター駆動 SL/TP は不可）。本関数は
``tc24051901.py:64-72`` の点数規則（dist=points×point_size・buy/sell 対称）を
**単一ソース化**したものである（6 戦略の各自複製は無改変で凍結し、本関数は
汎用戦略 :class:`GenericConditionStrategy` からのみ参照する）。

規則:
    dist = points × point_size
    buy : sl = base − sl_dist / tp = base + tp_dist
    sell: sl = base + sl_dist / tp = base − tp_dist
    points == 0 → その脚は ``None``（SL または TP を置かない）。

domain 層は外部依存ゼロ（pandas/JSON を import しない）。
"""
from __future__ import annotations

from simulator.domain._shared import SIDES


def sltp_from_points(
    side: str,
    base_price: float,
    sl_points: float,
    tp_points: float,
    point_size: float,
) -> "tuple[float | None, float | None]":
    """基準価格から SL/TP の絶対価格を返す。points==0 の脚は ``None``。

    未知の ``side`` は例外（無音で誤った建値へ倒さない）。
    """
    if side not in SIDES:
        raise ValueError(f"side は {sorted(SIDES)} のいずれか: {side!r}")
    sl_dist = sl_points * point_size
    tp_dist = tp_points * point_size
    if side == "buy":
        sl = None if sl_points == 0 else base_price - sl_dist
        tp = None if tp_points == 0 else base_price + tp_dist
    else:  # sell（対称）
        sl = None if sl_points == 0 else base_price + sl_dist
        tp = None if tp_points == 0 else base_price - tp_dist
    return sl, tp
