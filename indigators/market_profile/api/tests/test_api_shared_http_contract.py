"""api_shared.http_contract の移設回帰（ISSUE-094 🔵-11）。

HTTP 契約（ERROR_STATUS / nested_error）を中立共有パッケージ api_shared へ移設し、
marketdata.api_contract を後方互換の再エクスポートへ降格した。本テストは
(1) 実体が api_shared.http_contract にあること、(2) marketdata.api_contract 経由の
既存 import が同一オブジェクトを返すこと（破壊的変更なし）、(3) nested_error の
(status, body) が移設前と byte 等価であることを固定する。
"""
from __future__ import annotations

import api_shared.http_contract as http_contract
import marketdata.api_contract as api_contract


def test_error_status_table_is_the_expected_mapping() -> None:
    assert http_contract.ERROR_STATUS == {
        "validation": 400,
        "missing_column": 400,
        "missing_time": 400,
        "empty_series": 422,
        "backend_unavailable": 500,
        "internal": 500,
    }


def test_marketdata_reexport_is_same_object_as_api_shared() -> None:
    # 後方互換: marketdata.api_contract は api_shared.http_contract の実体を再エクスポートする。
    assert api_contract.ERROR_STATUS is http_contract.ERROR_STATUS
    assert api_contract.nested_error is http_contract.nested_error


def test_nested_error_validation_shape_unchanged() -> None:
    status, body = http_contract.nested_error("validation", "bad input")
    assert status == 400
    assert body == {
        "ok": False,
        "generation": 0,
        "error": {"type": "validation", "message": "bad input", "violations": []},
    }


def test_nested_error_unknown_type_falls_back_to_500() -> None:
    status, body = http_contract.nested_error("nope", "x", generation=7)
    assert status == 500
    assert body["generation"] == 7
    assert body["error"]["type"] == "nope"


def test_marketdata_reexport_produces_identical_output() -> None:
    # 既存呼び出し経路（marketdata.api_contract.nested_error）が移設後も同一出力を返す。
    assert api_contract.nested_error("internal", "boom", generation=3) == (
        http_contract.nested_error("internal", "boom", generation=3)
    )
