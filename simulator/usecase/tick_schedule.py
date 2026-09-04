"""TickSchedule: ティック粒度の評価スケジュール（ISSUE-479 Wave2 4-9・S-1）。

1 ティックにつき評価点を 1 つ生む。価格の取り方は経路で 2 通りある:

    実ティック経路（tick_model="real_ticks"）:
        含み損益と強制決済は tick が持つ Bid/Ask で評価し、SL/TP 到達は tick の価格
        1 点（high=low=price）で見る。買い・売りで範囲は変わらない。

    ペンディング経路（pending_lifecycle）:
        実 MT5 の OHLC クォート規約（bid=ティック価格 / ask=bid+spread×point）を採る。
        SL/TP は**決済サイドのクォート**で判定する——買い保有は Bid、売り保有は Ask。
        ここが両サイドで違う唯一の場所であり、評価点が到達範囲をサイドごとに持つ理由で
        ある（1 組に畳むと売りの SL 発火価格が変わる）。

ティックが 1 本も無いバー:
    実ティック実装はバー区間に該当ティックが 0 件のとき空列を返す（TickModelPort の
    事後条件）。そのバーでも保有玉は値洗いされるべきなので、**直近に見たティックの
    Bid/Ask を持ち越した点**を 1 つ生む。一度もティックを見ていなければ持ち越す値が
    無いので当該バーの終値で評価する。この点は SL/TP も stop-out も見ない——見るべき
    材料（そのバーで実際に成立した価格）が無いためであり、`is_synthetic_bar_point` で
    そのことを示す。記録するかどうか（保有玉が在るか）は呼出側が決める。

スケジュールが状態を持つ理由:
    「直近に見たティックの Bid/Ask」はバーをまたいで持ち越す。これはティック列を辿る側の
    状態なので、スケジュール自身が持つのが素直である（呼出側に持たせると、呼出側が
    ティック列の詳細を知ることになる）。スケジュールは run につき 1 つ作る。

usecase 層は domain のみ依存可。本モジュールは同層の evaluation_point / ports /
pending_lifecycle のみ参照する。
"""
from __future__ import annotations

from typing import Any, Iterable

from simulator.usecase.evaluation_point import (
    NOT_A_TICK,
    TICK_GRANULARITY,
    EvaluationPoint,
)
from simulator.usecase.pending_lifecycle import PendingLifecycleEngine
from simulator.usecase.ports import EvaluationSchedulePort


class TickSchedule(EvaluationSchedulePort):
    """1 ティック 1 点を生むスケジュール（ティック 0 件のバーは持ち越し点を 1 つ）。"""

    #: このスケジュールの名前。
    id = TICK_GRANULARITY

    def __init__(
        self, *, tick_model: Any, pending_lifecycle: bool, point_size: float
    ) -> None:
        self._tick_model = tick_model
        self._pending_lifecycle = pending_lifecycle
        self._point_size = point_size
        # 直近に見たティックの Bid/Ask（ティック 0 件バーの持ち越し用）。
        self._last_bid: "float | None" = None
        self._last_ask: "float | None" = None

    def points(
        self, bar_index: int, bar: Any, prev_close: "float | None"
    ) -> "Iterable[EvaluationPoint]":
        """当該バーの評価点を生む（ティック 1 本につき 1 点）。

        事前条件: `prev_close` は前足の終値（ティック合成に用いる実装がある）。
        事後条件: ティックが在ればその本数ぶんの点、無ければ持ち越し点 1 つを生む。
        ティック列は**バーにつき 1 回**だけ取りに行く。
        """
        saw_tick = False
        for tick_ordinal, tick in enumerate(self._tick_model.ticks_of(bar, prev_close)):
            price, bid, ask, _tick_time = tick
            saw_tick = True
            self._last_bid, self._last_ask = bid, ask
            if self._pending_lifecycle:
                # 実 MT5 OHLC のクォート規約（centered tick の bid/ask は使わない）。
                eval_bid, eval_ask = PendingLifecycleEngine.tick_quote(
                    price, spread=bar.spread, point_size=self._point_size
                )
                # SL/TP は決済サイドのクォートで判定する（買い=Bid / 売り=Ask）。
                hit_buy, hit_sell = eval_bid, eval_ask
            else:
                eval_bid, eval_ask = bid, ask
                # SL/TP は tick の価格 1 点で判定する（買い・売りとも同じ）。
                hit_buy = hit_sell = price
            yield EvaluationPoint(
                bar_index=bar_index,
                bar=bar,
                eval_bid=eval_bid,
                eval_ask=eval_ask,
                hit_buy_high=hit_buy,
                hit_buy_low=hit_buy,
                hit_sell_high=hit_sell,
                hit_sell_low=hit_sell,
                # 建玉変更の参照は保有玉の決済価格（含み損評価と同一基準）。
                pm_ref_buy=eval_bid,
                pm_ref_sell=eval_ask,
                granularity=TICK_GRANULARITY,
                tick_ordinal=tick_ordinal,
            )
        if saw_tick:
            return
        # ティック 0 件バー: 直近既知のクォート（無ければ終値）で 1 点だけ差し出す。
        carry_bid = self._last_bid if self._last_bid is not None else bar.close
        carry_ask = self._last_ask if self._last_ask is not None else bar.close
        yield EvaluationPoint(
            bar_index=bar_index,
            bar=bar,
            eval_bid=carry_bid,
            eval_ask=carry_ask,
            # 到達も参照も持たない（この点では SL/TP も建玉変更も行わない）。
            hit_buy_high=carry_bid,
            hit_buy_low=carry_bid,
            hit_sell_high=carry_ask,
            hit_sell_low=carry_ask,
            pm_ref_buy=carry_bid,
            pm_ref_sell=carry_ask,
            granularity=TICK_GRANULARITY,
            tick_ordinal=NOT_A_TICK,
            is_synthetic_bar_point=True,
        )
