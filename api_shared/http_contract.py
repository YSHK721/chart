"""HTTP 共有規約表（ISSUE-087 🔴-1 / ISSUE-091 A2 / ISSUE-094 🔵-11）。

error.type → HTTP ステータスの単一定義（§6.3.4 / §7.4）と nested エラーボディの単一整形。
indicator_ui api・market_profile api・replay backend の 3 殻が本表・本整形を同格に参照する
（殻ごとの独自整形＝契約分岐を排する。HTTP 機構は含まない純粋な対応表・純関数）。

ISSUE-094 🔵-11: HTTP 契約の所有者は配信殻であり marketdata のどのアクターでもないため、
中立共有パッケージ ``api_shared`` へ実体を移設した。実体は本モジュールの 1 箇所のみである。
旧 marketdata/api_contract.py は後方互換の再エクスポートへ降格したのち、参照ゼロ化を経て
ISSUE-479 F-8 で削除済み（不在は marketdata/tests/test_no_legacy_api_contract_reference.py
が固定する）。
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
    error_type: str, message: str, *, generation: int = 0, violations: "list | None" = None
) -> "tuple[int, dict[str, Any]]":
    """§6.3.4 nested エラーの (HTTP ステータス, ボディ) を返す（単一定義・純関数）。

    ボディ形は ``{ok: false, generation, error: {type, message, violations}}``。
    未知の error_type は 500（internal 相当）へフォールバックする。
    """
    status = ERROR_STATUS.get(error_type, 500)
    return status, {
        "ok": False,
        "generation": generation,
        # violations（ISSUE-283）: 指標が申告した機械可読な診断（例: 履歴不足の requiredBars）。
        #   既定は空＝従来の応答形と同一。文言の解析をクライアントに強いないための構造化面。
        "error": {"type": error_type, "message": message, "violations": list(violations or [])},
    }
