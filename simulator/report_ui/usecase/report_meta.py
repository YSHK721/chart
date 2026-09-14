"""特定実験の所与（meta オブジェクト・ISSUE-094 🟡-5）。

BuildReportPayload に直書きされていた「1 実験固有の記述」（EA 名 = StopEntryProbe_EA・
試験条件パラメータ文字列・IS/OOS 分割日・分割方式ノート・銘柄/時間足の既定）を、UC 引数
として外出しするためのデータオブジェクト。これにより BuildReportPayload 本体は EA 非依存の
純写像となり、実験固有値の変更は本オブジェクトの生成箇所（Composition Root）へ局所化される。

既定値は現行の StopEntryProbe 実験値（詳細設計 §4.5・§4.8）。既存呼出元が値を渡さない場合も
既定で現行 report.json を byte 不変に保つ。Composition Root（report_ui/tools）は現行値を
明示生成して渡す（実験所与の所有をルートへ移す）。
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ReportMeta:
    """report 出力に載る特定実験の所与。

    - ``expert``    : report ラベル "Expert" の値（EA 名）
    - ``params``    : 全体 meta "params"（試験条件パラメータの記述文字列）
    - ``split``     : 全体 meta "split"（IS/OOS 分割日）
    - ``note``      : 全体 meta "note"（分割方式の注記）
    - ``symbol``    : meta 既定の銘柄（呼出元が meta dict に symbol を含めない場合の既定）
    - ``timeframe`` : meta 既定の時間足（同上）
    """

    expert: str = "StopEntryProbe_EA"
    params: str = "ProbeDir=2(両建て) / offset100 / Lot0.1 / SL200 / TP500"
    split: str = "2026-04-15"
    note: str = "IS/OOS 単純分割（同一パラメータを両区間で評価・最適化なし）"
    symbol: str = "JP225"
    timeframe: str = "M1"
