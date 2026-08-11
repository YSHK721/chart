"""ジョブ実行系のプレーン DTO と usecase 例外（CLEAN_ARCH §5）。

境界を跨ぐデータは全て**プレーン**（dataclass / Mapping）にする。pydantic 型・HTTP の
Request/Response・pathlib の Path は usecase へ入れない（framework/adapter に留める）。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from simulator.sim_ui.domain.simulation_job import SimulationJob


@dataclass(frozen=True)
class JobSubmission:
    """ジョブ投入要求（§4.2 F-3 の入力）。

    ``backtest``: `simulator.main.run_backtest` へ渡す meta（ea_name / symbol / period /
      data_path / config_overrides ...）。sim コアは中身を解釈せず素通しする（子プロセスが
      解釈する）。ただし E-3 判定に必要な 2 つだけは読む（ea_name・entry_price_basis）。
    ``sizing``: サイジング設定。``None`` または ``enabled`` が偽なら **OFF**（既定・
      §12.1 で「既定 OFF・OFF は既存挙動と byte 等価」と裁定済み）。
    """

    backtest: Mapping[str, Any]
    sizing: "Mapping[str, Any] | None" = None

    @property
    def ea_name(self) -> str:
        return str(self.backtest.get("ea_name", ""))

    @property
    def entry_price_basis(self) -> str:
        """約定価格基準。既定は config_loader と同じ "close"。"""
        overrides = self.backtest.get("config_overrides") or {}
        return str(overrides.get("entry_price_basis", "close"))

    @property
    def sizing_enabled(self) -> bool:
        return bool(self.sizing) and bool(self.sizing.get("enabled", False))


@dataclass(frozen=True)
class JobView:
    """ジョブ状態の照会結果（§6.1 `GET /jobs/{id}` の本体）。"""

    job_id: str
    status: str
    failure_reason: "str | None" = None

    @classmethod
    def of(cls, job: SimulationJob) -> "JobView":
        """domain の :class:`SimulationJob` を照会結果へ写す。

        全 Interactor（投入・照会・取消）がこの 1 箇所を通る。写し方を各 Interactor に
        持たせると、状態の表現（`status` の値・`failure_reason` の扱い）が片方だけ
        変わったときに応答が食い違う。
        """
        return cls(
            job_id=job.job_id,
            status=job.status.value,
            failure_reason=job.failure_reason,
        )


class JobNotFoundError(Exception):
    """未知のジョブ識別子（adapter が 404 へ翻訳する）。"""


class SizingUnsupportedError(Exception):
    """E-3（§12.5）: 建値推定に使える価格系列を持たない戦略への sizing ON。

    受付時に明示エラーで拒否する（adapter が 400 へ翻訳する）。無音で OFF へ倒したり
    黙って別系列で代用したりしない（「エラーにならずに誤った結果を返す」を作らない）。
    """


class ResultNotAvailableError(Exception):
    """完了していないジョブの結果要求（adapter が 409 へ翻訳する）。

    §12.7 fail-stop: 取消・失敗・実行中のジョブの**部分結果は公開しない**。公開の可否は
    この 1 箇所（状態の検査）だけで決める。ファイルを消して回る方式は採らない
    （消し漏れ・競合で「消したつもりの部分結果が見える」経路が残るため）。
    """


class JobSubmissionInvalidError(Exception):
    """投入内容そのものが受理できない（キーの未知・必須欠落など・🟡-A / 🔵-C）。

    `SizingUnsupportedError` と分けるのは、こちらが**サイジングと無関係**の検証だから。
    同じ型で投げると「サイジングの問題だ」と誤読させ、切り分けを遅らせる。
    """
