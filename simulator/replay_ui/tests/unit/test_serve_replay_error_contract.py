"""ISSUE-097 🟡-4 — serve_replay の中央例外翻訳ヘルパ ``_error_response`` の単体テスト。

各ハンドラに個別コピーされていた ``except ValueError→validation / except Exception→internal``
分類を 1 関数へ集約した中央翻訳器の正典契約を固定する:
    ValueError            → validation / 400
    MemoryError・その他    → internal   / 500
ボディは api_shared.http_contract.nested_error の単一整形（{ok, generation, error{type,message,violations}}）。
"""
from __future__ import annotations

from simulator.replay_ui.framework.serve_replay import _error_response


def _assert_nested(body, error_type, generation=0):
    # nested 形（api_shared.http_contract.nested_error の単一整形）の一致検証。
    assert body["ok"] is False
    assert body["generation"] == generation
    assert body["error"]["type"] == error_type
    assert body["error"]["violations"] == []
    assert "message" in body["error"]


def test_value_error_maps_to_validation_400():
    # Arrange / Act
    status, body = _error_response(ValueError("bad input"))
    # Assert
    assert status == 400
    _assert_nested(body, "validation")
    assert body["error"]["message"] == "bad input"


def test_memory_error_maps_to_internal_500():
    status, body = _error_response(MemoryError())
    assert status == 500
    _assert_nested(body, "internal")


def test_generic_exception_maps_to_internal_500():
    status, body = _error_response(RuntimeError("boom"))
    assert status == 500
    _assert_nested(body, "internal")


def test_generation_is_threaded_into_nested_body():
    status, body = _error_response(ValueError("x"), generation=7)
    assert status == 400
    _assert_nested(body, "validation", generation=7)


def test_message_override_preserves_classification():
    # message override（compute の MemoryError→"memory limit" 等）は分類に影響しない。
    status, body = _error_response(MemoryError(), message="memory limit")
    assert status == 500
    _assert_nested(body, "internal")
    assert body["error"]["message"] == "memory limit"


def test_message_truncated_to_200_chars():
    status, body = _error_response(ValueError("x" * 500))
    assert status == 400
    assert body["error"]["message"] == "x" * 200
