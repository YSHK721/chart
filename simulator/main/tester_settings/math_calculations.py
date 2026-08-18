"""`Math calculations`（`Model=3`）の実行経路（内部設計 §8.2・D-09）。

1. 層名/責務:
    main 層（Composition Root）。バー系列を伴わない実行を、既存ファイルを 1 行も
    変えずに成立させる**追加専用の経路**。`build_interactor` を経由せず
    （`data_path` が必須引数であるため）、`RunBacktestInteractor` へ空のバー列と
    Null Port 3 点を直接注入する。統計の算出は既存 `compute_stats` に委ねる
    （集計の単一ソース性を保つ）。

2. 含む構造:
    run_math_calculations（実行 B）: 実効設定 ＋ 注入束 → (終了コード, 結果, メタ)。

3. 元 MQL 対応:
    Settings タブ Modelling の `Math calculations`。MT5 はティックを生成せず
    OnTester のみを評価する（基本設計 §4.5.2）。

4. 依存:
    標準: typing
    外部: なし
    プロジェクト内: simulator.main.tester_settings.exit_codes（成功終了コードの単一宣言）/
                    simulator.framework.config_loader（load_config）/
                    simulator.usecase.run_backtest（Interactor・Request）/
                    simulator.adapter.strategy.null_strategy /
                    simulator.adapter.indicator.null_registry /
                    simulator.adapter.execution.null_tick_model /
                    simulator.main.tester_settings.kwargs_mapper

`TICK_MODEL_REGISTRY` へ 5 件目を追加しない理由（§8.2.2 で棄却した代替案 A）:
    レジストリへの追加は `config_loader` の許容値（`TICK_MODEL_IDS`）を変え、既存
    テスト（id 集合と順序を厳密固定）を必ず失敗させる。既存 4 モードの bit-exact を
    守る唯一の手段が「既存に触れない追加専用経路」である。
"""
from __future__ import annotations

from typing import Any

from simulator.adapter.execution.null_tick_model import NullTickModel
from simulator.adapter.indicator.null_registry import NullIndicatorRegistry
from simulator.adapter.strategy.null_strategy import NullStrategy
from simulator.framework.config_loader import load_config
from simulator.main.tester_settings.exit_codes import SUCCESS_EXIT_CODE
from simulator.main.tester_settings.kwargs_mapper import (
    EngineBinding,
    TesterRunMetadata,
    build_run_metadata,
    verify_data_consistency,
)
from simulator.usecase.run_backtest import RunBacktestInteractor, RunBacktestRequest
from simulator.usecase.tester_settings import EffectiveSettings

#: `deposit` は `MATH_CALCULATIONS` で inert（規則 A）。推定値を入れず 0.0 を渡す
#: （`stats.initial_deposit` に 0.0 が現れることはメタ情報で隠さずに示す）。
INERT_DEPOSIT: float = 0.0


def run_math_calculations(
    effective: EffectiveSettings, binding: EngineBinding
) -> "tuple[int, Any, TesterRunMetadata]":
    """実行 B: `Math calculations` を実行して (終了コード, 結果, メタ) を返す。

    事前条件: ``effective.tick_model == TickModel.MATH_CALCULATIONS``（規則 S）。
    事後条件: 例外なく終了し ``stats.trades == 0``・終了コード 0。
    例外: E-03（`SettingsActivationError`・規則 S 違反）。

    渡す値の根拠:
        config          : `load_config({})`＝既定値（`tick_model` は既定 "every_tick" の
                          まま。ティックを生成しないため結果に影響しないが、この事実は
                          `TesterRunMetadata.tick_model` に "math_calculations" として残す）。
        bars            : 空列（ティック非生成の実体。メインループが 0 回になる）。
        symbol_spec     : 注入された銘柄仕様（inert な `symbol` から導出しない）。
        initial_deposit : 0.0（`deposit` は inert のため推定値を入れない）。
    """
    verify_data_consistency(effective, has_data=binding.data_path is not None)
    request = RunBacktestRequest(
        config=load_config({}),
        bars=[],
        symbol_spec=binding.symbol_spec,
        initial_deposit=INERT_DEPOSIT,
        stop_out_level=binding.stop_out_level,
    )
    interactor = RunBacktestInteractor(
        strategy=NullStrategy(),
        indicators=NullIndicatorRegistry(),
        tick_model=NullTickModel(),
    )
    return SUCCESS_EXIT_CODE, interactor.execute(request), build_run_metadata(effective)
