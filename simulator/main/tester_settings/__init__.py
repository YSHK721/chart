"""Settings 変換・実行層の公開面（API-06 / API-07 と実行 facade）。

1. 層名/責務:
    main 層（Composition Root）。`.ini` から読み込んだ設定（`TesterSettings`）を
    現行エンジンの投入契約へ写し、実行する経路の**唯一の公開窓口**。呼出側は
    ここに列挙したシンボルだけを参照し、下位モジュールの物理配置に依存しない。
    既存ファイル（`main/__init__.py` / `run_backtest.py` / `tick_model_registry.py`）は
    1 行も変えずに**追加のみ**で構成する（OCP）。

2. 含む構造:
    注入束・メタ : EngineBinding / TesterRunMetadata / build_run_metadata
    変換        : to_interactor_kwargs（API-06）
    期間        : DataWindow / resolve_data_window（API-07）/ verify_window_applied
    非対象      : UnsupportedRule / RULES / RUN_REQUEST_RULES / apply_unsupported_rules
    EA 入力     : EaInputBinding / EA_INPUT_BINDINGS / scalar_converter_for /
                  bind_ea_inputs / ea_stem
    実行        : run_from_settings（実行 A）/ run_math_calculations（実行 B）

3. 元 MQL 対応:
    Settings タブの内容で 1 パスを実行する経路（Start ボタン）に対応する。

4. 依存:
    標準: なし（再エクスポートのみ）
    外部: なし
    プロジェクト内: 同パッケージの ea_input_map / kwargs_mapper / math_calculations /
                    run_from_settings / unsupported / window
"""
from __future__ import annotations

from simulator.main.tester_settings.ea_input_map import (
    EA_INPUT_BINDINGS,
    EaInputBinding,
    bind_ea_inputs,
    ea_stem,
    scalar_converter_for,
)
from simulator.main.tester_settings.kwargs_mapper import (
    EngineBinding,
    TesterRunMetadata,
    build_run_metadata,
    to_interactor_kwargs,
    verify_data_consistency,
)
from simulator.main.tester_settings.math_calculations import run_math_calculations
from simulator.main.tester_settings.run_from_settings import run_from_settings
from simulator.main.tester_settings.unsupported import (
    NON_RAISING_RULES,
    RULES,
    RUN_REQUEST_RULES,
    UnsupportedRule,
    apply_unsupported_rules,
    raise_unsupported,
)
from simulator.main.tester_settings.window import (
    DataWindow,
    epoch_seconds,
    resolve_data_window,
    verify_window_applied,
)

__all__ = [
    # 注入束・実行メタ
    "EngineBinding",
    "TesterRunMetadata",
    "build_run_metadata",
    # API-06（変換）
    "to_interactor_kwargs",
    "verify_data_consistency",
    # API-07（期間）
    "DataWindow",
    "resolve_data_window",
    "verify_window_applied",
    "epoch_seconds",
    # 非対象（保証境界）
    "UnsupportedRule",
    "RULES",
    "RUN_REQUEST_RULES",
    "NON_RAISING_RULES",
    "apply_unsupported_rules",
    "raise_unsupported",
    # EA 入力の束縛
    "EaInputBinding",
    "EA_INPUT_BINDINGS",
    "scalar_converter_for",
    "bind_ea_inputs",
    "ea_stem",
    # 実行 facade
    "run_from_settings",
    "run_math_calculations",
]
