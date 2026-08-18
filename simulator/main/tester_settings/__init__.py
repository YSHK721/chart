"""Settings 変換・実行層の公開面（API-06 / API-07 と実行 facade）。

1. 層名/責務:
    main 層（Composition Root）。`.ini` から読み込んだ設定（`TesterSettings`）を
    現行エンジンの投入契約へ写し、実行する経路の**唯一の公開窓口**。呼出側は
    ここに列挙したシンボルだけを参照し、下位モジュールの物理配置に依存しない。

    A-1（ISSUE-397・依頼者承認済み）以前は既存ファイル（`main/__init__.py` /
    `tick_model_registry.py`）を 1 行も変えない**追加のみ**の構成だったが、その制約は
    `Math calculations` を UI から投入できない（L-1）・エンジンの `tick_model` が
    Settings の語彙と一致しない（L-5）という限界を生んだ。承認により制約を解き、
    レジストリへ 1 エントリ（既定値付きフィールドの追加＝既存 4 エントリは無改変）を
    加えて実行経路を 1 本に統合した。

    L-1 の解消は実測済み（`POST /sim/jobs` → 202 → `status="completed"` ＋ report.json
    生成）。ただし成立にはレジストリ追加だけでなく、EA ファクトリ**選択規則の判定点が
    1 つであること**が要る（`main._select_ea_factory`。詳細と実測は
    `math_calculations` の module docstring / `tests/integration/
    test_ea_factory_selection_rule.py`）。

2. 含む構造:
    注入束・メタ : EngineBinding / TesterRunMetadata / build_run_metadata
    変換        : to_interactor_kwargs（API-06）/ effective_to_interactor_kwargs
    期間        : DataWindow / resolve_data_window（API-07）/ verify_window_applied
    非対象      : UnsupportedRule / RULES / RUN_REQUEST_RULES / apply_unsupported_rules
    EA 入力     : EaInputBinding / EA_INPUT_BINDINGS / scalar_converter_for /
                  bind_ea_inputs / ea_stem
    実行        : run_from_settings（実行 A・終了コードへ翻訳）/
                  run_effective_settings（唯一の実行段・例外を送出）/
                  run_math_calculations（実行 B・実行段への薄い入口）

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
    effective_to_interactor_kwargs,
    to_interactor_kwargs,
    verify_data_consistency,
)
from simulator.main.tester_settings.math_calculations import run_math_calculations
from simulator.main.tester_settings.run_from_settings import (
    run_effective_settings,
    run_from_settings,
)
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
    "effective_to_interactor_kwargs",
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
    "run_effective_settings",
    "run_math_calculations",
]
