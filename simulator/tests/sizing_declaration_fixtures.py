"""サイジングの推定建値が**戦略の宣言**から決まることを測る道具（テストヘルパ・非テスト）。

2 つの検定が同じ道具を使う（同じものを手書き複製しない）:
    `simulator/tests/integration/test_entry_price_basis_single_source.py` … 宣言どおりに
        系列が分かれること（状態検証）。
    `simulator/tests/unit/test_entry_price_basis_supply_complexity.py` … 宣言を導く回数が
        規模で増えないこと（計算量検定・絶対命令 2026-08-28）。

含む構造:
    `DeclaringStrategy`  宣言だけを持ち、足ごとに成行を 1 本出す最小の戦略。
    `SeriesSpy`          `indicators.get(名前)` で問われた系列名を記録する Test Spy。
    `Account`            `equity` だけを持つ口座の代役。
    `ask_series`         包まれた戦略へ足を 1 本送り、問われた系列名を返す。

本モジュールは**テストではない**（ファイル名が test で始まらないため pytest は収集しない）。
"""
from __future__ import annotations

from typing import Any

#: 推定建値の系列として配る値（本数は足 1 本ぶんの参照に足りればよい）。
_SERIES_VALUE = 100.0
_SERIES_LENGTH = 8
#: 成行 1 本ぶんの損切り価格（リスク距離が 0 だと `SizingPort` が Fail-Stop する）。
_STOP_LOSS = 90.0


class DeclaringStrategy:
    """判定の瞬間を名乗るだけの戦略。足ごとに成行を ``orders_per_bar`` 本出す。

    宣言を読まれた回数を ``declaration_reads`` に数える（Test Spy）。宣言は run のあいだ
    変わらない 1 つの値なので、何度導き直しても**出力は正しいまま**である。したがって
    無駄の検出は状態検証ではなく回数の検定にしかできない。
    """

    def __init__(self, declared: "str | None", *, orders_per_bar: int = 1) -> None:
        self._declared = declared
        self._orders_per_bar = orders_per_bar
        self.declaration_reads = 0

    @property
    def entry_price_basis(self) -> "str | None":
        self.declaration_reads += 1
        return self._declared

    # ---- StrategyPort（使う 1 点だけを実装し、他は呼ばれたら落とす）----

    def on_init(self, config: Any, indicators: Any) -> None:  # pragma: no cover
        raise AssertionError("本ヘルパの利用者は on_init を通らない")

    def on_position_check(self, position: Any, bar_index: int, indicators: Any) -> str:
        raise AssertionError("本ヘルパの利用者は on_position_check を通らない")

    def on_tick(self, bar_index: int, bid: float, ask: float, account: Any) -> list:
        return []

    def on_new_bar(self, bar_index: int, indicators: Any, account: Any) -> list:
        from simulator.domain.order import Order

        return [
            Order(side="buy", kind="market", volume=1.0, sl=_STOP_LOSS, tp=None, price=None)
            for _ in range(self._orders_per_bar)
        ]


class SeriesSpy:
    """`indicators.get(名前)` で問われた系列名を記録する。"""

    def __init__(self) -> None:
        self.asked: "list[str]" = []

    def get(self, name: str):
        import pandas as pd

        self.asked.append(name)
        return pd.Series([_SERIES_VALUE] * _SERIES_LENGTH)


class Account:
    """`equity` だけを持つ口座の代役。"""

    equity = 100_000.0


class VolumeConstraints:
    """量制約の代役（`build_sizing_decorator` は属性アクセスで読む）。"""

    volume_min = 0.01
    volume_max = 100.0
    volume_step = 0.01


def ask_series(wrapped: Any, *, bar_index: int = 3) -> str:
    """包まれた戦略へ足を 1 本送り、推定建値がどの系列から取られたかを返す。"""
    spy = SeriesSpy()
    wrapped.on_new_bar(bar_index, spy, Account())
    assert spy.asked, "推定建値の系列が 1 度も問われていない"
    return spy.asked[-1]
