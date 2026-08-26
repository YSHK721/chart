"""StopEntryProbe 戦略（StrategyPort 実装・原典 simulator/tests/confirmation/2026-04_stop-probe/ea.mq5）。

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
    - ロットは OnInit で **1 回だけ** NormalizeLot(Lot, vmin, vmax, vstep) して g_lot に保持し、
      発注（BuyStop/SellStop）は保持値を使う（ISSUE-445 段階 3-B）。Lot<=0 または
      正規化結果<=0 は INIT_PARAMETERS_INCORRECT＝起動失敗であり、Python 側では on_init が
      ConfigError を送出する（MaSlope.on_init が SL/TP>0 を拒否するのと同じ扱い）。
      本 EA の NormalizeLot は MA_Slope_EA.mq5 / 2026-03_ma-limit/ea.mq5 の同名関数とは
      **別物**（vstep<=0 の扱いと digits 式が異なる）であり共通化しない
      （差異は tests/unit/test_normalize_lot_originals_diverge.py が固定する）。
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

import math
from typing import Any

from simulator.domain.exceptions import ConfigError
from simulator.domain.order import Order
from simulator.usecase.ports import StrategyPort


class StopEntryProbe(StrategyPort):
    """逆指値プローブ EA の移植（両建て BuyStop+SellStop・OCO・再アーム・SL/TP 付き）。"""

    def __init__(self) -> None:
        self._config: dict | None = None
        self._lot: float = 0.0

    def on_init(self, config: Any, indicators: Any) -> None:
        # 原典 OnInit:53-57 — 正規化前の入力値そのものを検査する。
        lot = float(config["lot_size"])
        if lot <= 0.0:
            raise ConfigError(
                "StopEntryProbe は lot_size > 0 を要求します"
                "（原典 2026-04_stop-probe/ea.mq5:53 INIT_PARAMETERS_INCORRECT）",
                context={"lot_size": lot},
            )
        # 原典 OnInit:63-73 — 銘柄仕様に丸めた実効ロットを起動時に 1 回だけ確定して保持する。
        self._config = config
        g_lot = self._normalize_lot(
            lot,
            vmin=self._spec_value(config, "volume_min"),
            vmax=self._spec_value(config, "volume_max"),
            vstep=self._spec_value(config, "volume_step"),
        )
        if g_lot <= 0.0:
            self._config = None
            raise ConfigError(
                "StopEntryProbe はロットを確定できません"
                "（原典 2026-04_stop-probe/ea.mq5:69 INIT_PARAMETERS_INCORRECT）",
                context={
                    "lot_size": lot,
                    "volume_min": self._spec_value(config, "volume_min"),
                    "volume_step": self._spec_value(config, "volume_step"),
                },
            )
        self._lot = g_lot

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
        # 原典 :138 / :149 — 発注は OnInit で確定した g_lot を使う（再正規化しない）。
        return Order(
            side=side, kind=kind, volume=self._lot, price=price, sl=sl, tp=tp
        )

    def _normalize_lot(
        self, lot: float, *, vmin: float, vmax: float, vstep: float
    ) -> float:
        """原典 ``2026-04_stop-probe/ea.mq5:159 NormalizeLot(lot, vmin, vmax, vstep)`` の移植。

        原典（1:1・条件も境界も足さない／削らない）::

            if(vstep <= 0.0) vstep = (vmin > 0.0) ? vmin : 0.01;
            double v = MathRound(lot / vstep) * vstep;
            if(v < vmin) v = vmin;
            if(vmax > 0.0 && v > vmax) v = vmax;
            int digits = (int)MathMax(0.0, MathCeil(-MathLog10(vstep) - 1e-9));
            return(NormalizeDouble(v, digits));

        ``MA_Slope_EA.mq5`` / ``2026-03_ma-limit/ea.mq5`` の同名関数と**混同しない**:
        あちらは ``step <= 0`` で丸めをスキップし ``digits`` を 2 に固定する。ここは
        ``vstep`` を置換して必ず丸め、``digits`` は 1e-9 のイプシロンを引いてから切り上げる。
        """
        if vstep <= 0.0:
            vstep = vmin if vmin > 0.0 else 0.01
        v = self._math_round(lot / vstep) * vstep
        if v < vmin:
            v = vmin
        if vmax > 0.0 and v > vmax:
            v = vmax
        digits = int(max(0.0, math.ceil(-math.log10(vstep) - 1e-9)))
        return self._normalize_double(v, digits)

    @staticmethod
    def _math_round(x: float) -> float:
        """MQL5 ``MathRound``（絶対値 0.5 を切り上げ＝ゼロから遠ざかる丸め）。

        Python 組込み ``round`` は銀行家丸め（実測: round(2.5)==2 / round(0.5)==0）で
        原典と境界の挙動が食い違うため使わない。
        """
        magnitude = math.floor(abs(x))
        if abs(x) - magnitude >= 0.5:
            magnitude += 1.0
        return math.copysign(magnitude, x)

    @classmethod
    def _normalize_double(cls, value: float, digits: int) -> float:
        """MQL5 ``NormalizeDouble(value, digits)``（指定小数桁への丸め・0.5 は切り上げ）。"""
        scale = 10.0**digits
        return cls._math_round(value * scale) / scale

    @staticmethod
    def _spec_value(cfg: Any, key: str) -> float:
        try:
            return float(cfg[key])
        except KeyError:
            return 0.0

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
