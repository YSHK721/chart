"""BacktestController（adapter 層・入口アダプタ）。

構築済 BacktestConfig + データソース参照を受け取り、MarketDataPort 経由でデータを
ロード→RunBacktestInputBoundary（Interactor）へ委譲→発生した例外を終了コードへ
翻訳する（DESIGN §9.4）:

    成功            → 0
    ConfigError     → 2（設定不正・BacktestError サブクラスのため先に捕捉する）
    BacktestError   → 1（DataError 等を含む実行時例外）

config.yaml → BacktestConfig の pydantic 検証は framework 層（Section 4）の責務であり、
本 controller は構築済 BacktestConfig を受け取る設計とする（yaml パースは申し送り）。

adapter 層は usecase + domain のみに依存する（framework / main は import しない）。
"""
from __future__ import annotations

from typing import Any

from backtest.domain.exceptions import BacktestError, ConfigError
from backtest.usecase.ports import MarketDataPort, RunBacktestInputBoundary
from backtest.usecase.run_backtest import RunBacktestRequest


class BacktestController:
    """ロード→Interactor 委譲→終了コード翻訳を担う入口アダプタ。"""

    def __init__(
        self,
        *,
        market_data: MarketDataPort,
        interactor: RunBacktestInputBoundary,
    ) -> None:
        self._market_data = market_data
        self._interactor = interactor

    def run(
        self,
        config: Any,
        source_ref: Any,
        *,
        timeframe: Any = None,
        period: Any = None,
        symbol_spec: Any = None,
        initial_deposit: float = 0.0,
        stop_out_level: float = 0.0,
    ) -> int:
        """1 run を実行し終了コード（0/1/2）を返す。"""
        try:
            bars = self._market_data.load(source_ref, timeframe, period)
            request = RunBacktestRequest(
                config=config,
                bars=bars,
                symbol_spec=symbol_spec,
                initial_deposit=initial_deposit,
                stop_out_level=stop_out_level,
            )
            self._interactor.execute(request)
            return 0
        except ConfigError:
            # ConfigError は BacktestError サブクラスのため必ず先に捕捉する（DESIGN §9.4）
            return 2
        except BacktestError:
            return 1
