"""評価粒度の選択規則（ISSUE-479 Wave2 4-11・O-1）。

何を解くか:
    「この run は足の途中に評価点を要するか」を決める条件が、移設前は実行経路に
    書かれた文字列比較だった:

        if getattr(config, "tick_model", None) == "real_ticks" or getattr(
            config, "pending_lifecycle", False
        ):

    この形には 2 つの欠陥がある:

    1. 条件を 1 つ増やすには実行経路そのものを開いて書き足す必要がある（OCP 違反）。
    2. 判定の全体像が実行経路の途中に埋もれ、「いま何が粒度を決めているのか」を読むに
       は本体を追うしかない。

    条件を 1 つの表へ出すと、追加は表への 1 行になり、規則は表を見れば分かる。

なぜ真偽値の条件を「== True」で書かないか:
    移設前の判定は `or features.pending_lifecycle` という**真偽評価**だった。`== True`
    に置き換えると、真だが `True` ではない値（duck-typed config が持ちうる）で判定が
    変わる。表の各行は「属性名」と「その値をどう見るか」の組にして、移設前の見方を
    そのまま保つ。

本モジュールは adapter を import しない（スケジュールの実物を知らない）。ここが決めるのは
「足の途中の点が要るか否か」だけであり、どのスケジュールを使うかは呼出側が決める。

usecase 層は domain のみ依存可。本モジュールは何も import しない純粋な判定である。
"""
from __future__ import annotations

from typing import Any, Callable


def _is_real_ticks(value: Any) -> bool:
    """実ティックを消費する run か（合成ティックでは足の途中の点は要らない）。"""
    return value == "real_ticks"


#: 足の途中に評価点を要する条件の表（属性名 → その値の見方）。
#: ここに 1 行足せば新しい条件が効く。実行経路は開かない。
TICK_GRANULARITY_TRIGGERS: "tuple[tuple[str, Callable[[Any], bool]], ...]" = (
    # 実ティックはティックごとに値洗いする必要がある。
    ("tick_model", _is_real_ticks),
    # ペンディング（指値・逆指値）は足の途中で水準に触れたら約定する。引く機会が
    #   足の途中に無ければ、そもそも注文として意味を持たない。
    ("pending_lifecycle", bool),
)


def requires_tick_granularity(features: Any) -> bool:
    """この run が足の途中の評価点を要するか。

    事前条件: `features` は表が挙げた属性を持つ（run のスイッチ束が満たす）。
    事後条件: いずれかの条件が成立すれば True。**成立した時点で残りは見ない**
    （判定に要らない属性は読まない）。
    """
    for name, matches in TICK_GRANULARITY_TRIGGERS:
        if matches(getattr(features, name)):
            return True
    return False
