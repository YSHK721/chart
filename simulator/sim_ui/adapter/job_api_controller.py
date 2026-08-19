"""A-JobApiController: ジョブ API の HTTP 表現 ⇄ usecase の変換（adapter 層）。

責務（SRP）: **翻訳だけ**。HTTP の語彙（メソッド・パス・状態コード・JSON）と usecase の
語彙（DTO・例外）の間を写す。ジョブの規則は一切持たない（それは usecase と domain）。

例外 → 状態コードの対応（無音の誤動作を作らないための明示）:
    JobNotFoundError        → 404
    SizingUnsupportedError  → 400（E-3・§12.5「明示エラーで拒否」）
    JobTransitionError      → 409（§12.7 終端確定への違反）
    ResultNotAvailableError → 409（§12.7 部分結果の非公開）
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from simulator.sim_ui.domain.simulation_job import JobTransitionError
from simulator.sim_ui.usecase.job_models import (
    JobNotFoundError,
    JobSubmissionInvalidError,
    JobSubmission,
    JobView,
    ResultNotAvailableError,
    SizingUnsupportedError,
)


@dataclass(frozen=True)
class ApiResponse:
    """HTTP 応答（framework が書き出す）。"""

    status: int
    payload: "dict[str, Any]"

    def to_bytes(self) -> bytes:
        return json.dumps(self.payload, ensure_ascii=False).encode("utf-8")


class JobApiController:
    """ジョブ API の入出力変換。"""

    def __init__(self, *, submit, query, cancel, fetch_result) -> None:
        self._submit = submit
        self._query = query
        self._cancel = cancel
        self._fetch_result = fetch_result

    # --- POST /jobs ------------------------------------------------------

    def submit(self, raw_body: bytes) -> ApiResponse:
        try:
            body = json.loads(raw_body or b"{}")
        except ValueError:
            return ApiResponse(400, {"error": "JSON として解釈できない本文です"})
        backtest = body.get("backtest") if isinstance(body, dict) else None
        if not isinstance(backtest, dict) or not backtest:
            return ApiResponse(
                400, {"error": "backtest（実行仕様）を含む JSON 本文が必要です"}
            )
        submission = JobSubmission(
            backtest=backtest,
            sizing=body.get("sizing"),
            # Phase 6 F-8（P6-E3）: 戦略項目ブロックを読取る。不在は None（既定 OFF・byte 等価）。
            strategy=body.get("strategy"),
            # Phase 8 §18（T-4）: Tester Settings ブロック（第 4 ブロック）。不在は None
            # ＝旧 spec と併存し、現行経路は byte 等価のまま。
            settings=body.get("settings"),
        )
        try:
            view = self._submit.execute(submission)
        except (SizingUnsupportedError, JobSubmissionInvalidError) as exc:
            # 受付検証で弾いた投入は 400（本文が悪い）。捕捉し漏らすとハンドラまで
            # 例外が抜けて**接続が切れ**、利用者には「サーバが落ちた」としか見えない。
            return ApiResponse(400, {"error": str(exc)})
        return ApiResponse(202, _view_payload(view))

    # --- GET /jobs/{id} --------------------------------------------------

    def query(self, job_id: str) -> ApiResponse:
        try:
            view = self._query.execute(job_id)
        except JobNotFoundError as exc:
            return ApiResponse(404, {"error": str(exc)})
        return ApiResponse(200, _view_payload(view))

    # --- POST /jobs/{id}/cancel ------------------------------------------

    def cancel(self, job_id: str) -> ApiResponse:
        try:
            view = self._cancel.execute(job_id)
        except JobNotFoundError as exc:
            return ApiResponse(404, {"error": str(exc)})
        except JobTransitionError as exc:
            return ApiResponse(409, {"error": str(exc)})
        return ApiResponse(200, _view_payload(view))

    # --- GET /data/{id}/{filename} ---------------------------------------

    def result_path(self, job_id: str, filename: str) -> "tuple[Any, ApiResponse | None]":
        """公開してよい結果の所在を返す。公開不可なら ``(None, 応答)``。"""
        try:
            return self._fetch_result.execute(job_id, filename), None
        except JobNotFoundError as exc:
            return None, ApiResponse(404, {"error": str(exc)})
        except ResultNotAvailableError as exc:
            return None, ApiResponse(409, {"error": str(exc)})
        except ValueError as exc:  # 台帳が受理しない識別子・ファイル名（CWE-22）
            return None, ApiResponse(404, {"error": str(exc)})


def _view_payload(view: JobView) -> "dict[str, Any]":
    return {
        "job_id": view.job_id,
        "status": view.status,
        "failure_reason": view.failure_reason,
    }
