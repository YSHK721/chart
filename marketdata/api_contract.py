"""API 共有規約表（ISSUE-087 🔴-1 / ISSUE-091 A2）。

error.type → HTTP ステータスの単一定義（§6.3.4 / §7.4）と nested エラーボディの単一整形。
indicator_ui api・market_profile api・replay backend の 3 殻が本表・本整形を同格に参照する
（殻ごとの独自整形＝契約分岐を排する。HTTP 機構は含まない純粋な対応表・純関数＝
データ層への機構持ち込みではない）。
"""
from __future__ import annotations

from typing import Any

ERROR_STATUS: "dict[str, int]" = {
    "validation": 400,
    "missing_column": 400,
    "missing_time": 400,
    "empty_series": 422,
    "backend_unavailable": 500,
    "internal": 500,
}


def nested_error(
    error_type: str, message: str, *, generation: int = 0
) -> "tuple[int, dict[str, Any]]":
    """§6.3.4 nested エラーの (HTTP ステータス, ボディ) を返す（単一定義・純関数）。

    ボディ形は ``{ok: false, generation, error: {type, message, violations}}``。
    未知の error_type は 500（internal 相当）へフォールバックする。
    """
    status = ERROR_STATUS.get(error_type, 500)
    return status, {
        "ok": False,
        "generation": generation,
        "error": {"type": error_type, "message": message, "violations": []},
    }
