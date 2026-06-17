"""PRO!fit_Band 戦略（#5 ＝ my_first_ea・StrategyPort 実装・SPEC §3.5・PROCESS §3.4）.

一次情報（原典・今回入手）:
    ``backtest/experts/PRO!fit_Band.mq5``（MetaQuotes "My First EA" 原型・#5 の原典）.
    本モジュールは同 .mq5 の OnTick 判定（買い/売り条件・厳密不等号の全 AND・p_close=
    mrate[1].close・桁補正・SL/TP 式・Bars<60 ゲート・同方向のみ重複抑止・反転決済なし）を
    忠実再現する. SPEC §3.5 は当該原典の写しであり、原典が条件・式・既定値（StopLoss=30/
    TakeProfit=100/ADX_Period=8/MA_Period=8/Adx_Min=22.0/Lot=0.1）の一次情報である.
    原典との一致は ``tests/unit/test_strategy_pro_fit_band.py`` の独立オラクル
    （production 非 import）テストで固定する.

EMA(MA_Period) の傾き + 直前足終値の位置 + ADX(ADX_Period)>Adx_Min + +DI/−DI の大小で
トレンド方向を判定し、新規バー 1 回だけ成行で 1 ポジション売買する.

指標参照（PROCESS §0.3: MQL [0]=最新 → Python iloc[bar_index]・[1]→bar_index-1・[2]→bar_index-2）:
    EMA(MA_Period,close) / ADX(ADX_Period) 本線 / +DI / −DI.
    p_close = mrate[1].close（1 本前の確定終値）= close.iloc[bar_index-1].

エントリ（全条件 AND・厳密不等号・SPEC §3.5）:
    買い: EMA[0]>EMA[1] && EMA[1]>EMA[2] & p_close>EMA[1] & ADX[0]>Adx_Min & +DI[0]>−DI[0]
    売り: EMA[0]<EMA[1] && EMA[1]<EMA[2] & p_close<EMA[1] & ADX[0]>Adx_Min & +DI[0]<−DI[0]

決済（発注時の固定 SL/TP のみ・反転決済なし）:
    桁補正: digits∈{3,5} のとき STP=StopLoss*10, TKP=TakeProfit*10（SPEC §3.5）.
    買い: sl=Ask−STP×point, tp=Ask+TKP×point ／ 売り: sl=Bid+STP×point, tp=Bid−TKP×point.

ポート契約（usecase Interactor に整合・tc24051901 と同形）:
    on_new_bar(bar_index, indicators, account) -> list[Order]
    エントリ基準価格は indicators.get("close").iloc[bar_index]（spread=0 近似で Ask=Bid=close.
    実 spread 反映は spread_model 接続時＝範囲外）. Order は kind="market"・price=None
    （約定価格は execution で解決）、sl/tp は絶対価格.

制限: 同方向ポジ保有時は重複禁止（account.open_positions の side で抑止）.
実行頻度: 新規バー 1 回（on_new_bar が新規バーごとに 1 回呼ばれる前提）.
"""
from __future__ import annotations

from typing import Any

from backtest.domain.order import Order
from backtest.usecase.ports import StrategyPort


class ProFitBand(StrategyPort):
    """EMA 傾き + ADX + DI のトレンド追従戦略（固定 SL/TP・#5 PRO!fit_Band）。"""

    def __init__(self) -> None:
        self._config: dict | None = None
        self._indicators: Any = None

    def on_init(self, config: Any, indicators: Any) -> None:
        self._config = config
        self._indicators = indicators

    def on_new_bar(self, bar_index: int, indicators: Any, account: Any) -> "list[Order]":
        # warmup ゲート（SPEC §3.5「Bars<60 は処理しない」/ PROCESS §2-A・§3.4 step1）。
        # MQL `Bars` は現足[0]含む総本数 → 現足が bar_index のとき総本数=bar_index+1。
        # Bars<min_bars ⟺ bar_index < min_bars-1（現足含め min_bars 本目未満）で処理しない。
        min_bars = self._config.get("min_bars", 60)
        if bar_index < min_bars - 1:
            return []
        if bar_index < 2:  # 境界: EMA[2]（2 本前）が無ければ傾き判定不可
            return []
        ema = indicators.get("ema")
        adx = indicators.get("adx")
        plus_di = indicators.get("plus_di")
        minus_di = indicators.get("minus_di")

        ema0 = ema.iloc[bar_index]
        ema1 = ema.iloc[bar_index - 1]
        ema2 = ema.iloc[bar_index - 2]
        adx0 = adx.iloc[bar_index]
        pdi0 = plus_di.iloc[bar_index]
        mdi0 = minus_di.iloc[bar_index]
        p_close = indicators.get("close").iloc[bar_index - 1]
        adx_min = self._config["adx_min"]
        held_sides = self._held_sides(account)

        # 買い条件（全 AND・厳密不等号）
        if (
            ema0 > ema1 and ema1 > ema2
            and p_close > ema1
            and adx0 > adx_min
            and pdi0 > mdi0
            and "buy" not in held_sides
        ):
            return [self._build_order("buy", indicators, bar_index)]

        # 売り条件（全 AND・厳密不等号）
        if (
            ema0 < ema1 and ema1 < ema2
            and p_close < ema1
            and adx0 > adx_min
            and pdi0 < mdi0
            and "sell" not in held_sides
        ):
            return [self._build_order("sell", indicators, bar_index)]

        return []

    def on_position_check(self, position: Any, bar_index: int, indicators: Any) -> str:
        # 反転決済なし（固定 SL/TP のみ）。
        return "hold"

    # --- 内部 ---------------------------------------------------------------

    @staticmethod
    def _held_sides(account: Any) -> set[str]:
        if account is None:
            return set()
        return {p.side for p in getattr(account, "open_positions", [])}

    def _build_order(self, side: str, indicators: Any, bar_index: int) -> Order:
        cfg = self._config
        price = float(indicators.get("close").iloc[bar_index])
        # 桁補正（SPEC §3.5）: digits∈{3,5} のとき StopLoss/TakeProfit を ×10。
        mult = 10 if cfg["digits"] in (3, 5) else 1
        sl_dist = cfg["stop_loss_points"] * mult * cfg["point_size"]
        tp_dist = cfg["take_profit_points"] * mult * cfg["point_size"]
        if side == "buy":
            sl, tp = price - sl_dist, price + tp_dist
        else:
            sl, tp = price + sl_dist, price - tp_dist
        return Order(
            side=side,
            kind="market",
            volume=cfg["lot_size"],
            price=None,
            sl=sl,
            tp=tp,
        )
