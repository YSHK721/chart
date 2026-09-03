"""BarSchedule: バー粒度の評価スケジュール（ISSUE-479 Wave2 4-8・S-1）。

1 バーにつき評価点を 1 つ生む。価格は当該バーの OHLC から取る:

    * SL/TP 到達は極値（high/low）で見る——バーの中でどこまで届いたかを表す唯一の情報。
    * 建玉変更の参照はトレーリング方向の到達価格（買い=high / 売り=low）。到達判定と
      対称にする（close を参照にすると、SL/TP は当たったのにトレーリングは動かない、
      という非対称が生まれる）。
    * 含み損益と強制決済のクォートは評価基準（floating_pnl_basis）が決める。

`prev_close` は受け取るが使わない。前足の終値はティックを合成するために要る値であって、
バーの評価そのものには関わらないためである（スケジュールの入口は粒度を問わず同じ形に
しておき、呼出側が粒度で分岐しなくて済むようにする）。

usecase 層は domain のみ依存可。本モジュールは同層の evaluation_point / ports /
_execution のみ参照する。
"""
from __future__ import annotations

from typing import Any, Iterable

from simulator.usecase._execution import resolve_eval_quote
from simulator.usecase.evaluation_point import (
    BAR_GRANULARITY,
    NOT_A_TICK,
    EvaluationPoint,
)
from simulator.usecase.ports import EvaluationSchedulePort


class BarSchedule(EvaluationSchedulePort):
    """1 バー 1 点を生むスケジュール。"""

    #: このスケジュールの名前（どの粒度で走った run かを結果から辿れるようにする）。
    id = BAR_GRANULARITY

    def __init__(self, *, floating_pnl_basis: str, point_size: float) -> None:
        # 評価基準と銘柄の刻みは run のあいだ変わらないので構築時に閉じる
        # （点を作るたびに config を引き直さない）。
        self._floating_pnl_basis = floating_pnl_basis
        self._point_size = point_size

    def points(
        self, bar_index: int, bar: Any, prev_close: "float | None"
    ) -> "Iterable[EvaluationPoint]":
        """当該バーの評価点を生む（バー粒度はちょうど 1 つ）。

        事後条件: 生まれた点は 1 つで、`bar_index` / `bar` はそのまま載る。点は
        **求められて初めて**作る（先回りして作り置きしない）。
        """
        eval_bid, eval_ask = resolve_eval_quote(
            bar, basis=self._floating_pnl_basis, point_size=self._point_size
        )
        yield EvaluationPoint(
            bar_index=bar_index,
            bar=bar,
            eval_bid=eval_bid,
            eval_ask=eval_ask,
            # 到達は極値で見る（買い・売りとも同じ範囲）。
            hit_buy_high=bar.high,
            hit_buy_low=bar.low,
            hit_sell_high=bar.high,
            hit_sell_low=bar.low,
            # トレーリングの参照は到達価格（買い=high / 売り=low）。
            pm_ref_buy=bar.high,
            pm_ref_sell=bar.low,
            granularity=BAR_GRANULARITY,
            tick_ordinal=NOT_A_TICK,
        )
