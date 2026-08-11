"""ジョブ実行系の境界（Port・usecase 層・§6.1「バックエンド境界」）。

ISP: クライアントごとに分ける。台帳（永続化）・起動器（子プロセス）・指標系列カタログ
（E-3 判定）は別々の変更要因を持つため、1 つの巨大 Port にまとめない。

DIP: Interactor はこれらの抽象にのみ依存し、FS / subprocess / pandas を知らない。

責務分割（SRP・本 Phase の設計判断）:
    * :class:`JobLedgerPort`   — ジョブ状態と投入仕様の**永続化**だけ。プロセスを知らない。
    * :class:`JobLauncherPort` — 子プロセスの**生死**だけ。状態の意味を知らない。
    * 両者を突き合わせて状態遷移を確定するのは usecase（`query_job`）の仕事。
      こうすると「子プロセスが落ちたのに実行中のまま」の照合点が 1 箇所に閉じる。
"""
from __future__ import annotations

import abc
from typing import Any

from simulator.sim_ui.domain.simulation_job import JobStatus, SimulationJob
from simulator.sim_ui.usecase.job_models import JobSubmission


class JobLedgerPort(abc.ABC):
    """ジョブ台帳（登録・状態更新・照会）の抽象。"""

    @abc.abstractmethod
    def create(self, submission: JobSubmission) -> SimulationJob:
        """識別子を採番し、投入仕様を保存して受付状態のジョブを返す。"""
        raise NotImplementedError

    @abc.abstractmethod
    def load(self, job_id: str) -> "SimulationJob | None":
        """ジョブを読む。未知の識別子は ``None``。"""
        raise NotImplementedError

    @abc.abstractmethod
    def update(self, job: SimulationJob, *, expect: "JobStatus") -> None:
        """compare-and-set でジョブ状態を保存する。

        ``expect``: 呼び出し側が読んだ時点の状態。**永続状態が ``expect`` と異なれば
        書かずに** :class:`JobTransitionError` を送出する。

        なぜ必要か（実測された壊れ方）: 無条件書き込みでは、cancel が CANCELLED を
        書いた直後に query が（古い読みに基づく）COMPLETED を上書きし、**取消した
        ジョブの結果が公開される**（§12.7「取消＝終端確定・部分結果非公開」の破れ）。
        読み→判断→書きの間に他者が割り込めないことを、書き込み時点で検査する。
        """
        raise NotImplementedError

    @abc.abstractmethod
    def read_failure_report(self, job_id: str) -> "str | None":
        """子プロセスが残した失敗理由を読む。無ければ ``None``。

        子プロセスは**状態を書かない**（`run_job.py:14-17`: SIGKILL で何も書けずに
        死んでも「実行中のまま固まる」経路を作らないため）。一方で終了コードだけでは
        「なぜ落ちたか」が運用者に届かない。理由**だけ**を別ファイルに残し、状態の確定は
        従来どおり sim core 側が行う、という分担にする。
        """
        raise NotImplementedError

    @abc.abstractmethod
    def result_path(self, job_id: str, filename: str) -> Any:
        """完了ジョブの結果ファイルの所在を返す（存在保証はしない）。"""
        raise NotImplementedError


class JobLauncherPort(abc.ABC):
    """計算の子プロセスの起動・停止・生存確認の抽象。"""

    @abc.abstractmethod
    def launch(self, job_id: str) -> None:
        """子プロセスを**即時**起動する（§12.7 並列実行・待ち行列を作らない）。"""
        raise NotImplementedError

    @abc.abstractmethod
    def terminate(self, job_id: str) -> None:
        """子プロセスへ SIGTERM を送る（§12.7 取消）。"""
        raise NotImplementedError

    @abc.abstractmethod
    def poll(self, job_id: str) -> "int | None":
        """終了していれば終了コード、実行中なら ``None`` を返す。"""
        raise NotImplementedError


class IndicatorSeriesCatalogPort(abc.ABC):
    """ea_name → 指標レジストリに登録される系列名の集合（E-3 判定用・§12.5）。"""

    @abc.abstractmethod
    def series_for(self, ea_name: str) -> "frozenset[str]":
        """登録系列名の集合を返す。判定できないときは空集合（＝fail-safe に不可）。"""
        raise NotImplementedError


class StopLossParamCatalogPort(abc.ABC):
    """ea_name → その EA の SL を決める**設定パラメータ名**の集合（§12.8 受付時検証）。

    裁定（2026-08-11）: ケリー基準は損切り幅が無ければリスク額を定義できず f→ロット変換が
    成立しない。したがって「戦略設定が SL を保証すること」は**実行前に決着すべき前提条件**
    であり、E-3 と同じ受付検証で扱う。

    ISP: E-3 の :class:`IndicatorSeriesCatalogPort` とは別の変更要因（指標系列 vs 設定
    パラメータ）なので分ける。

返り値の 3 値（🟡-3）:
        ``frozenset({...})`` — 探索できて、SL を決める設定名が判明した。
        ``frozenset()``      — 探索できたが、SL 設定パラメータを持たないと判明した。
        ``None``             — **探索できなかった**（戦略クラスを特定できない等）。

    後 2 者はいずれも「設定の正値検査では SL 保証を証明できない」ため受付では決着させず、
    実行中の内部不変条件違反（fail-stop）で受け止める。区別するのは、
    「調べた上で無い」と「調べられなかった」を混同すると、探索が壊れたときに
    「SL 検証が黙って無効化された」ことに気付けなくなるため。
    """

    @abc.abstractmethod
    def stop_loss_params(self, ea_name: str) -> "frozenset[str] | None":
        """SL を決める設定パラメータ名の集合。探索不能なら ``None``。"""
        raise NotImplementedError
