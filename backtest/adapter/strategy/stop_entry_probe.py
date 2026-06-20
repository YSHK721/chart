"""StopEntryProbe 戦略（StrategyPort 実装・原典 backtest/tests/confirmation/2026-04_stop-probe/ea.mq5）。

逆指値（Stop）注文の動作確認専用 EA を Python へ移植したアダプタ。MA シグナルに依存せず、
フラットになるたび現値の上下へ両建ての逆指値（BuyStop / SellStop）を一度だけ設置し、
片側が約定したら反対側を取消す（OCO）。約定玉が SL/TP で決済されフラットへ戻ると再装填する。

原典挙動（OnTick / PlaceProbeOrders / CancelOpposite / EnableReArm）:
    - PROBE_BOTH 固定（本突合 ProbeDir=2）。BuyStop=Ask+offset / SellStop=Bid−offset。
    - 発注クォートは「OnTick が走ったその足途中ティックの bid/ask」（実 MT5 は positions==0 &&
      pendings==0 のティックで PlaceProbeOrders を呼ぶ）。よって SL/TP 決済直後の実ティック価格で
      即再アームする（ISSUE-024: バー始値ではなく決済が起きた制御点のクォートを使う）。
    - offset = EntryOffsetPts × point（stops_level×point を下限にクランプ）。
    - SL/TP はペンディング価格基準（Buy: sl=price−SLd, tp=price+TPd / Sell は対称）。
    - 設置は「一度だけ」。約定するまで同一価格の注文を保持し続ける（原典は pendings>0 の間
      再設置しない）＝持続モード（config: pending_persistent）で Interactor が resting を約定まで保持。

ポート契約（StrategyPort）:
    on_new_bar(...) -> []          バー境界では何もしない（本 EA は OnTick で発注）。
    on_tick(bar_index, bid, ask, account) -> list[Order]
        Interactor が「保有0・resting 0」のティックでのみ呼ぶ。当該ティッククォートで両建て
        逆指値 2 件を装填して返す（ステートレス＝engine が呼ぶ条件で持続/再アームを制御）。
    config は subscript アクセス（RunConfig）。

前提（config）:
    pending_lifecycle=True（ペンディング経路）/ pending_persistent=True（resting 保持＋on_tick 再アーム）
    / pending_oco=True（1 本約定で兄弟取消＝原典 CancelOpposite）。三者揃って原典挙動を再現する。
"""
from __future__ import annotations

from typing import Any

from backtest.domain.order import Order
from backtest.usecase.ports import StrategyPort


class StopEntryProbe(StrategyPort):
    """逆指値プローブ EA の移植（両建て BuyStop+SellStop・OCO・再アーム・SL/TP 付き）。"""

    def __init__(self) -> None:
        self._config: dict | None = None

    def on_init(self, config: Any, indicators: Any) -> None:
        self._config = config

    def on_new_bar(self, bar_index: int, indicators: Any, account: Any) -> "list[Order]":
        # 本 EA は OnTick（足途中ティック）で発注する＝バー境界では何もしない。装填・再アームは
        #   on_tick が担う（config: pending_persistent 必須）。よって常に [] を返す。
        return []

    def on_tick(self, bar_index: int, bid: float, ask: float, account: Any) -> "list[Order]":
        # Interactor は「保有0・resting 0」のティックでのみ本メソッドを呼ぶ（実 MT5 の
        #   positions==0 && pendings==0 のとき PlaceProbeOrders に相当）。当該ティッククォートで
        #   両建て逆指値を装填して返す（以後 Interactor が約定まで保持＝持続モード）。ステートレス。
        return [
            self._build_stop("buy", bid=bid, ask=ask),
            self._build_stop("sell", bid=bid, ask=ask),
        ]

    def on_position_check(self, position: Any, bar_index: int, indicators: Any) -> str:
        # SL/TP は Order に載せ Interactor が監視する（戦略側の手動決済なし）。
        return "hold"

    # --- 内部 ---------------------------------------------------------------

    def _build_stop(self, side: str, *, bid: float, ask: float) -> Order:
        cfg = self._config
        point = cfg["point_size"]
        digits = cfg["digits"]
        offset = cfg["entry_offset_points"] * point
        min_dist = cfg["stops_level"] * point
        if offset < min_dist:  # ブローカー最小ストップ距離を下限に確保（原典 g_effOffset）
            offset = min_dist

        # 逆指値: Buy=Ask+offset（上抜け） / Sell=Bid−offset（下抜け）。
        price = (ask + offset) if side == "buy" else (bid - offset)
        kind = "buy_stop" if side == "buy" else "sell_stop"
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
