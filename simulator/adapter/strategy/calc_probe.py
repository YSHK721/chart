"""CalcProbe 戦略（StrategyPort 実装・計算処理の動作確認専用 EA）。

目的:
    エンジンの計算（約定価格・損益・証拠金・ロスカット・ロット刻み・指標値）を、テストが
    設計書の式から**独立に**計算した値と照合できるようにする。EA は売買を起こすだけで、
    自分では何も照合しない（照合の所有者は
    `simulator/tests/integration/test_calc_probe_calculations.py`）。

売買規則（決定論・判定は足の始まり）:
    足 i の始値 ``open[i]`` と、直前の確定足までの SMA ``sma[i-1]`` を比べる。
        open[i] >  sma[i-1] → 買い
        open[i] <= sma[i-1] → 売り
    望む方向を既に保有していれば何もしない。反対方向を保有していれば成行を出し、エンジンの
    途転（反対玉の reverse 決済 → 新規建て）で建て替える。SMA が未成立（NaN）の足では何もしない。

    買いと売りを交互に建てるため、bid/ask の両側・損益の両符号・途転決済の経路を必ず通る。

ロット:
    ``lot_size`` を ``on_init`` で **1 回だけ** 銘柄の刻みへ切り捨てて保持する。刻みの規則は
    domain の `floor_to_step` が唯一の所有者であり、ここで書き直さない。発注できない量
    （刻みが正でない・最小未満）は `ConfigError`＝起動失敗とする。
"""
from __future__ import annotations

import math
from typing import Any

from simulator.domain.exceptions import ConfigError
from simulator.domain.order import Order
from simulator.domain.volume_step import floor_to_step
from simulator.usecase.ports import EntryPriceBasisPort, StrategyPort


class CalcProbe(StrategyPort, EntryPriceBasisPort):
    """始値と直前確定足の SMA の大小で買い・売りを途転し続ける EA。"""

    #: 判定の瞬間（`EntryPriceBasisPort`）。読むのは当該足の始値と確定済みの SMA だけなので、
    #: 判定は足の**始まり**で成立する（取れる価格は始値の bid/ask）。
    entry_price_basis = "current_open"

    def __init__(self) -> None:
        self._lot: float | None = None

    def on_init(self, config: Any, indicators: Any) -> None:
        try:
            lot = floor_to_step(
                float(config["lot_size"]),
                step=float(config["volume_step"]),
                minimum=float(config["volume_min"]),
                maximum=float(config["volume_max"]),
            )
        except ValueError as exc:
            raise ConfigError(
                "CalcProbe は lot_size を銘柄の刻みへ丸められません", context={"reason": str(exc)}
            ) from None
        if lot is None:
            raise ConfigError(
                "CalcProbe の lot_size が発注可能な量になりません",
                context={"lot_size": config["lot_size"]},
            )
        self._lot = lot

    def on_new_bar(self, bar_index: int, indicators: Any, account: Any) -> list[Order]:
        if self._lot is None:
            raise ValueError("CalcProbe requires on_init() before on_new_bar().")
        if bar_index < 1:
            return []
        prev_sma = float(indicators.get("sma").iloc[bar_index - 1])
        if math.isnan(prev_sma):
            return []
        open_ = float(indicators.get("open").iloc[bar_index])
        side = "buy" if open_ > prev_sma else "sell"
        if side in self._held_sides(account):
            return []
        return [
            Order(side=side, kind="market", volume=self._lot, price=None, sl=None, tp=None)
        ]

    def on_position_check(self, position: Any, bar_index: int, indicators: Any) -> str:
        return "hold"

    @staticmethod
    def _held_sides(account: Any) -> set[str]:
        if account is None:
            return set()
        return {p.side for p in getattr(account, "open_positions", [])}
