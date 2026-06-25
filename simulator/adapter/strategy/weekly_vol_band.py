"""WeeklyVolBand 戦略（StrategyPort 実装・詳細設計 §5.1・D1）。

セグメント先頭バー(bar_index=0)で market ロング 1 件（sl=S, tp=T, volume=N）を発注。
S/T/N は当週 forecast から VolatilityBand で算出。O はセグメント先頭バー open
（indicators["open"].iloc[0]）。on_position_check は常に "hold"。

D1：金曜引けは engine の end_of_test 清算が担う（曜日判定で close する誤実装を禁止）。
本 EA は engine 未配線時も VO/発注ロジックを単体検証可能。pandas は indicators 参照
（iloc）でのみ触れる adapter 境界。
"""
from __future__ import annotations

from typing import Any

from simulator.domain.oco_order_pair import OcoOrderPair
from simulator.domain.order import Order
from simulator.domain.variance_forecast import VarianceForecast
from simulator.domain.volatility_band import VolatilityBand
from simulator.usecase.ports import StrategyPort


def indicators_open_at(indicators: Any, i: int) -> float:
    """indicators の "open" 系列の位置 i の値（セグメント先頭 open＝O）。"""
    return float(indicators.get("open").iloc[i])


def _cfg_get(config: Any, key: str, default: Any) -> Any:
    """config を dict（subscript）/ BacktestConfig（属性）の双方で読む。

    engine は on_init に BacktestConfig（属性アクセス）を渡し、単体テストは dict を渡す。
    両経路で digits / volume_step を取得できるようにする。
    """
    if config is None:
        return default
    if hasattr(config, "get") and not hasattr(config, "__dataclass_fields__"):
        return config.get(key, default)
    try:
        return config[key]  # subscript 可能な dict
    except (TypeError, KeyError):
        return getattr(config, key, default)


class WeeklyVolBand(StrategyPort):
    def __init__(
        self, forecast: VarianceForecast, p_tp: float, capital: float, f_risk: float
    ) -> None:
        self._fc = forecast
        self._p_tp = p_tp
        self._capital = capital
        self._f_risk = f_risk
        self._armed = False
        self._config: Any = None

    def on_init(self, config: Any, indicators: Any) -> None:
        self._config = config

    def on_new_bar(self, bar_index: int, indicators: Any, account: Any) -> "list[Order]":
        if self._armed or bar_index != 0:
            return []
        self._armed = True
        O = indicators_open_at(indicators, 0)
        band = VolatilityBand.from_forecast(
            week_id=self._fc.week_id, O=O,
            sigma_minus=self._fc.sigma_minus, sigma_plus=self._fc.sigma_plus,
            p_tp=self._p_tp, f_risk=self._f_risk, capital=self._capital,
        )
        digits = int(_cfg_get(self._config, "digits", 0))
        N = self._round_volume(band.N)
        sl = round(band.S, digits)
        tp = round(band.T, digits)
        entry = Order(side="buy", kind="market", volume=N, price=None, sl=sl, tp=tp)
        return OcoOrderPair(entry, band).as_orders()

    def on_position_check(self, position: Any, bar_index: int, indicators: Any) -> str:
        # D1：engine 未配線。金曜引けは週セグメントの end_of_test 清算が担う。
        # 曜日判定で close する誤実装を禁止。
        return "hold"

    def _round_volume(self, raw: float) -> float:
        step = float(_cfg_get(self._config, "volume_step", 0.0))
        if step <= 0:
            return raw
        return round(round(raw / step) * step, 8)
