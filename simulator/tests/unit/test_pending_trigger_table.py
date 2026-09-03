"""ペンディング約定のトリガ条件が 1 つの表に集約されていること（ISSUE-479 Wave2 フェーズ 1-A）。

固定する仕様:
    指値 / 逆指値 4 種別のトリガ条件は `PENDING_TRIGGERS` という**表**が唯一の
    宣言であり、種別の文字列（"buy_limit" 等）は表の外に 1 つも現れない。
    `fill_pending_order` は表を引いて評価するだけで、種別ごとの分岐を持たない。

なぜ表にするか（OCP）:
    条件が if/elif の連鎖として書かれていると、種別を 1 つ増やす作業が「分岐の追加」
    になる。分岐の追加は既存分岐の読み直しを伴い、順序（どの elif が先か）という
    本来無関係な性質に意味が生まれる。表なら追加は行の追加で閉じ、順序は無意味になる。

    さらに種別の文字列が分岐条件として散ると、`simulator/domain/order.py` が持つ
    語彙（Order の kind 集合）との対応が人間の記憶に依存する。表にすれば
    `test_the_table_covers_exactly_the_pending_kinds_declared_by_domain` が
    「domain が種別を増やしたのに表が増えていない」を機械的に赤にできる。

なぜ計算量を測るか:
    トリガ評価は resting（未約定ペンディング）の本数 × 評価点の回数だけ発行される。
    表引きが「全種別を試してから 1 つ選ぶ」形に退化しても出力は正しいままなので、
    状態検証では原理的に落ちない。発行回数を数えて「1 注文 1 評価点あたり 1 回」
    （発行 − 使用 = 0）を固定する。

対象外:
    fill_buy_limit（expire 付きの別関数・既存経路）は本サイクルで触らない。
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

from simulator.domain.order import Order, _KINDS
from simulator.usecase._execution import PENDING_TRIGGERS, fill_pending_order
from simulator.usecase.pending_lifecycle import PendingLifecycleEngine

_EXECUTION_SOURCE = Path(__file__).resolve().parents[2] / "usecase" / "_execution.py"

#: domain が宣言するペンディング種別（成行を除く）。表の期待値の唯一の出所。
_PENDING_KINDS = frozenset(_KINDS) - {"market"}


def _order(kind: str, price: float = 100.0) -> Order:
    side = "buy" if kind.startswith("buy") else "sell"
    return Order(side=side, kind=kind, volume=1.0, price=price)


class TestThePendingTriggerTableIsTheSingleDeclaration:
    """トリガ条件の宣言が表 1 つに集約されていること。"""

    def test_the_table_covers_exactly_the_pending_kinds_declared_by_domain(self):
        assert set(PENDING_TRIGGERS) == set(_PENDING_KINDS)

    def test_no_pending_kind_literal_appears_outside_the_table(self):
        """種別の文字列が表の外に 0 個（分岐条件として散っていない）。"""
        source = _EXECUTION_SOURCE.read_text(encoding="utf-8")
        tree = ast.parse(source, filename=str(_EXECUTION_SOURCE))

        table_ranges = [
            (node.lineno, getattr(node, "end_lineno", node.lineno))
            for node in tree.body
            if isinstance(node, (ast.Assign, ast.AnnAssign))
            and any(
                isinstance(target, ast.Name) and target.id == "PENDING_TRIGGERS"
                for target in (
                    node.targets if isinstance(node, ast.Assign) else [node.target]
                )
            )
        ]
        assert table_ranges, "PENDING_TRIGGERS の宣言が見つからない"

        outside = [
            (node.value, node.lineno)
            for node in ast.walk(tree)
            if isinstance(node, ast.Constant)
            and node.value in _PENDING_KINDS
            and not any(lo <= node.lineno <= hi for lo, hi in table_ranges)
        ]
        assert outside == [], (
            "種別リテラルが表の外に現れています（分岐の再生）:\n  "
            + "\n  ".join(f"{_EXECUTION_SOURCE.name}:{line} {value}" for value, line in outside)
        )


class TestFillPendingOrderReadsTheTable:
    """表引きの結果が各種別の MT5 トリガ条件と一致すること（境界含む）。"""

    @pytest.mark.parametrize(
        "kind,bid,ask",
        [
            ("buy_limit", 99.5, 100.0),    # Ask == price（境界）
            ("sell_limit", 100.0, 100.5),  # Bid == price（境界）
            ("buy_stop", 100.0, 100.0),    # Ask == price（境界）
            ("sell_stop", 100.0, 100.5),   # Bid == price（境界）
        ],
        ids=["buy_limit", "sell_limit", "buy_stop", "sell_stop"],
    )
    def test_the_boundary_quote_fills_at_the_order_price(self, kind, bid, ask):
        position = fill_pending_order(_order(kind), bid=bid, ask=ask)
        assert position is not None
        assert position.entry_price == 100.0

    @pytest.mark.parametrize(
        "kind,bid,ask",
        [
            ("buy_limit", 99.6, 100.1),    # Ask > price
            ("sell_limit", 99.9, 100.4),   # Bid < price
            ("buy_stop", 99.4, 99.9),      # Ask < price
            ("sell_stop", 100.1, 100.6),   # Bid > price
        ],
        ids=["buy_limit", "sell_limit", "buy_stop", "sell_stop"],
    )
    def test_a_quote_short_of_the_boundary_does_not_fill(self, kind, bid, ask):
        assert fill_pending_order(_order(kind), bid=bid, ask=ask) is None

    def test_a_kind_absent_from_the_table_never_fills(self):
        """表に無い種別（成行）は None（None ガード）。"""
        assert fill_pending_order(_order("market"), bid=1.0, ask=1.0) is None


class TestThePendingTriggerEvaluationDoesNotWasteWork:
    """計算量検定（Test Spy・発行 − 使用 = 0）。測るのは時間ではなく回数。"""

    @staticmethod
    def _count_trigger_calls(monkeypatch) -> "list[str]":
        """表の各条件を計数ラッパで包む（表の同一性は保つ＝setitem）。"""
        calls: "list[str]" = []
        for kind, predicate in list(PENDING_TRIGGERS.items()):
            def counted(bid, ask, price, _kind=kind, _inner=predicate):
                calls.append(_kind)
                return _inner(bid, ask, price)

            monkeypatch.setitem(PENDING_TRIGGERS, kind, counted)
        return calls

    def test_one_trigger_evaluation_per_resting_order_per_point(self, monkeypatch):
        calls = self._count_trigger_calls(monkeypatch)
        resting = [_order("buy_limit", price=90.0)]   # 未到達（約定させない）
        filled, carried = PendingLifecycleEngine.evaluate_triggers(
            resting, bid=100.0, ask=100.0, oco=False
        )
        # 発行（トリガ評価）− 使用（評価した注文）= 0。試しては捨てる評価が 1 件も無い。
        assert len(calls) - len(resting) == 0
        assert filled == [] and len(carried) == len(resting)

    @pytest.mark.parametrize("resting_count", [1, 8], ids=["resting_1", "resting_8"])
    def test_the_issue_count_is_determined_by_the_resting_count_alone(
        self, monkeypatch, resting_count
    ):
        """resting 1 / 8 の 2 点で「発行数 == 注文数」（オーダーの表明・種別数に非比例）。"""
        calls = self._count_trigger_calls(monkeypatch)
        resting = [_order("buy_limit", price=90.0) for _ in range(resting_count)]
        PendingLifecycleEngine.evaluate_triggers(
            resting, bid=100.0, ask=100.0, oco=False
        )
        assert len(calls) - resting_count == 0

    def test_only_the_matching_kind_is_evaluated(self, monkeypatch):
        """他種別の条件を試して捨てる形（全種別走査）に退化していないこと。"""
        calls = self._count_trigger_calls(monkeypatch)
        fill_pending_order(_order("sell_stop"), bid=100.0, ask=100.5)
        assert calls == ["sell_stop"]
