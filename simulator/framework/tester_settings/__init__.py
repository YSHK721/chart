"""Settings 検証層の公開面（検証済み DTO への入口）。

1. 層名/責務:
    framework 層。`.ini`（MT5 ストラテジーテスターの Settings タブ）を検証して
    内側 DTO（``TesterSettings``）へ変換する経路の**唯一の公開窓口**。外側の層
    （main・CLI・テスト）はここに列挙したシンボルだけを参照し、``validation`` /
    ``loader`` の物理配置と pydantic の存在に依存しない。

2. 含む構造:
    API      : load_tester_settings（API-01）/ dump_tester_settings（API-02）/
               tester_settings_from_mapping（API-03）/ tester_settings_to_mapping（API-04）
    検証入口 : build_settings（字句層を経由せず生値から構築する場合の下位入口）
    定義     : EXPERT_ONLY_KEYS / KEY_RULES / rule_id_for（規則 ID と Expert 専用
               キーの唯一の出所。キー集合・標準キー順は字句層 ``ini_codec`` の
               ``TESTER_KEYS`` / ``STANDARD_KEY_ORDER`` が持つ）
    ログ     : LOGGER（``logging.getLogger("simulator.tester_settings")`` 1 本）

3. 元 MQL 対応:
    `[Tester]` の 18 キーと `[TesterInputs]` の入力行（基本設計 §2.2.3 の実測）。

4. 依存:
    標準: なし（再エクスポートのみ）
    外部: pydantic（``validation`` の内部にのみ存在し、本パッケージの公開面には
          pydantic 型が 1 つも現れない）
    プロジェクト内: simulator.framework.tester_settings.validation / .loader
"""
from __future__ import annotations

from simulator.framework.tester_settings.loader import (
    LOGGER,
    dump_tester_settings,
    load_tester_settings,
    tester_settings_from_mapping,
    tester_settings_to_mapping,
)
from simulator.framework.tester_settings.validation import (
    EXPERT_ONLY_KEYS,
    KEY_RULES,
    MAX_INPUT_NAME_CHARS,
    TesterKeyRule,
    build_settings,
    rule_id_for,
)

__all__ = [
    # API-01〜API-04
    "load_tester_settings",
    "dump_tester_settings",
    "tester_settings_from_mapping",
    "tester_settings_to_mapping",
    # 検証入口
    "build_settings",
    # 検証層が持つ定義（規則 ID・Expert 専用キー）
    "EXPERT_ONLY_KEYS",
    "KEY_RULES",
    "MAX_INPUT_NAME_CHARS",
    "TesterKeyRule",
    "rule_id_for",
    # ログ
    "LOGGER",
]
