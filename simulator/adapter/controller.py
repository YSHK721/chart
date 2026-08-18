"""BacktestController（adapter 層・入口アダプタ）。

構築済 BacktestConfig + データソース参照を受け取り、MarketDataPort 経由でデータを
ロード→RunBacktestInputBoundary（Interactor）へ委譲→発生した例外を終了コードへ
翻訳する（DESIGN §9.4）:

    成功            → 0
    ConfigError     → 2（設定不正・BacktestError サブクラスのため先に捕捉する）
    BacktestError   → 1（DataError 等を含む実行時例外）

翻訳の**規約そのもの**（成功値・対応表・評価順）は本モジュールでは宣言せず、
`simulator.adapter.exit_codes` が唯一の宣言場所である（A-6）。本 controller は
その語彙を読むだけで、表を複製しない。

config.yaml → BacktestConfig の pydantic 検証は framework 層（Section 4）の責務であり、
本 controller は構築済 BacktestConfig を受け取る設計とする（yaml パースは申し送り）。

adapter 層は usecase + domain のみに依存する（framework / main は import しない）。
"""
from __future__ import annotations

from typing import Any

from simulator.adapter.exit_codes import SUCCESS_EXIT_CODE, exit_code_for
from simulator.domain.exceptions import BacktestError
from simulator.usecase.ports import MarketDataPort, RunBacktestInputBoundary
from simulator.usecase.run_backtest import RunBacktestRequest


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

    @property
    def interactor(self) -> RunBacktestInputBoundary:
        """注入されたインタラクタ（読み取り専用の公開取得点）。

        ISSUE-395 / A-5: 「検証した request をそのまま実行する」呼び出し側
        （`main.tester_settings.run_from_settings`）は `run()` を使えない——`run()` は
        `market_data.load` を再実行して別のバー列で `RunBacktestRequest` を組み直すため、
        検証済 request の `trading_start` が落ちる。従来その到達手段が非公開属性
        `_interactor` だったため、本プロパティを公開の取得点として設ける。
        注入した実体をそのまま返し、差し替えは許さない（構築時注入の DI 契約を保つ）。
        """
        return self._interactor

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
            return SUCCESS_EXIT_CODE
        except BacktestError as error:
            # 翻訳規約（ConfigError→2 / BacktestError→1・評価順を含む）は
            # `simulator.adapter.exit_codes` が唯一宣言する（DESIGN §9.4）。
            # `BacktestError` 以外はここで捕捉せず、そのまま呼出側へ送出する。
            return exit_code_for(error)
