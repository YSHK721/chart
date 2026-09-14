"""例外の分類は「例外が自分で宣言した種別」に従う（ISSUE-284）。

実際に起きたこと（2026-08-08 実測）:
    cvfe の `E01_INSUFFICIENT_BARS`（履歴不足＝入力条件の未達）が、`/live/compute` では
    **400 validation**、`/replay/compute` では **500 internal** になっていた。原因は
    `_error_response` が Python の例外型（ValueError か否か）だけで分類していたこと。
    指標計算は `ComputeError`（`error_type` / `message` を持つ）で分類を**申告している**のに、
    その申告が無視され、汎用の `except Exception` 側の文言ごと internal へ落ちていた。

500 は「サーバ内部の異常」を意味する。入力条件の未達を 500 で返すと、監視・切り分けが壊れる。
"""
from __future__ import annotations

from simulator.replay_ui.framework.serve_replay import _error_response


class _DeclaredError(Exception):
    """ComputeErrorPort を満たす例外（error_type / message を宣言する）。"""

    def __init__(self, error_type: str, message: str) -> None:
        super().__init__(f"ComputeError: {error_type}: {message}")
        self.error_type = error_type
        self.message = message


def test_declared_validation_becomes_400_not_500():
    status, body = _error_response(
        _DeclaredError("validation", "E01_INSUFFICIENT_BARS: バー数 1234 では σ̂ を 1 本も出力できない"),
        generation=7,
        message="ComputeError: 汎用文言で上書きされてはいけない",
    )

    assert status == 400
    assert body["error"]["type"] == "validation"
    assert body["error"]["message"].startswith("E01_INSUFFICIENT_BARS"), "宣言側のメッセージを使う"
    assert body["generation"] == 7


def test_declared_empty_series_keeps_its_own_status():
    status, body = _error_response(_DeclaredError("empty_series", "必須バケットが空です"))

    assert status == 422, "分類ごとの status は api_shared.http_contract の単一表に従う"
    assert body["error"]["type"] == "empty_series"


def test_undeclared_value_error_is_still_validation():
    status, body = _error_response(ValueError("timeframe が不正です"))

    assert status == 400
    assert body["error"]["type"] == "validation"


def test_undeclared_other_exception_is_internal():
    status, body = _error_response(RuntimeError("想定外"), message="RuntimeError: 想定外")

    assert status == 500
    assert body["error"]["type"] == "internal"
    assert body["error"]["message"] == "RuntimeError: 想定外"
