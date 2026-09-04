"""EvaluationPoint: エンジンが口座を再評価する「点」（ISSUE-479 Wave2 4-8・S-1）。

何を解くか:
    移設前、2 つの実行エンジンは「どこで評価するか」と「評価点で何をするか」を混ぜて
    持っていた。バー用は `bar.close` を見て、ティック用はティック価格を見て、それぞれ
    自前に H（SL/TP 到達判定）・建玉変更・I（口座再評価）を書いていた。同じ手続きの
    2 つの写しなので、片方だけが変わる形の食い違いが起こりうる。

    評価点を**値**として取り出すと、「何をするか」は 1 つの手続きになり、「どこで」だけが
    スケジュールの差になる。粒度の違いは点の中身（価格の取り方）に畳み込まれる。

なぜ SL/TP 到達の範囲がサイドごとに 2 つあるか:
    到達判定に使う価格は粒度で意味が変わる。

      * バー粒度: 当該バーの極値（high/low）。買い・売りとも同じ範囲を見る。
      * ティック粒度（実ティック）: そのティックの価格 1 点。買い・売りとも同じ。
      * ティック粒度（ペンディング経路）: **決済サイドのクォート**。買い保有は Bid、
        売り保有は Ask で判定する（実 MT5 整合。売りの SL は high ティックの
        Ask=high+spread×point で発火する）。

    最後の 1 つだけがサイドで違うため、範囲はサイドごとに持つ。1 組しか持たないと
    ペンディング経路の判定価格を表現できず、束ねた瞬間に数値が動く。

usecase 層は domain のみ依存可。本モジュールは何も import しない純粋な値である。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any


#: 評価粒度の名前。建玉変更（PositionManagerPort）へもこの名前で伝える。
BAR_GRANULARITY = "bar"
TICK_GRANULARITY = "tick"

#: バー点が持つティック序数（＝ティックではない）。
NOT_A_TICK = -1


@dataclass(frozen=True)
class EvaluationPoint:
    """1 回の口座再評価に必要な事実すべて（粒度に依らない形）。

    ここに載っているのは「その点で価格がどう見えるか」だけであり、口座・保有玉・記録先は
    載せない。点は run の状態を知らないので、同じ点を何度評価しても点自身は変わらない。
    """

    #: 点が属するバーの位置と、そのバー。
    bar_index: int
    bar: Any

    #: 含み損益の評価と、強制決済に使うクォート。
    eval_bid: float
    eval_ask: float

    #: SL/TP 到達を見る価格範囲（買い保有用）。
    hit_buy_high: float
    hit_buy_low: float

    #: SL/TP 到達を見る価格範囲（売り保有用）。ペンディング経路だけが買いと異なる。
    hit_sell_high: float
    hit_sell_low: float

    #: 建玉変更（トレーリング）へ渡す参照価格。
    pm_ref_buy: float
    pm_ref_sell: float

    #: 評価粒度（BAR_GRANULARITY / TICK_GRANULARITY）。
    granularity: str

    #: バー内でのティックの序数（0=open）。バー点は NOT_A_TICK。
    #: ペンディング約定玉は「この序数より後」のティックからのみ SL/TP 監視する。
    tick_ordinal: int = NOT_A_TICK

    #: ティックが 1 本も無いバーで、保有玉の評価だけを行うために立てた点か。
    #: この点は SL/TP も stop-out も見ない（評価する材料が無いため）。
    is_synthetic_bar_point: bool = False
