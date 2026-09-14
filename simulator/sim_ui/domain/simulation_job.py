"""E-SimulationJob: バックテストジョブの状態と遷移規則（domain・CLEAN_ARCH §4）。

責務（SRP）: 「ジョブがどの状態を取り、どの遷移が許されるか」だけを持つ。台帳の永続化・
子プロセスの起動・HTTP の形は一切知らない（それらは usecase / adapter / framework）。

不変条件（基本設計書 §4.2 F-3・§12.7 依頼者裁定）:
    1. 状態は 受付 / 実行中 / 完了 / 失敗 / 取消 の 5 値。
    2. **終端状態（完了・失敗・取消）からの遷移は不変条件違反**。取消は終端確定であり、
       取消後に「完了」へ戻って部分結果が公開される経路を構造的に作らない（§12.7 fail-stop）。
    3. 取消は 受付・実行中 の双方から可能（子プロセス起動前でも取り消せる）。
    4. 失敗は理由を伴う。理由なしの失敗・失敗以外への理由付与はいずれも不変条件違反
       （「失敗したが理由が無い」「完了なのに理由が残る」状態を型で作れなくする）。
    5. 不変オブジェクト（frozen dataclass）。遷移は新インスタンスを返す。並列実行
       （§12.7）で複数ジョブを同時に扱っても、共有インスタンスの書き換え競合が起きない。

domain は外部依存ゼロ（PROCESS §4）。enum / dataclasses は標準ライブラリ。
"""
from __future__ import annotations

from dataclasses import dataclass, replace
from enum import Enum


class JobTransitionError(Exception):
    """ジョブ状態遷移の不変条件違反。

    無音の誤動作を作らないため、メッセージには遷移元と遷移先を必ず含める
    （「なぜ拒まれたか」が状態だけで読めるようにする）。
    """


class JobStatus(Enum):
    """ジョブ状態の 5 値（§6.1「受付 / 実行中 / 完了 / 失敗 / 取消」）。"""

    RECEIVED = "received"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"

    @property
    def is_terminal(self) -> bool:
        """終端状態（これ以上遷移しない）か。"""
        return self in _TERMINAL


# 終端状態。ここからの遷移は全て不変条件違反（§12.7）。
_TERMINAL: "frozenset[JobStatus]" = frozenset(
    {JobStatus.COMPLETED, JobStatus.FAILED, JobStatus.CANCELLED}
)

# 遷移表（許可された遷移のみを列挙する＝既定は拒否）。
# 表に無い遷移は全て JobTransitionError。表を単一ソースとし、分岐を散らさない。
_ALLOWED: "dict[JobStatus, frozenset[JobStatus]]" = {
    JobStatus.RECEIVED: frozenset(
        {JobStatus.RUNNING, JobStatus.FAILED, JobStatus.CANCELLED}
    ),
    JobStatus.RUNNING: frozenset(
        {JobStatus.COMPLETED, JobStatus.FAILED, JobStatus.CANCELLED}
    ),
    JobStatus.COMPLETED: frozenset(),
    JobStatus.FAILED: frozenset(),
    JobStatus.CANCELLED: frozenset(),
}


@dataclass(frozen=True)
class SimulationJob:
    """ジョブの状態を持つ不変オブジェクト。

    ``failure_reason`` は FAILED のときのみ非 None（不変条件 4）。
    """

    job_id: str
    status: JobStatus
    failure_reason: "str | None" = None

    @staticmethod
    def received(job_id: str) -> "SimulationJob":
        """新規ジョブ（受付状態）を作る。"""
        return SimulationJob(job_id=job_id, status=JobStatus.RECEIVED)

    def to(
        self, target: JobStatus, *, failure_reason: "str | None" = None
    ) -> "SimulationJob":
        """``target`` へ遷移した新インスタンスを返す。違反時 JobTransitionError。"""
        if target not in _ALLOWED[self.status]:
            raise JobTransitionError(
                f"許されない状態遷移です: {self.status.value} → {target.value}"
            )
        if target is JobStatus.FAILED and failure_reason is None:
            raise JobTransitionError(
                f"失敗遷移には理由が必要です: {self.status.value} → {target.value}"
            )
        if target is not JobStatus.FAILED and failure_reason is not None:
            raise JobTransitionError(
                "失敗理由は失敗遷移にのみ指定できます: "
                f"{self.status.value} → {target.value}"
            )
        return replace(self, status=target, failure_reason=failure_reason)
