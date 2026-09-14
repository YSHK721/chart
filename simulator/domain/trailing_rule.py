"""E-TrailingRule: 点数駆動トレーリングストップ規則（Value Object・Phase 7 FR-07）.

TBD-01（案 B）/ 裁定 2026-08-13: トレーリング方向は **tighten_only**（トレーリング
ストップの普遍的意味論＝緩めない・逆行で SL を戻さない）。点数→価格換算は
``point_size`` を乗じる（:func:`sltp_from_points` と同規則）。pandas/JSON 非依存。

規則:
    trigger_points … この含み益（点数）に達するまで作動しない（未満は据え置き＝None）。
    distance_points … 作動時、参照価格から離す距離（点数）。新 SL 候補 = ref ∓ distance。
    step_points … 更新の最小刻み（点数）。current_sl から step 以上動く場合のみ更新。
                  0 は連続（厳密に締まる限り常に更新）。
    point_size … 点数→価格の換算係数。

含み益（価格）:
    buy : ref_price − entry
    sell: entry − ref_price
新 SL 候補:
    buy : ref_price − distance_dist（SL を上げる方向が「締める」）
    sell: ref_price + distance_dist（SL を下げる方向が「締める」）
tighten_only:
    current_sl があるとき、buy は候補 > current_sl、sell は候補 < current_sl のときのみ
    更新する（それ以外は緩める/横ばいなので None）。current_sl が None は初回設定として作動。

domain 層は外部依存ゼロ。
"""
from __future__ import annotations

from dataclasses import dataclass

from simulator.domain._shared import SIDES


@dataclass(frozen=True)
class TrailingRule:
    trigger_points: float
    distance_points: float
    step_points: float
    point_size: float

    def new_stop(
        self,
        side: str,
        entry: float,
        ref_price: float,
        current_sl: "float | None",
    ) -> "float | None":
        """新しい SL 価格を返す。作動しない/緩める/刻み未満なら None（据え置き）。

        未知の ``side`` は例外（無音で誤った方向へ倒さない）。
        """
        if side not in SIDES:
            raise ValueError(f"side は {sorted(SIDES)} のいずれか: {side!r}")

        trigger_dist = self.trigger_points * self.point_size
        distance_dist = self.distance_points * self.point_size
        step_dist = self.step_points * self.point_size

        if side == "buy":
            profit = ref_price - entry
            if profit < trigger_dist:
                return None
            candidate = ref_price - distance_dist
            if current_sl is not None:
                if candidate <= current_sl:  # 緩める/横ばい → 据え置き（tighten_only）
                    return None
                if step_dist > 0 and (candidate - current_sl) < step_dist:
                    return None
            return candidate
        else:  # sell（対称）
            profit = entry - ref_price
            if profit < trigger_dist:
                return None
            candidate = ref_price + distance_dist
            if current_sl is not None:
                if candidate >= current_sl:  # 緩める/横ばい → 据え置き（tighten_only）
                    return None
                if step_dist > 0 and (current_sl - candidate) < step_dist:
                    return None
            return candidate
