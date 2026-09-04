"""発注量の刻み丸め（domain・Value 変換規則）。

責務（SRP）: 「連続量として算出された発注量を、銘柄仕様（刻み・下限・上限）が許す
離散値へ**保守側**で写す」ことだけを持つ。証拠金式・ケリー式・口座状態は一切知らない。

なぜ domain か: 丸め方向（floor）は取引の安全側という**業務規則**であり、pandas や
HTTP といった偶有的技術に依存しない。実測（`simulator/domain/order.py` の
`_validate_volume` は production 経路で呼ばれない＝刻み違反はエンジンで例外にならない）
より、刻みの強制点はここと Decorator の出力そのものになる。

規則（基本設計書 §12.2・依頼者裁定「丸めは保守側」）:
    1. `step` の倍数へ **floor**。切り上げは exposure を増やすため行わない。
    2. `maximum` を超えない。
    3. 結果が `minimum` 未満なら ``None``（発注不可）。`minimum` へ切り上げると
       「計算上許されない量」を建てることになり保守側でない。
"""
from __future__ import annotations

import math

# 刻み比の相対許容。0.3/0.1 = 2.9999999999999996 のような二進表現誤差で 1 刻み
# 落ちるのを防ぐ。判定量（volume/step の整数近さ）は `Order._validate_volume` と同一のため、
# 許容値も**同一ソース**を使う。別々に持つと、片方だけ変えたときに「丸めた結果を
# Order.validate が刻み違反として弾く」という食い違いが静かに生まれる。
from simulator.domain.order import _STEP_RATIO_TOL


def floor_to_step(
    volume: float, *, step: float, minimum: float, maximum: float
) -> "float | None":
    """``volume`` を保守側（切り捨て）で刻みへ丸める。発注不可なら ``None``。

    Args:
        volume: 連続量として算出された発注量。
        step: 銘柄の volume_step（正）。
        minimum: 銘柄の volume_min。
        maximum: 銘柄の volume_max。

    Raises:
        ValueError: ``step`` が正でない、または ``maximum < minimum``。
    """
    if step <= 0:
        raise ValueError(f"volume_step は正である必要があります: {step}")
    if maximum < minimum:
        raise ValueError(
            f"volume_max は volume_min 以上である必要があります: {maximum} < {minimum}"
        )
    if volume <= 0:
        return None

    capped = min(volume, maximum)
    ratio = capped / step
    # 二進表現誤差で 1 刻み落ちるのを防ぐ（3.0 が 2.9999999999999996 になる類）。
    if abs(ratio - round(ratio)) <= _STEP_RATIO_TOL:
        steps = round(ratio)
    else:
        steps = math.floor(ratio)
    quantized = steps * step

    if quantized + _STEP_RATIO_TOL * step < minimum:
        return None
    return quantized
