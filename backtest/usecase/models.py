"""usecase 層のプレーン DTO（CLEAN_ARCH §9 / 依頼仕様）。

すべて素の ``@dataclass``（pydantic 非依存）。検証は adapter 境界（framework の
pydantic）に閉じ、本層はデータ保持のみを責務とする。BacktestResult は to_html /
to_markdown / compare 等の変換責務を持たない（CLEAN_ARCH §8 依存方向違反②・③の解消）。

usecase 層は domain のみ依存可（adapter/framework/main を import しない）。
時刻は domain と同じく ``numpy.datetime64 | int``（pd.Timestamp を前提にしない）。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class BacktestConfig:
    """PROCESS §7 決定論性チェックリスト 9 項目を保持する設定 DTO。

    #1 tick_model / #2 spread_model / #3 sltp_tie(=SL 優先) / #4 fill_delay(=次tick) /
    #5 ohlc_order / #6 session_calendar / #7 digits / #8 legacy_quirks / #9 return_basis。
    """

    tick_model: str
    spread_model: str
    sltp_tie: str
    fill_delay: str
    ohlc_order: str
    session_calendar: str
    digits: int
    legacy_quirks: bool
    return_basis: str


@dataclass
class SymbolSpec:
    """シンボル仕様。domain.order.Order.validate が duck typing で要求する属性
    （volume_min/volume_max/volume_step/stops_level/point_size）を満たす。
    """

    contract_size: float
    volume_min: float
    volume_max: float
    volume_step: float
    stops_level: int
    digits: int
    point_size: float
    leverage: float


@dataclass
class BacktestStats:
    """METRICS の STAT_* と 1:1 のフィールド + 計算値（§1〜§4）。"""

    # §1 損益サマリー
    initial_deposit: float
    profit: float
    gross_profit: float
    gross_loss: float
    profit_factor: float
    recovery_factor: float
    expected_payoff: float
    sharpe_ratio: float
    # §3 件数・分布
    trades: int
    profit_trades: int
    loss_trades: int
    long_trades: int
    short_trades: int
    profit_long_trades: int
    profit_short_trades: int
    # §2 ドローダウン（Balance 系）
    balance_min: float
    balance_dd: float
    balance_dd_percent: float
    balance_dd_relative: float
    balance_ddrel_percent: float
    # §4 個別トレード統計
    max_profit_trade: float
    max_loss_trade: float
    max_con_wins: int
    max_con_profit_trades: float
    max_con_losses: int
    max_con_loss_trades: float
    con_profit_max: float
    con_profit_max_trades: int
    con_loss_max: float
    con_loss_max_trades: int
    profit_trades_avg_con: float
    loss_trades_avg_con: float


@dataclass
class BacktestResult:
    """1 run の結果データ。保持のみ（変換責務なし・CLEAN_ARCH §8 違反②解消）。"""

    trades: Any
    deals: Any
    equity_curve: Any
    balance_curve: Any
    stats: BacktestStats
    indicator_values: dict[str, Any] = field(default_factory=dict)
