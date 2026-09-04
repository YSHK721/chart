"""MA_Slope 戦略（StrategyPort 実装・原典 simulator/tests/fixtures/mt5/ma_slope_jp225_202501/expert/MA_Slope_EA.mq5）。

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
    ロットは原典 NormalizeLot(Lot) で銘柄仕様（VOLUME_MIN/MAX/STEP）に合わせて正規化し、
    0 以下なら発注しない（原典 OpenPosition の分岐・ISSUE-445 段階 1）。銘柄仕様は
    config の volume_min / volume_max / volume_step で供給する（未供給は制約なし）。
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

import math
from typing import Any

from simulator.adapter.strategy.mql5_runtime import (
    math_round,
    normalize_double,
    spec_value,
)
from simulator.domain.exceptions import ConfigError
from simulator.domain.order import Order
from simulator.usecase.ports import StrategyPort


class MaSlope(StrategyPort):
    """EMA 傾き戦略（傾き上向き→買い・下向き→売り・反転はドテン）。"""

    def __init__(self) -> None:
        self._config: dict | None = None
        self._indicators: Any = None

    def on_init(self, config: Any, indicators: Any) -> None:
        # 本 EA は SL/TP 無し（原典 MA_Slope_EA.mq5 は StopLoss=TakeProfit=0）。
        # StrategyPort 契約（on_new_bar）に無い暗黙事前条件を on_new_bar 経路の
        # NotImplementedError で強制すると LSP 不成立（ISSUE-098 🟡-2）。SL/TP>0 は
        # 起動前（on_init）にドメイン例外 ConfigError で明示拒否する。
        if config["stop_loss_points"] > 0 or config["take_profit_points"] > 0:
            raise ConfigError(
                "MaSlope は stop_loss_points/take_profit_points > 0 を未サポートです"
                "（本 EA は SL/TP 無し・ISSUE-098 🟡-2）",
                context={
                    "stop_loss_points": config["stop_loss_points"],
                    "take_profit_points": config["take_profit_points"],
                },
            )
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
        order = self._build_order(signal)
        # 原典 OpenPosition: NormalizeLot が 0 以下なら発注せず戻る（Print して return）。
        return [] if order is None else [order]

    def on_position_check(self, position: Any, bar_index: int, indicators: Any) -> str:
        # 反転はシグナルで実施する（on_position_check は SL/TP 監視用）。
        return "hold"

    # --- 内部 ---------------------------------------------------------------

    @staticmethod
    def _held_sides(account: Any) -> set[str]:
        if account is None:
            return set()
        return {p.side for p in getattr(account, "open_positions", [])}

    def _build_order(self, side: str) -> "Order | None":
        cfg = self._config
        volume = self._normalize_lot(cfg["lot_size"])
        if volume <= 0.0:
            # 原典 OpenPosition: 有効なロット数を算出できない場合は発注しない。
            return None
        # 本 EA は SL/TP 無し（None）。SL/TP>0 は on_init が ConfigError で拒否済みの
        # ため、ここへ到達する config は必ず SL/TP=0（ISSUE-098 🟡-2 の LSP 是正）。
        return Order(
            side=side,
            kind="market",
            volume=volume,
            price=None,
            sl=None,
            tp=None,
        )

    def _normalize_lot(self, lot: float) -> float:
        """原典 MA_Slope_EA.mq5:NormalizeLot(lot) の移植（ISSUE-445 段階 1）。

        銘柄仕様は config（strategy_params）の volume_min / volume_max / volume_step
        で供給する。未供給時は 0.0＝制約なしとして原典の非正値分岐に載せる。

        MQL5 プリミティブ（MathRound / NormalizeDouble / 銘柄仕様の読み取り）は
        :mod:`simulator.adapter.strategy.mql5_runtime` が単独で所有する。本メソッドは
        参照するだけで再実装しない（ISSUE-445・複製の再発は AST ゲートが赤にする）。
        本メソッド自体は原典 EA ごとに挙動が異なるため共通化しない
        （``tests/unit/test_normalize_lot_originals_diverge.py`` が非同値を固定）。
        """
        cfg = self._config
        step = spec_value(cfg, "volume_step")
        volume_min = spec_value(cfg, "volume_min")
        v = lot
        if step > 0.0:
            v = math_round(v / step) * step
        if v < volume_min:
            v = volume_min
        volume_max = spec_value(cfg, "volume_max")
        if volume_max > 0.0 and v > volume_max:
            v = volume_max
        # 浮動小数の誤差を除去（ステップの桁数で正規化）— 原典 NormalizeDouble(v, digits)。
        digits = int(math.ceil(-math.log10(step))) if step > 0.0 else 2
        if digits < 0:
            digits = 0
        return normalize_double(v, digits)
