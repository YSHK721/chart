"""MA_Slope 戦略（StrategyPort 実装・原典 backtest/tests/fixtures/mt5/ma_slope_jp225_202501/expert/MA_Slope_EA.mq5）。

EMA(MA_Period, close) の傾きで売買するシンプルな EA。新規バーのみ処理し、確定足
（ArraySetAsSeries(true) の ma[1]=直近確定足）を参照する（リペイント回避）。

シグナル（原典 OnTick）:
    確定足 slope = ema[bar_index-1] − ema[bar_index-1-SlopeShift]
        （原典: ma[1] − ma[1+SlopeShift]。SlopeShift=1 で ma[1]−ma[2]）
    threshold = SlopeMinPts × point_size
    slope >  threshold → 買い / slope < −threshold → 売り / それ以外 → 様子見([])
反転（原典）:
    現在ポジ方向 current（同方向のみ）。signal==current → 何もしない。
    current!=0 かつ signal≠current → ドテン: 反対側の成行 Order を 1 件返す
    （既存玉の決済は Interactor の反転決済ロジックに委ねる）。
発注:
    成行（kind="market"・price=None。約定価格・スプレッドは execution で解決）。
    SL/TP は stop_loss_points / take_profit_points が 0 のとき None（本 EA は SL/TP 無し）。
境界:
    bar_index < (1 + SlopeShift) は確定足 2 点が引けず []。

ポート契約（usecase Interactor に整合）:
    on_new_bar(bar_index, indicators, account) -> list[Order]
    indicators.get("ema") は pandas.Series（.iloc で位置参照）。
    config は subscript アクセス（dict 様。既存戦略・RunConfig と同契約）。
    account は Interactor が実 Account を渡す（run_backtest.py の on_new_bar 呼び出し）。
    tc24051901 と同じく duck typing で open_positions を読む。テスト等で account=None が
    渡された場合は保有なしとみなす（_held_sides が空集合を返す後方互換のため None 許容）。
"""
from __future__ import annotations

from typing import Any

from backtest.domain.order import Order
from backtest.usecase.ports import StrategyPort


class MaSlope(StrategyPort):
    """EMA 傾き戦略（傾き上向き→買い・下向き→売り・反転はドテン）。"""

    def __init__(self) -> None:
        self._config: dict | None = None
        self._indicators: Any = None

    def on_init(self, config: Any, indicators: Any) -> None:
        self._config = config
        self._indicators = indicators

    def on_new_bar(self, bar_index: int, indicators: Any, account: Any) -> "list[Order]":
        cfg = self._config
        slope_shift = cfg["slope_shift"]
        # 境界: 確定足 ema[bar_index-1] と ema[bar_index-1-slope_shift] の 2 点が要る
        if bar_index < 1 + slope_shift:
            return []

        ema = indicators.get("ema")
        recent = ema.iloc[bar_index - 1]
        past = ema.iloc[bar_index - 1 - slope_shift]
        slope = recent - past
        threshold = cfg["slope_min_points"] * cfg["point_size"]

        if slope > threshold:
            signal = "buy"
        elif slope < -threshold:
            signal = "sell"
        else:
            return []

        # 現在の保有方向（原典 current）。signal==current は何もしない。
        held_sides = self._held_sides(account)
        if signal in held_sides:
            return []
        # 反対方向保有はドテン（反対側成行を返し interactor が反転決済）/ 無保有は新規。
        return [self._build_order(signal)]

    def on_position_check(self, position: Any, bar_index: int, indicators: Any) -> str:
        # 反転はシグナルで実施する（on_position_check は SL/TP 監視用）。
        return "hold"

    # --- 内部 ---------------------------------------------------------------

    @staticmethod
    def _held_sides(account: Any) -> set[str]:
        if account is None:
            return set()
        return {p.side for p in getattr(account, "open_positions", [])}

    def _build_order(self, side: str) -> Order:
        cfg = self._config
        # 本 EA は StopLoss/TakeProfit=0 のため SL/TP 無し（None）。SL/TP 付き
        # （points>0）の絶対価格化は price=None のため execution 側の責務であり、
        # cycle 1 の対象外（原典 .../expert/MA_Slope_EA.mq5 は StopLoss=TakeProfit=0）。
        sl = None if cfg["stop_loss_points"] == 0 else _stop_level_unsupported()
        tp = None if cfg["take_profit_points"] == 0 else _stop_level_unsupported()
        return Order(
            side=side,
            kind="market",
            volume=cfg["lot_size"],
            price=None,
            sl=sl,
            tp=tp,
        )


def _stop_level_unsupported() -> None:
    # cycle 1 範囲外: SL/TP 付き（points>0）は別サイクルで対応する。
    raise NotImplementedError(
        "stop_loss_points/take_profit_points > 0 は cycle 1 の対象外です"
    )
