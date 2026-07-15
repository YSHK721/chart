"""API 共有規約表（ISSUE-087 🔴-1）。

error.type → HTTP ステータスの単一定義（§6.3.4 / §7.4）。indicator_ui api と
market_profile api の両モジュールが本表を同格に参照する（MP→indicator_ui の
裸パッケージ依存を排するため、両者から到達可能な最下層 marketdata に置く。
HTTP 機構は含まない純粋な対応表＝データ層への機構持ち込みではない）。
"""
from __future__ import annotations

ERROR_STATUS: "dict[str, int]" = {
    "validation": 400,
    "missing_column": 400,
    "missing_time": 400,
    "empty_series": 422,
    "backend_unavailable": 500,
    "internal": 500,
}
