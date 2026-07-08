"""報告ドメインモデル DTO（詳細設計 §5・§3.1）。

すべて素の ``@dataclass``（stdlib のみ依存）。ステージ① で payload 形状を全確定し、
後段 ②〜⑤ で形状不変とする（F-1 で segments.{is,oos} を全て含める）。
遅延フィールド（orders/agg の heat/scatter/graph 詳細等）は空/最小で確保する。
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class TradeRow:
    """trades[] 1 行（詳細設計 §4.1・16キー）。"""

    id: int
    side: str
    entry_time: int
    exit_time: int
    entry_price: float
    exit_price: float
    profit: float
    volume: str
    sl: str
    tp: str
    order: int
    comment: str
    balance: float
    hold_sec: int
    mfe: float
    mae: float


@dataclass
class OrderRow:
    """orders[] 1 行（SPEC §2.2.2 11列互換・詳細設計 §4.4）。"""

    open_time: int
    order: int
    symbol: str
    type: str
    volume: str
    price: float
    sl: str
    tp: str
    time: int
    state: str
    comment: str


@dataclass
class SummaryModel:
    """summary.{seg}（詳細設計 §4.8・§5.3）。"""

    trades: int
    net: float
    final_balance: float
    win_rate: float
    profit_factor: float
    expectancy: float
    payoff: float
    return_pct: float
    max_dd_pct: float


@dataclass
class SegmentModel:
    """segments.{is,oos}（詳細設計 §5.2）。

    report/orders/agg はステージ① では遅延キー（空/最小）を確保する。
    """

    label: str
    meta: dict
    report: dict
    bars: list
    trades: list  # list[TradeRow]
    orders: list  # list[OrderRow]（ステージ① は空配列）
    agg: dict


@dataclass
class VerdictModel:
    """verdict（詳細設計 §5.3）。"""

    result: str
    reasons: list


@dataclass
class ReportPayloadModel:
    """ReportPayload 全体（詳細設計 §5.1）。"""

    meta: dict
    segments: dict  # {"is": SegmentModel, "oos": SegmentModel}
    summary: dict  # {"is": SummaryModel, "oos": SummaryModel}
    degradation: dict
    verdict: VerdictModel
    contract_notes: list = field(default_factory=list)
