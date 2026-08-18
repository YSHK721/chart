"""`Math calculations`（`Model=3`）の実行入口（A-1 で経路を一本化・ISSUE-397）。

1. 層名/責務:
    main 層（Composition Root）。**呼出側の契約を保つためだけの薄い入口**であり、
    実行の知識を 1 つも持たない。実体は `run_from_settings.run_effective_settings`
    （唯一の実行段）へ委譲する。

2. 含む構造:
    run_math_calculations（実行 B・薄い入口）: 実効設定 ＋ 注入束 → (終了コード, 結果, メタ)。
    INERT_DEPOSIT: `kwargs_mapper` の同名定数の再 export（宣言の実体は写像層が持つ）。

3. 元 MQL 対応:
    Settings タブ Modelling の `Math calculations`。MT5 はティックを生成せず
    OnTester のみを評価する（基本設計 §4.5.2）。

4. 依存:
    標準: typing
    外部: なし
    プロジェクト内: simulator.main.tester_settings.kwargs_mapper（EngineBinding /
                    TesterRunMetadata / INERT_DEPOSIT）/
                    simulator.main.tester_settings.run_from_settings（実行段）/
                    simulator.usecase.tester_settings（EffectiveSettings）

A-1 で追加専用経路を撤去した理由（旧 §8.2.2 代替案 A の反転）:
    旧経路は「`TICK_MODEL_REGISTRY` へ 5 件目を追加しない」ために `build_interactor` を
    迂回し、`RunBacktestInteractor` へ空バー列と Null Port を直接注入していた。その結果
    (a) UI（`POST /sim/jobs` → `run_backtest`）から math を投入できず（L-1）、
    (b) `BacktestConfig.tick_model` が既定 `"every_tick"` のままになっていた（L-5）。
    依頼者承認によりレジストリ追加が可能になったため、実行経路を 1 本に戻した。

    L-1 の解消は**実測で成立している**（フェーズ 4 差し戻し 🔴-1 の是正後）:
        `POST /sim/jobs` → 202 → 子プロセス `run_job` → `status="completed"` ＋
        `report.json`（3099 bytes・`summary.single.trades == 0`）が生成され、
        `report_payload_error.json` は残らない。

    ただし**レジストリ追加だけでは成立しなかった**ことを記録する。A-1 時点では
    `build_ea_indicators` が選択規則（`_select_ea_factory`）を経由せず `_EA_FACTORIES` を
    生で引いていたため、`run_job` の `_supply_contacts` が
    ``DataError: 指標計算用 CSV の読み込みに失敗しました: None`` になり、終了コード 0・
    `status="completed"` のまま report.json が生成されなかった（変異試験で再現済み）。
    成立の条件は「選択規則の判定点が 1 つであること」であり、それを
    `simulator/tests/integration/test_ea_factory_selection_rule.py` が AST で機械的に固定する。

    本モジュールを残す理由: `run_math_calculations` は `main.tester_settings` の公開名
    （`__init__` の `__all__`）であり、**例外を送出する**契約（規則 S 違反は E-03 を
    そのまま伝播）を持つ。終了コードへ翻訳する `run_from_settings` では代替できないため、
    公開契約を壊さずに実行段へつなぐ入口として残す。実行の知識は持たないので、
    経路の二重化は残っていない（`RunBacktestInteractor` の直接組立は本ファイルから消えた）。
"""
from __future__ import annotations

from typing import Any

from simulator.main.tester_settings.kwargs_mapper import (
    INERT_DEPOSIT,
    EngineBinding,
    TesterRunMetadata,
)
from simulator.main.tester_settings.run_from_settings import run_effective_settings
from simulator.usecase.tester_settings import EffectiveSettings

__all__ = ["INERT_DEPOSIT", "run_math_calculations"]


def run_math_calculations(
    effective: EffectiveSettings, binding: EngineBinding
) -> "tuple[int, Any, TesterRunMetadata]":
    """実行 B: `Math calculations` を実行して (終了コード, 結果, メタ) を返す。

    事前条件: ``effective.tick_model == TickModel.MATH_CALCULATIONS``（規則 S）。
    事後条件: 例外なく終了し ``stats.trades == 0``・終了コード 0。
    例外: E-03（`SettingsActivationError`・規則 S 違反）ほか実行段が送出するもの。

    渡す値の根拠（すべて実行段＝写像層の規則が決める。本入口は値を作らない）:
        symbol / period / initial_deposit / leverage : inert なので `EngineBinding` が権威。
        data_path                                     : ``None``（バー系列を供給しない）。
        bars                                          : 空列（`NullMarketDataRepository`）。
        tick_model                                    : ``"math_calculations"``（L-5 の解消）。
    """
    return run_effective_settings(effective, binding)
