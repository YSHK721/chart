"""EmaDeviationShort 戦略（StrategyPort 実装・EMA 上方乖離の水準で売る EA・依頼 2026-10-06）。

売買規則（依頼者裁定 2026-10-06）:
    エントリー: **形成中の足**の価格 P が、形成中の EMA（期間 ``ma_period``）から
        ``ema_deviation_pct`` % 以上上方へ離れた瞬間に売る（足の途中で触れたら約定）。
    決済: TP は 21EMA（依頼者指示 2026-10-07）。足 k で形成中の EMA に触れる価格は ema[k-1]
        （EMA 固有の同値・接点スキャンの参照実装と同じ）なので、TP は ema[k-1] を刻みへ切り捨てた
        値。建てるときに付け、保有中は新しい足ごとに動かす（`retarget_positions`）。
        SL は固定（``stop_loss_points``・0 は置かない）。``take_profit_points`` は使わない。

形成中の線に触れる価格（接点スキャンの規約・`simulator/usecase/contact_scan/spec.py`）:
    形成中の EMA は ``ema_i(P) = a·P + (1−a)·ema[i-1]``（a = 2/(ma_period+1)）。
    ``P >= (1+d)·ema_i(P)`` を P について解くと、``1 − (1+d)·a > 0`` のもとで
        P >= ema[i-1] · (1+d)(1−a) / (1 − (1+d)·a)
    となる。右辺は足の始まりに確定している値だけで決まるので、足 i の始まりに
    この価格へ指値売りを置けば「形成中の線に触れたら約定」になる。d = 0 では
    参照実装の水準 ``ma[i-1]`` に戻る（EMA 固有の同値）。ema[i-1]×(1+d) ではない
    （21EMA・8% で係数 1.088710・合成 80,000 点で定義との食い違い 0 を実測）。
    指値は刻みへ**切り上げ**て丸める（切り捨てると乖離 d 未満で約定しうる）。

足の始まりで既に水準以上の場合:
    MT5 は Bid より下の sell_limit を受け付けない（invalid price）。触れた時点は足の
    始まりなので、始値 Bid の成行売りにする。

保有中は新規に建てない（売り 1 玉まで）。待機注文は毎足取り消して置き直す（1 足寿命）。

水準の系列（`threshold_series`・2026-10-06）:
    ``THRESHOLD_SERIES`` の足 k の値は ema[k] × 係数＝「次の足で乖離 d に触れる価格」。束縛が
    1 回だけ作り、戦略は確定足 i-1 の値を読んで刻みへ切り上げ（`limit_prices`）、指値にする。
    売買履歴チャートは同じ系列を 1 本ずらして同じ関数で切り上げた ``LEVEL_SERIES``（足 k の
    指値価格）を描く（係数の式も切り上げの式も 2 か所に書かない・依頼者指示 2026-10-07）。
"""
from __future__ import annotations

import math
from typing import Any

from simulator.domain.exceptions import ConfigError
from simulator.domain.order import Order
from simulator.domain.volume_step import floor_to_step
from simulator.usecase.pending_order_use import PendingOrderUse
from simulator.usecase.ports import EntryPriceBasisPort, PositionRetargetPort, StrategyPort


#: 次の足の水準の系列名（足 k の値＝ema[k] × 係数・戦略が確定足 i-1 で読む）。
THRESHOLD_SERIES = "deviation_threshold"
#: 足 k の水準の系列名（``THRESHOLD_SERIES`` を 1 本ずらしたもの・チャートが描く）。
LEVEL_SERIES = "deviation_level"


def limit_prices(levels: Any, *, point_size: float, digits: int) -> Any:
    """水準を指値の刻みへ切り上げる（戦略の指値とチャートの線が呼ぶ唯一の式）。

    切り上げるのは、切り捨てると乖離 d 未満で約定しうるため。配列で受けて配列で返す
    （戦略は 1 要素で呼ぶ）。同じ関数を通すので、指値とチャートの線は値が必ず一致する。
    NaN はそのまま NaN。
    """
    import numpy as np

    values = np.asarray(levels, dtype=float)
    return np.round(np.ceil(np.round(values / point_size, 9)) * point_size, int(digits))


def take_profit_prices(levels: Any, *, point_size: float, digits: int) -> Any:
    """売りの TP を刻みへ切り捨てる（線に触れたときにだけ決済する・`limit_prices` と対の式）。"""
    import numpy as np

    values = np.asarray(levels, dtype=float)
    return np.round(np.floor(np.round(values / point_size, 9)) * point_size, int(digits))


def threshold_series(ema: Any, *, ma_period: int, deviation_pct: float) -> Any:
    """足 k の値＝次の足で形成中 EMA から乖離に触れる価格（ema[k] × 係数）。"""
    return ema * touch_coefficient(ma_period, deviation_pct)


