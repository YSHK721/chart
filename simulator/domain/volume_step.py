"""発注量の刻み量子化（domain・Value 変換規則）— **刻み量子化規則の単一所有者**。

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

所有の単一化（ISSUE-502 D-13・2026-09-06）:
    「刻み比が整数に十分近いか（abs(ratio - round(ratio)) <= STEP_RATIO_TOL）を見て
    round か floor を選ぶ」という量子化規則の本体は、以前 3 箇所（本モジュール・
    部分決済規則の決済量算出・発注の刻み検査）に書かれていた。実測（正規化 AST 指紋）で
    前 2 者は完全一致、3 者目は同じ近整数判定の述語側だった。そこで本モジュールが規則と
    許容値を所有し、他は :func:`quantize_to_step` / :func:`is_step_multiple` へ委譲する。
    別々に持つと、片方だけ変えたときに「丸めた結果を発注検査が刻み違反として弾く」
    食い違いが静かに生まれる。第 2 実装の再出現は
    ``simulator/tests/unit/test_volume_step_single_ownership.py`` が AST 走査で赤にする
    （規約ではなく機械的検査で強制する）。
"""
from __future__ import annotations

import math

__all__ = ["STEP_RATIO_TOL", "is_step_multiple", "quantize_to_step", "floor_to_step"]

#: 刻み比の相対許容。0.3/0.1 = 2.9999999999999996 のような二進表現誤差で 1 刻み
#: 落ちるのを防ぐ。判定量（volume/step の整数近さ）は発注の刻み検査・部分決済の決済量
#: 算出と同一のため、許容値も**同一ソース**である（供給元は本モジュールだけ）。
STEP_RATIO_TOL = 1e-6


def _is_near_integer(ratio: float) -> bool:
    """刻み比 ``ratio`` が整数から ``STEP_RATIO_TOL`` 以内か。

    **量子化規則の唯一の本体**。domain のどこかに同じ式が再出現したら単一所有が壊れる。
    """
    return abs(ratio - round(ratio)) <= STEP_RATIO_TOL


def is_step_multiple(value: float, step: float) -> bool:
    """``value`` が ``step`` の整数倍か（刻み比の丸め誤差を ``STEP_RATIO_TOL`` まで許容）。

    事前条件: ``step > 0``。``step <= 0``（＝刻み制約なし）をどう扱うかは呼び出し側の
    業務判断であり、本関数は判断しない（ゼロ除算を隠さない）。
    """
    return _is_near_integer(value / step)


def quantize_to_step(value: float, step: float) -> float:
    """``value`` を ``step`` の倍数へ**保守側（切り捨て）**で量子化する。

    近整数（``STEP_RATIO_TOL`` 以内）は round、それ以外は floor。二進表現誤差で
    1 刻み落ちるのを防ぐ（3.0 が 2.9999999999999996 になる類）。

    下限・上限（銘柄の volume min / volume max）や「0 は発注不可」といった**方針**は
    含まない。方針は呼び出し側（:func:`floor_to_step` と部分決済規則）が持つ。

    事前条件: ``step > 0``。
    """
    ratio = value / step
    steps = round(ratio) if _is_near_integer(ratio) else math.floor(ratio)
    return steps * step


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
    quantized = quantize_to_step(capped, step)

    if quantized + STEP_RATIO_TOL * step < minimum:
        return None
    return quantized
