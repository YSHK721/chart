"""GET /catalog の純ロジック controller（HTTP 殻非依存・ISSUE-092 ③）。

param 既定値の単一情報源（``adapter.compute.catalog_schema``）を JSON 応答形へ載せる。
``handle_candles`` / ``handle_compute`` と同型の ``(status, body)`` 純関数で、殻
（``framework/server.py``）は「handle → JSON 送出」のみへ縮小する。エラーは正典契約
（``api_shared.http_contract.nested_error``）に従う（殻ごとの独自整形を排する）。
"""

from __future__ import annotations

from typing import Any

from adapter.compute import catalog_defaults


def handle_catalog() -> "tuple[int, dict[str, Any]]":
    """指標 param 既定値スキーマを配信する（§単一情報源・ISSUE-092 ③）。

    応答は ``{ok: true, catalog: {compute_id: {param_name: default}}}``。想定外例外は正典
    nested error（internal・500）で返す。
    """
    try:
        return 200, {"ok": True, "catalog": catalog_defaults()}
    except Exception as exc:  # noqa: BLE001（controller の最後の砦・nested で返す）
        from api_shared.http_contract import nested_error

        return nested_error("internal", f"catalog 取得に失敗しました: {exc}")