def touch_coefficient(ma_period: int, deviation_pct: float) -> float:
    """形成中 EMA から ``deviation_pct`` % 上方に触れる価格 ÷ ema[i-1]。"""
    a = 2.0 / (int(ma_period) + 1)
    up = 1.0 + float(deviation_pct) / 100.0
    denominator = 1.0 - up * a
    if denominator <= 0.0:
        raise ConfigError(
            "EmaDeviationShort: この ma_period と乖離率では、形成中の EMA から離れる価格が存在しません",
            context={"ma_period": ma_period, "ema_deviation_pct": deviation_pct},
        )
    return up * (1.0 - a) / denominator


class EmaDeviationShort(StrategyPort, EntryPriceBasisPort, PositionRetargetPort):
    """EMA 上方乖離の水準に触れたら売り、固定 SL/TP で決済する EA。"""

    #: 判定の瞬間（`EntryPriceBasisPort`）。読むのは確定足 i-1 の水準と当該足の open だけで、
    #: いずれも足の**始まり**に確定している。
    entry_price_basis = "current_open"

    #: 待機注文の使い方（ISSUE-557）。毎足、未約定の指値を取り消して置き直す（1 足寿命）。
    pending_order_use = PendingOrderUse(persistent=False, oco=False)

    def __init__(self) -> None:
        self._config: Any = None
        self._lot: float | None = None

    def on_init(self, config: Any, indicators: Any) -> None:
        self._config = config
        requested = float(config["lot_size"])
        if requested <= 0.0:
            raise ConfigError(
                f"EmaDeviationShort の lot_size={config['lot_size']} は正でありません",
                context={"lot_size": config["lot_size"]},
            )
        minimum = float(config["volume_min"])
        try:
            lot = floor_to_step(
                max(requested, minimum),
                step=float(config["volume_step"]),
                minimum=minimum,
                maximum=float(config["volume_max"]),
            )
        except ValueError as exc:
            raise ConfigError(
                "EmaDeviationShort は lot_size を銘柄の刻みへ丸められません",
                context={"reason": str(exc)},
            ) from None
        if lot is None:
            raise ConfigError(
                "EmaDeviationShort は最小ロットを刻みへ丸めると発注可能な量になりません",
                context={"volume_min": config["volume_min"], "volume_step": config["volume_step"]},
            )
        self._lot = lot

    def on_new_bar(self, bar_index: int, indicators: Any, account: Any) -> "list[Order]":
        if self._lot is None:
            raise ValueError("EmaDeviationShort requires on_init() before on_new_bar().")
        if bar_index < 1:  # 前足の EMA が無い（参照実装と同じく先頭足は飛ばす）
            return []
        if "sell" in self._held_sides(account):
            return []
        # 系列名は文字列で書く（建値基準の宣言と読み方の照合は、読む系列を字面で数える）。
        level = float(indicators.get("deviation_threshold").iloc[bar_index - 1])
        if math.isnan(level):
            return []
        level = float(
            limit_prices(
                [level],
                point_size=float(self._config["point_size"]),
                digits=int(self._config["digits"]),
            )[0]
        )
        bid = float(indicators.get("open").iloc[bar_index])
        tp = self._tp_at(bar_index, indicators)
        if bid >= level:
            return [self._order("market", None, basis=bid, tp=tp)]
        return [self._order("sell_limit", level, basis=level, tp=tp)]

    def on_position_check(self, position: Any, bar_index: int, indicators: Any) -> str:
        return "hold"  # 決済は Order に載せた SL/TP（エンジンが監視する）

    # --- 内部 ---------------------------------------------------------------

    @staticmethod
    def _held_sides(account: Any) -> set[str]:
        if account is None:
            return set()
        return {p.side for p in getattr(account, "open_positions", [])}

    def _order(
        self, kind: str, price: "float | None", *, basis: float, tp: "float | None"
    ) -> Order:
        return Order(
            side="sell", kind=kind, volume=self._lot, price=price, sl=self._sl(basis), tp=tp
        )

    def retarget_positions(
        self, bar_index: int, indicators: Any, position: Any
    ) -> "tuple[float | None, float | None]":
        """保有中の売りの TP を、この足の 21EMA（ema[bar_index-1] を刻みへ切り捨て）へ動かす。"""
        if position.side != "sell":
            return None, None
        return None, self._tp_at(bar_index, indicators)

    def _tp_at(self, bar_index: int, indicators: Any) -> "float | None":
        """足 bar_index で形成中の EMA に触れる価格（ema[bar_index-1]）を刻みへ切り捨てた TP。"""
        if bar_index < 1:
            return None
        ema_prev = float(indicators.get("ema").iloc[bar_index - 1])
        if math.isnan(ema_prev):
            return None
        return float(
            take_profit_prices(
                [ema_prev],
                point_size=float(self._config["point_size"]),
                digits=int(self._config["digits"]),
            )[0]
        )

    def _sl(self, price: float) -> "float | None":
        """売りの SL（points==0 は None・最小距離は stops_level・MaSlopePending と同じ規則）。"""
        cfg = self._config
        if cfg["stop_loss_points"] <= 0:
            return None
        point = float(cfg["point_size"])
        min_dist = cfg["stops_level"] * point
        return round(price + max(cfg["stop_loss_points"] * point, min_dist), int(cfg["digits"]))
