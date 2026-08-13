"""E-PartialCloseRule: 点数駆動の部分決済規則（Value Object・Phase 7 FR-08）.

裁定 2026-08-13: 部分決済のロット丸めは **保守側**（volume_step へ floor・§12.2 TBD-14
継承）。点数→価格換算は ``point_size`` を乗じる。pandas/JSON 非依存。

規則:
    trigger_profit_points … この含み益（点数）に達するまで部分決済しない（未満は None）。
    close_fraction … 到達時に決済する保有量の割合（0<fraction<1 を想定）。
    point_size … 点数→価格の換算係数。

close_volume(side, entry, ref_price, position_volume, volume_step):
    含み益（buy: ref−entry / sell: entry−ref）が trigger 未満なら None。
    作動時は position_volume × close_fraction を volume_step で floor（切り上げない）。
    floor 結果が 0 なら None（決済不可）。position_volume 以上なら None（全量は部分でない）。

再発火抑止（1 回のみ・裁定「trigger 到達で 1 度」）は本規則の責務外。作動判定は純関数で
毎回同じ結果を返し、「1 回のみ」は適用済み判定を持つ :class:`PositionManager` が担う。

domain 層は外部依存ゼロ。刻み丸めは :func:`floor_to_step` を共有せず、本規則は
「0 と全量を None にする」独自の境界を持つため専用に floor する（volume_min/max は
建玉時に検証済みで、部分決済の残玉・決済量は step の倍数であれば足りる）。
"""
from __future__ import annotations

import math
from dataclasses import dataclass

from simulator.domain._shared import SIDES

# volume_step 倍数判定の丸め誤差許容（Order._validate_volume と同一ソース）。
from simulator.domain.order import _STEP_RATIO_TOL
from simulator.domain.sltp import sltp_from_points


@dataclass(frozen=True)
class PartialCloseRule:
    trigger_profit_points: float
    close_fraction: float
    point_size: float

    def close_volume(
        self,
        side: str,
        entry: float,
        ref_price: float,
        position_volume: float,
        volume_step: float,
    ) -> "float | None":
        """部分決済する数量を返す。作動しない/丸めて 0/全量なら None。

        未知の ``side`` は例外（無音で誤った方向へ倒さない）。
        """
        if side not in SIDES:
            raise ValueError(f"side は {sorted(SIDES)} のいずれか: {side!r}")
        if volume_step <= 0:
            raise ValueError(f"volume_step は正である必要があります: {volume_step}")

        trigger_dist = self.trigger_profit_points * self.point_size
        profit = (ref_price - entry) if side == "buy" else (entry - ref_price)
        if profit < trigger_dist:
            return None

        raw = position_volume * self.close_fraction
        ratio = raw / volume_step
        # 二進表現誤差で 1 刻み落ちるのを防ぐ（floor_to_step と同一の許容）。
        if abs(ratio - round(ratio)) <= _STEP_RATIO_TOL:
            steps = round(ratio)
        else:
            steps = math.floor(ratio)
        quantized = steps * volume_step

        if quantized <= 0:
            return None  # 丸めて 0 → 決済不可（保守側）
        if quantized >= position_volume - _STEP_RATIO_TOL * volume_step:
            return None  # 全量は部分決済でない（保守側・残玉を残す）
        return quantized

    def fill_price(self, side: str, entry: float) -> float:
        """bar 粒度の部分 TP フィル価格（トリガー水準）を返す（Phase 7・依頼者裁定 2026-08-13）。

        部分決済は部分 TP とみなし、極値ではなく**トリガー水準のレベル**で約定させる:
            buy : entry + trigger_profit_points × point_size
            sell: entry − trigger_profit_points × point_size
        点数→価格換算は :func:`sltp_from_points`（既存の単一ソース）の TP 脚を再利用する
        （写経しない）。trigger=0（None を返す縮退）は entry（0 益水準）へ倒す。
        tick 粒度は現在価格を用いるため本メソッドは使わない（PositionManager が分岐する）。
        """
        _, level = sltp_from_points(side, entry, 0.0, self.trigger_profit_points, self.point_size)
        return entry if level is None else level
