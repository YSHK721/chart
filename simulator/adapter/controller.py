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
from simulator.usecase.models import AccountSpec
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

    @property
    def market_data(self) -> MarketDataPort:
        """注入された MarketDataPort 実装（読み取り専用の公開取得点）。

        🟡-5: 「どの実装が注入されたか」「その実装で読み直すと何が返るか」を確かめる
        呼び出し側（取得窓の合成デコレータ検定＝`tests/integration/
        test_marketdata_window_mt5_path.py`）が、非公開属性 `_market_data` へ到達して
        いた。`interactor` と同型の公開プロパティを設け、到達手段を契約の内側に置く。
        注入した実体をそのまま返し、差し替えは許さない（構築時注入の DI 契約を保つ）。
        """
        return self._market_data

    def execute(self, request: RunBacktestRequest) -> Any:
        """組立済 request をそのまま 1 run 実行し、**結果を返す**。

        ISSUE-398 / SRP: `run()` は「データ取得」「1 run 実行」「終了コード翻訳」の
        3 責務を 1 メソッドに畳んでいた。本メソッドは中央の 1 責務（実行）だけを担う。

        終了コード翻訳を**行わない**のは意図的である。`run_from_settings` が既に明記する
        規律——「検証だけを行いたい呼出しが終了コードを解釈し直さずに済む」——に従い、
        例外は翻訳せずそのまま送出する。翻訳が要る呼出側は `exit_codes.exit_code_for`
        （唯一の宣言場所）を自分で呼ぶ。

        事前条件: `request` は実行可能な `RunBacktestRequest`（bars を含む）。
        事後条件: インタラクタの戻り値をそのまま返す（値を加工しない）。
        例外: インタラクタが送出した例外をそのまま伝播する。

        「組立済 request を実行する」呼出側（MT5 突合・IS/OOS・レポート出力・
        `run_from_settings`）は従来これを非公開属性 `_interactor` 経由で行っていた
        （カプセル化の破れ）。本メソッドがその公開の到達点である。
        """
        return self._interactor.execute(request)

    def _build_request(
        self,
        config: Any,
        source_ref: Any,
        *,
        timeframe: Any,
        period: Any,
        symbol_spec: Any,
        account: AccountSpec,
    ) -> RunBacktestRequest:
        """データ取得段: `source_ref` を読み `RunBacktestRequest` を組む。

        非公開に留める理由（ISSUE-398 §3）: 取得点は `market_data` プロパティが既に
        公開しており、ここを公開すると**同一操作に 2 つの入口**ができる。`run()` が
        自分の引数から request を組むための内部手続きに閉じる。

        `account`（ISSUE-445 段階 3-D3）は口座の契約であり **既定値を置かない**。
        初期証拠金・必要証拠金の除数・ストップアウト水準をここで発明すると、人が書いた
        値が権威になる（RC-1 と同型）。呼出側が値を持たないなら、その呼出は証拠金計算を
        伴う run を組めていない。
        """
        return RunBacktestRequest(
            config=config,
            bars=self._market_data.load(source_ref, timeframe, period),
            symbol_spec=symbol_spec,
            account=account,
        )

    def run(
        self,
        config: Any,
        source_ref: Any,
        *,
        timeframe: Any = None,
        period: Any = None,
        symbol_spec: Any = None,
        account: AccountSpec,
    ) -> int:
        """1 run を実行し終了コード（0/1/2）を返す。

        「ロード → request 組立 → 実行 → 翻訳」の順に 2 段（`_build_request` /
        `execute`）へ割り、本メソッドは**翻訳**だけを自分の責務として残す。
        戻り値・例外伝播は従来と同一である。

        `account` はキーワード必須（既定値なし・ISSUE-445 段階 3-D3）。段階 3-D2 まで
        `initial_deposit` / `stop_out_level` は既定 0.0 を持っていたが、それは
        「人が書いた値を既定として持つ」形（RC-1 と同型）の残渣であり、口座の契約を
        1 つの型へ束ねた本段階で消えた。**本番の呼出は 0 件**（`run_backtest` は
        `execute(request)` を直接呼ぶ・実測 2026-08-27）。
        """
        try:
            self.execute(
                self._build_request(
                    config,
                    source_ref,
                    timeframe=timeframe,
                    period=period,
                    symbol_spec=symbol_spec,
                    account=account,
                )
            )
            return SUCCESS_EXIT_CODE
        except BacktestError as error:
            # 翻訳規約（ConfigError→2 / BacktestError→1・評価順を含む）は
            # `simulator.adapter.exit_codes` が唯一宣言する（DESIGN §9.4）。
            # `BacktestError` 以外はここで捕捉せず、そのまま呼出側へ送出する。
            return exit_code_for(error)
