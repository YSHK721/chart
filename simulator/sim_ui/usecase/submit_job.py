"""U-SubmitJob: ジョブ投入（F-3 / FR-10・§12.7 並列実行）。

規則:
    1. sizing ON のときだけ**受付時検証**を行う。判定前に台帳へ書かない・子プロセスを
       起こさない（拒否したジョブの残骸を作らない）。検証は 2 つ:
         a. E-3（§12.5）: 建値推定に使える価格系列を指標レジストリが持たない戦略を拒否。
         b. SL 保証（§12.8・裁定 2026-08-11）: ケリー基準は損切り幅が無ければリスク額を
            定義できず f→ロット変換が成立しない。よって「戦略設定が SL を保証すること」を
            **投入時の必須項目**として検証し、満たさなければ拒否する。
       いずれも判定は**ハードコードの戦略リストではなく** EA 別のカタログ Port から導出する
       （§12.1「戦略リストのハードコード禁止」）。
    2. 台帳へ登録（受付）→ 子プロセスを**即時起動** → 実行中へ遷移する。
       §12.7 の裁定により、実行中のジョブがあっても待たせない（同時 1 本にしない）。
    3. 起動に失敗したら失敗状態（理由つき）にして返す。例外を呼び出し側へ投げない
       ——投入 API は「失敗した」という**状態**を返すのが契約だから（§4.2 F-3 後条件）。

DIP: Port（台帳・起動器・系列カタログ）と、必要系列を決める関数のみに依存する。
    ``required_series`` は Group B（`simulator.usecase.sizing_ports.required_price_series`）
    を合成根が注入する。usecase から sizing 実装へ直接依存しない。
"""
from __future__ import annotations

from typing import Callable

from simulator.sim_ui.domain.simulation_job import JobStatus
from simulator.sim_ui.usecase.job_models import (
    JobSubmission,
    JobSubmissionInvalidError,
    JobView,
    SizingUnsupportedError,
)
from simulator.sim_ui.usecase.job_ports import (
    IndicatorSeriesCatalogPort,
    JobLauncherPort,
    JobLedgerPort,
    StopLossParamCatalogPort,
)


class SubmitJobInteractor:
    """ジョブを受け付け、子プロセスを起動して状態を確定する。"""

    def __init__(
        self,
        *,
        ledger: JobLedgerPort,
        launcher: JobLauncherPort,
        series_catalog: IndicatorSeriesCatalogPort,
        required_series: "Callable[[str], str]",
        stop_loss_catalog: StopLossParamCatalogPort,
        allowed_backtest_keys: "Callable[[], frozenset[str]]",
        required_backtest_keys: "Callable[[], frozenset[str]]",
    ) -> None:
        self._ledger = ledger
        self._launcher = launcher
        self._series_catalog = series_catalog
        self._required_series = required_series
        self._stop_loss_catalog = stop_loss_catalog
        self._allowed_backtest_keys = allowed_backtest_keys
        self._required_backtest_keys = required_backtest_keys

    def execute(self, submission: JobSubmission) -> JobView:
        """投入して現在状態を返す。E-3 違反は :class:`SizingUnsupportedError`。"""
        # sizing の有無に関係なく、子へ素通しする meta のキーを先に検証する
        # （未知キーは子プロセスで TypeError になり「投入は通ったが実行だけ落ちる」
        #  という遅い失敗になる。受付で弾いて即座に理由を返す）。
        self._reject_invalid_backtest_keys(submission)
        if submission.sizing_enabled:
            # 順序: 先に E-3（建値推定の可否）→ 次に SL 保証。前者が満たせない戦略は
            # そもそもサイジングの対象外なので、より根本的な理由を先に返す。
            self._reject_if_price_series_missing(submission)
            self._reject_if_stop_loss_not_guaranteed(submission)

        job = self._ledger.create(submission)
        try:
            self._launcher.launch(job.job_id)
        except Exception as exc:  # 起動の失敗（実行ファイル不在・資源不足等）
            failed = job.to(JobStatus.FAILED, failure_reason=f"ジョブの起動に失敗しました: {exc}")
            self._ledger.update(failed, expect=JobStatus.RECEIVED)
            return JobView.of(failed)

        running = job.to(JobStatus.RUNNING)
        self._ledger.update(running, expect=JobStatus.RECEIVED)
        return JobView.of(running)

    def _reject_if_price_series_missing(self, submission: JobSubmission) -> None:
        """E-3（§12.5）: 必要な価格系列が無い戦略の sizing ON を明示エラーで拒む。"""
        needed = self._required_series(submission.entry_price_basis)
        available = self._series_catalog.series_for(submission.ea_name)
        if needed not in available:
            raise SizingUnsupportedError(
                f"戦略 {submission.ea_name} の指標レジストリは成行の建値推定に必要な "
                f"価格系列 {needed!r} を持たないため、サイジングを有効にできません"
                f"（約定価格基準={submission.entry_price_basis}・"
                f"登録系列={sorted(available)}）"
            )

    def _reject_invalid_backtest_keys(self, submission: JobSubmission) -> None:
        """`backtest` のキーを検証する（未知キー＝🔴-5b／必須欠落＝🟡-A）。

        許可集合・必須集合はいずれも合成根が `inspect.signature(build_interactor)` から
        導出して注入する（手書き表を持たない＝引数が増えても取り残されない・二重化しない）。

        例外型は `JobSubmissionInvalidError`。sizing と無関係の検証なので
        `SizingUnsupportedError` を流用しない（🔵-C）。
        """
        given = set(submission.backtest or {})
        allowed = self._allowed_backtest_keys()
        unknown = sorted(given - allowed)
        if unknown:
            raise JobSubmissionInvalidError(
                f"バックテスト設定に未知のキーがあります: {unknown}"
                f"（受理できるキー: {sorted(allowed)}）"
            )
        missing = sorted(self._required_backtest_keys() - given)
        if missing:
            raise JobSubmissionInvalidError(
                f"バックテスト設定に必須キーが足りません: {missing}"
                "（欠けたまま起動すると子プロセスで引数不足になり、"
                "投入は成功したのに実行だけ失敗する）"
            )

    def _reject_if_stop_loss_not_guaranteed(self, submission: JobSubmission) -> None:
        """§12.8: 設定が SL を保証しない sizing ON を受付時に明示拒否する。

        カタログが空集合を返す EA は「SL が設定パラメータで決まらない」という意味であり
        （例: WeeklyVolBand は VolatilityBand から導く）、設定の正値検査では保証を
        証明できない。その場合はここで決着させず、実行中の fail-stop に委ねる。
        """
        params = self._stop_loss_catalog.stop_loss_params(submission.ea_name)
        # None（探索できなかった）も空集合（設定パラメータを持たない）も、
        # 「設定の正値検査では SL 保証を証明できない」点は同じなので受付では決着させない
        # （実行中の fail-stop が受け皿・§12.8）。
        if not params:
            return
        backtest = submission.backtest or {}
        for name in sorted(params):
            value = backtest.get(name)
            if not isinstance(value, (int, float)) or isinstance(value, bool) or value <= 0:
                raise SizingUnsupportedError(
                    f"戦略 {submission.ea_name} でサイジングを有効にするには、SL（損切り幅）を"
                    f"決める設定 {name!r} が正の値である必要があります"
                    f"（指定値={value!r}）。ケリー基準は損切り幅が無いとリスク額を"
                    "定義できず、発注量を決められません"
                )
