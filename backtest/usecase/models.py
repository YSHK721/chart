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
    # 約定価格基準（実 MT5 突合・後方互換）。既定 "close"＝従来挙動（close 約定・spread 無視）。
    # "current_open"＝原典 .mq5（新規バーで現値約定）に整合: bid=現バー open、
    # 買い=open+spread×point（実 fixture 初回 buy 39412=open39402+100×0.1）・売り=open。
    # default 付きのため既存 9 引数構築（config_loader/既存テスト）と完全後方互換。
    entry_price_basis: str = "close"
    # 証拠金ストップアウト時の挙動（cycle4 で追加）。既定 "fail_stop"＝従来挙動
    # （margin_level < stop_out で MarginCallError を送出し部分結果を破棄）。
    # "close_and_halt"＝全保有玉を強制決済（exit_reason="stop_out"）し、以降の新規発注を
    # 抑止して最終統計まで完走する。default 付きのため既存構築と完全後方互換。
    stop_out_action: str = "fail_stop"
    # 取引開始境界の最初の 1 バーを「アタッチ/プライム」として扱うか（層1・config-gated）。
    # 既定 False＝trading_start 境界バーも取引対象（従来不変）。True かつ trading_start 指定時、
    # bar.time >= trading_start となる最初のバーを warmup 同様「指標 update のみ・発注/equity
    # 除外」とし、初回約定を次足へ落とす（実 MT5 のテスト開始バー=アタッチ挙動に整合）。
    prime_first_trading_bar: bool = False
    # 含み損益の評価基準（層2・config-gated）。既定 "close"＝従来どおり bar.close 固定評価。
    # "bid_ask"＝決済価格基準（買い保有=Bid=close / 売り保有=Ask=close+spread×point_size）。
    # default 付きのため既存構築と完全後方互換。
    floating_pnl_basis: str = "close"


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
    # 実 MT5 golden 突合で追加（report_900005560.json への校正）。
    # 既存コンストラクタ互換のため末尾に default 付きで配置する。
    average_profit_trade: float = 0.0  # gross_profit / profit_trades(>=0)
    average_loss_trade: float = 0.0    # gross_loss / loss_trades(<0)
    z_score: float = 0.0               # Wald-Wolfowitz（MT5 実装式）
    ahpr: float = 0.0                  # mean(1 + profit_i / balance_before_i)
    balance_dd_abs: float = 0.0        # initial_deposit - min(balance)
    # 実 MT5 校正済の equity 系 DD（第2サイクルで compute_stats() 本体へ結線）。
    # equity_curve（含み損込みバー別 equity）由来。equity_curve 未供給時は 0（後方互換）。
    equity_dd_abs: float = 0.0          # initial_deposit - min(equity)
    equity_dd_max: float = 0.0          # equity peak-to-trough の最大金額 DD
    equity_dd_max_percent: float = 0.0  # 金額 DD 最大点での % DD


@dataclass
class BacktestResult:
    """1 run の結果データ。保持のみ（変換責務なし・CLEAN_ARCH §8 違反②解消）。"""

    trades: Any
    deals: Any
    equity_curve: Any
    balance_curve: Any
    stats: BacktestStats
    indicator_values: dict[str, Any] = field(default_factory=dict)
