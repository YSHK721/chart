"""E-Order: 発注（Value Object・CLEAN_ARCH §4 / PROCESS §4）。

不変条件:
    side in {buy, sell}
    kind in {market, buy_limit}
    volume は volume_step の倍数かつ [volume_min, volume_max]
    SL/TP は stops_level 距離制約（|price - sl| >= stops_level * point_size）

``validate(symbol_spec)`` は違反時 :class:`InvalidPriceError` を送出し、
適合時は ``None`` を返す。symbol_spec は duck typing（属性アクセス）で受ける
（domain は外部依存ゼロのため pydantic 等を import しない）。

設計判断:
    * side/kind の集合検査も validate() に含め、単一の InvalidPriceError に統一する。
    * price が None（成行で約定価格未確定）の場合、SL/TP の距離検査はスキップする。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from backtest.domain._shared import SIDES
from backtest.domain.exceptions import InvalidPriceError

# kind は Order 固有の語彙のため当モジュールに留める（YAGNI: 単一利用）。
_KINDS = frozenset({"market", "buy_limit"})
# 価格・範囲比較の絶対許容（FX 価格の丸め誤差を吸収する）。
_TOL = 1e-9
# volume / volume_step が整数倍かを判定する際の許容（刻み比の丸め誤差を吸収する）。
_STEP_RATIO_TOL = 1e-6


@dataclass(frozen=True)
class Order:
    side: str
    kind: str
    volume: float
    price: float | None
    sl: float | None = None
    tp: float | None = None

    def validate(self, symbol_spec: Any) -> None:
        """symbol_spec に照らして不変条件を検査する。違反時 InvalidPriceError。"""
        if self.side not in SIDES:
            raise InvalidPriceError(
                "side は {buy, sell} のいずれか", context={"side": self.side}
            )
        if self.kind not in _KINDS:
            raise InvalidPriceError(
                "kind は {market, buy_limit} のいずれか", context={"kind": self.kind}
            )
        self._validate_volume(symbol_spec)
        self._validate_stops(symbol_spec)

    def _validate_volume(self, spec: Any) -> None:
        if not (spec.volume_min - _TOL <= self.volume <= spec.volume_max + _TOL):
            raise InvalidPriceError(
                "volume が [volume_min, volume_max] 範囲外",
                context={
                    "volume": self.volume,
                    "min": spec.volume_min,
                    "max": spec.volume_max,
                },
            )
        # volume_step の倍数か（丸め誤差許容）
        ratio = self.volume / spec.volume_step
        if abs(ratio - round(ratio)) > _STEP_RATIO_TOL:
            raise InvalidPriceError(
                "volume が volume_step の倍数でない",
                context={"volume": self.volume, "step": spec.volume_step},
            )

    def _validate_stops(self, spec: Any) -> None:
        if self.price is None:
            return  # 約定価格未確定（成行）。距離検査は約定後に委ねる
        min_dist = spec.stops_level * spec.point_size
        for label, level in (("sl", self.sl), ("tp", self.tp)):
            if level is None:
                continue
            if abs(self.price - level) < min_dist - _TOL:
                raise InvalidPriceError(
                    f"{label} が stops_level 距離制約に違反",
                    context={
                        "price": self.price,
                        label: level,
                        "min_dist": min_dist,
                    },
                )
