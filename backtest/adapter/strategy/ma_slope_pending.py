"""MA_Slope_Pending 戦略（StrategyPort 実装・原典 backtest/tests/confirmation/2026-03_ma-limit/ea.mq5）。

既存 :class:`backtest.adapter.strategy.ma_slope.MaSlope`（成行）と同一の slope シグナルを
用いつつ、注文方式を「指値（limit）/逆指値（stop）」へ切替えるペンディング版 EA。
MT5 原典が ``MA_Slope_EA.mq5`` と ``MA_Slope_Pending_EA.mq5`` の別ファイルである構成を
忠実に踏襲し、本アダプタは新規ファイルとして並存させる（成行アダプタは無変更）。

シグナル（原典 OnTick・MaSlope と同一）:
    確定足 slope = ema[bar_index-1] − ema[bar_index-1-SlopeShift]
    threshold = SlopeMinPts × point_size
    slope >  threshold → 買い / slope < −threshold → 売り / それ以外 → 様子見([])

ペンディング発注（原典 OpenPending / CalcSlTp）:
    現値は当該バー始値クォート（bid=open / ask=open+spread×point）で評価する
    （実 MT5 は新規バー先頭ティック=open で OnTick が走る）。
        指値  : Buy = ask − offset / Sell = bid + offset（不利側で待つ）
        逆指値: Buy = ask + offset / Sell = bid − offset（有利側で待つ）
    offset = EntryOffsetPts × point（stops_level×point を下限にクランプ）。
    SL/TP はペンディング価格基準（Buy: sl=price−SLd, tp=price+TPd / Sell は対称）。
    価格・SL・TP は digits で丸める（原典 NormalizeDouble）。

毎バーのライフサイクル（原典）:
    自 EA の未約定ペンディングを毎バー取消して最新シグナルで再設置する。本契約では
    「on_new_bar が返す Order 列＝そのバスで保持すべきペンディング」と定義し、空 list を
    返すと Interactor が既存ペンディングを取消す（cancel-and-replace）。同方向保有時は
    [] を返し（何もしない）、逆方向保有時はペンディング 1 件を返す（Interactor が bar open
    で逆玉を成行決済＝ドテン後、ペンディングを設置する）。

ポート契約（StrategyPort・MaSlope と同一）:
    on_new_bar(bar_index, indicators, account) -> list[Order]
    indicators.get("ema"/"open"/"spread") は pandas.Series（.iloc で位置参照）。
    config は subscript アクセス（RunConfig）。account.open_positions を duck typing で読む。
"""
from __future__ import annotations

from typing import Any

from backtest.domain.order import Order
from backtest.usecase.ports import StrategyPort


class MaSlopePending(StrategyPort):
    """EMA 傾き戦略のペンディング版（指値/逆指値・SL/TP 付き・反転はドテン）。"""

    def __init__(self) -> None:
        self._config: dict | None = None
        self._indicators: Any = None

    def on_init(self, config: Any, indicators: Any) -> None:
        self._config = config
        self._indicators = indicators

    def on_new_bar(self, bar_index: int, indicators: Any, account: Any) -> "list[Order]":
        cfg = self._config
        slope_shift = cfg["slope_shift"]
        # 境界: 確定足 ema[bar_index-1] と ema[bar_index-1-slope_shift] の 2 点が要る。
        if bar_index < 1 + slope_shift:
            return []

        ema = indicators.get("ema")
        recent = ema.iloc[bar_index - 1]
        past = ema.iloc[bar_index - 1 - slope_shift]
        slope = recent - past
        point = cfg["point_size"]
        threshold = cfg["slope_min_points"] * point

        if slope > threshold:
            signal = "buy"
        elif slope < -threshold:
            signal = "sell"
        else:
            return []  # signal==0: ペンディングを設置しない（Interactor が取消）

        # 同方向を既に保有していれば何もしない（原典 signal==current）。
        if signal in self._held_sides(account):
            return []

        # 当該バー始値クォート（bid=open / ask=open+spread×point）で現値を評価する。
        open_ = float(indicators.get("open").iloc[bar_index])
        spread_pts = float(indicators.get("spread").iloc[bar_index])
        bid = open_
        ask = open_ + spread_pts * point
        return [self._build_pending(signal, bid=bid, ask=ask)]

    def on_position_check(self, position: Any, bar_index: int, indicators: Any) -> str:
        # 反転はシグナルで実施。SL/TP は Order に載せ Interactor が監視する。
        return "hold"

    # --- 内部 ---------------------------------------------------------------

    @staticmethod
    def _held_sides(account: Any) -> set[str]:
        if account is None:
            return set()
        return {p.side for p in getattr(account, "open_positions", [])}

    def _build_pending(self, side: str, *, bid: float, ask: float) -> Order:
        cfg = self._config
        point = cfg["point_size"]
        digits = cfg["digits"]
        offset = cfg["entry_offset_points"] * point
        min_dist = cfg["stops_level"] * point
        if offset < min_dist:  # ブローカー最小ストップ距離を下限に確保（原典）
            offset = min_dist

        entry_type = cfg["entry_type"]
        if entry_type == "limit":
            price = (ask - offset) if side == "buy" else (bid + offset)
            kind = "buy_limit" if side == "buy" else "sell_limit"
        elif entry_type == "stop":
            price = (ask + offset) if side == "buy" else (bid - offset)
            kind = "buy_stop" if side == "buy" else "sell_stop"
        else:
            raise ValueError(f"未知の entry_type: {entry_type!r}")

        price = round(price, digits)
        sl, tp = self._calc_sltp(side, price)
        return Order(
            side=side, kind=kind, volume=cfg["lot_size"], price=price, sl=sl, tp=tp
        )

    def _calc_sltp(self, side: str, price: float) -> "tuple[float | None, float | None]":
        """基準価格から SL/TP を算出（points==0 で None・原典 CalcSlTp）。"""
        cfg = self._config
        point = cfg["point_size"]
        digits = cfg["digits"]
        min_dist = cfg["stops_level"] * point

        sl: float | None = None
        tp: float | None = None
        if cfg["stop_loss_points"] > 0:
            dist = cfg["stop_loss_points"] * point
            if dist < min_dist:
                dist = min_dist
            sl = round((price - dist) if side == "buy" else (price + dist), digits)
        if cfg["take_profit_points"] > 0:
            dist = cfg["take_profit_points"] * point
            if dist < min_dist:
                dist = min_dist
            tp = round((price + dist) if side == "buy" else (price - dist), digits)
        return sl, tp
