"""Settings 層の内側公開面（DTO・列挙の再エクスポート）。

1. 層名/責務:
    usecase 層。`.ini`（MT5 ストラテジーテスターの Settings タブ）を表す DTO と
    列挙の**唯一の公開窓口**。外側の層（adapter / framework / main）は本パッケージ
    のシンボルだけを参照し、``models`` / ``enums`` の物理配置に依存しない。
    I/O・検証・エンジン投入は一切行わない（値の定義のみ）。

2. 含む構造:
    生表現   : IniLineKind / IniLine / IniDocument
    入れ子   : DateRange / TesterInput
    設定本体 : SettingsPayload / TesterSettings / EffectiveSettings / INERT_FIELDS
    列挙     : Timeframe / TickModel / DateRangeKind / DatesPreset / ForwardMode /
               OptimizationMode / OptimizationCriterion / SubjectKind / InputForm
    定数     : ExecutionDelay / TIMEFRAME_INI_LABELS / INI_LABEL_TO_TIMEFRAME /
               TICK_MODEL_ENGINE_IDS
    実証状態 : PROVEN_EXECUTION_DELAYS / PROVISIONAL_EXECUTION_DELAYS /
               approximation_reason_for（`ExecutionDelay` と同じ宣言サイト）

3. 元 MQL 対応:
    `.ini` の 2 セクション `[Tester]` / `[TesterInputs]` と MQL の
    ``ENUM_TIMEFRAMES`` ほかの生値に対応する（各シンボルの docstring を参照）。

4. 依存:
    標準: なし（再エクスポートのみ）
    外部: なし。**pydantic / pandas / numpy を import しない**（内部設計 §3.3 I-1）
    プロジェクト内: simulator.usecase.tester_settings.enums / .models
"""
from __future__ import annotations

from simulator.usecase.tester_settings.enums import (
    INI_LABEL_TO_TIMEFRAME,
    PROVEN_EXECUTION_DELAYS,
    PROVISIONAL_EXECUTION_DELAYS,
    TICK_MODEL_ENGINE_IDS,
    TIMEFRAME_INI_LABELS,
    DateRangeKind,
    DatesPreset,
    ExecutionDelay,
    ForwardMode,
    InputForm,
    OptimizationCriterion,
    OptimizationMode,
    SubjectKind,
    TickModel,
    Timeframe,
    approximation_reason_for,
)
from simulator.usecase.tester_settings.models import (
    INERT_FIELDS,
    DateRange,
    EffectiveSettings,
    IniDocument,
    IniLine,
    IniLineKind,
    SettingsPayload,
    TesterInput,
    TesterSettings,
)

__all__ = [
    # 列挙・定数
    "DateRangeKind",
    "DatesPreset",
    "ExecutionDelay",
    "ForwardMode",
    "INI_LABEL_TO_TIMEFRAME",
    "InputForm",
    "OptimizationCriterion",
    "OptimizationMode",
    "PROVEN_EXECUTION_DELAYS",
    "PROVISIONAL_EXECUTION_DELAYS",
    "SubjectKind",
    "TICK_MODEL_ENGINE_IDS",
    "TIMEFRAME_INI_LABELS",
    "TickModel",
    "Timeframe",
    "approximation_reason_for",
    # 生表現
    "IniDocument",
    "IniLine",
    "IniLineKind",
    # 設定 DTO
    "DateRange",
    "EffectiveSettings",
    "INERT_FIELDS",
    "SettingsPayload",
    "TesterInput",
    "TesterSettings",
]
