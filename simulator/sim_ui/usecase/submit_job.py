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
    EaSubjectPort,
    IndicatorSeriesCatalogPort,
    JobLauncherPort,
    JobLedgerPort,
    SettingsValidationPort,
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
        settings_validator: "SettingsValidationPort | None" = None,
        ea_subject: "EaSubjectPort | None" = None,
    ) -> None:
        self._ledger = ledger
        self._launcher = launcher
        self._series_catalog = series_catalog
        self._required_series = required_series
        self._stop_loss_catalog = stop_loss_catalog
        self._allowed_backtest_keys = allowed_backtest_keys
        self._required_backtest_keys = required_backtest_keys
        # Phase 8（§18）: settings ブロックを持つ投入だけが使う 2 Port。既定 ``None`` は
        # 「settings 経路を結線していない構成」を表す。settings 不在の投入は 1 度も
        # 触れないため、既存の結線（Phase 1〜7）は 1 行も変えずに動く（OCP）。
        self._settings_validator = settings_validator
        self._ea_subject = ea_subject

    def execute(self, submission: JobSubmission) -> JobView:
        """投入して現在状態を返す。E-3 違反は :class:`SizingUnsupportedError`。"""
        # sizing の有無に関係なく、子へ素通しする meta のキーを先に検証する
        # （未知キーは子プロセスで TypeError になり「投入は通ったが実行だけ落ちる」
        #  という遅い失敗になる。受付で弾いて即座に理由を返す）。
        self._reject_invalid_backtest_keys(submission)
        # Tester Settings（Phase 8 §18）: settings present のときだけ検証する。
        # **戦略項目の検証より先**に置く: 粒度ゲート（下）は settings の `Model` を権威に
        # 実効粒度を決めるため、`Model` の妥当性が確定していないと判定の前提が立たない。
        if submission.settings:
            self._reject_invalid_settings(submission)
        # 戦略項目（Phase 6 E-5）: 参照する指標系列が当該 ea_name の登録系列に含まれるかを
        # 受付時に検証する（sizing とは独立）。E-3 と同じ系列カタログ Port を再利用する。
        if submission.strategy_enabled:
            self._reject_if_strategy_indicators_unavailable(submission)
            # 建玉変更（Phase 7 FR-07/08）: trailing/partial_close サブブロックの構造を受付で
            # 検証する（マッピングでなければ即拒否）。意味検証（列挙・範囲）は run_job の
            # framework loader が fail-stop で担う（受付は構造のみ・二重化しない）。
            self._reject_invalid_position_change(submission)
            # 粒度不一致の fail-stop（🟡・無言不作動の防止）: トレーリングの granularity が
            # この run の実効粒度と一致しないと B2/B4 のどちらでも発火せず無音で不作動になる。
            self._reject_trailing_granularity_mismatch(submission)
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

    def _reject_invalid_settings(self, submission: JobSubmission) -> None:
        """Phase 8（§18.4 スライス 3）: settings ブロックの受付検証 3 本。

        a. 設定規則（B〜Q）— `SettingsValidationPort` が framework の単一ソースへ委譲する。
        b. 実行対象の一致 — `Expert` の語幹（`EaSubjectPort`）と `backtest.ea_name` が
           一致すること。食い違ったまま実行すると「指定した EA と違う EA の結果」が
           静かに出る（どちらが権威かを決めずに両方渡す形にはしない）。
        c. T-2 裁定 — `[TesterInputs]` は Phase 8 では実行不能。束縛表（`EA_INPUT_BINDINGS`）
           が空であり、入力 1 行でも実行段で必ず `ConfigError` になる。受付で理由つきに
           拒否して「投入は通ったのに実行だけ落ちる」遅い失敗を作らない。

        検証の順序は a → b → c（より根本的な理由から返す）。
        """
        settings = submission.settings or {}
        tester = settings.get("tester") or {}
        inputs = settings.get("inputs") or []
        if self._settings_validator is None or self._ea_subject is None:
            raise JobSubmissionInvalidError(
                "この構成は Tester Settings 経路を受け付けません"
                "（settings ブロックの検証 Port が結線されていません）"
            )
        self._settings_validator.validate(tester, inputs)

        subject = str(tester.get("Expert", ""))
        stem = self._ea_subject.stem_of(subject)
        if stem != submission.ea_name:
            raise JobSubmissionInvalidError(
                f"Tester Settings の Expert={subject!r}（EA 名={stem!r}）は、実行仕様の "
                f"ea_name={submission.ea_name!r} と一致しません。同じ EA を指してください"
                "（食い違ったまま実行すると、指定した EA と違う EA の結果が出ます）"
            )

        if inputs:
            raise JobSubmissionInvalidError(
                f"[TesterInputs] は現在実行できません（指定 {len(inputs)} 行）。EA 入力の"
                "束縛表が空のため、1 行でも指定すると実行段で必ず設定エラーになります。"
                "SL / TP / 移動平均 / ロット等は実行仕様（backtest）側で指定してください"
            )

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

    def _reject_if_strategy_indicators_unavailable(self, submission: JobSubmission) -> None:
        """E-5: 戦略条件が参照する指標系列が ea_name の registry に無ければ明示拒否する。

        判定は**ハードコードの戦略リストではなく** EA 別の系列カタログ Port から導出する
        （§12.1「戦略リストのハードコード禁止」・E-3 と同じ Port を再利用）。無音で OFF に
        倒したり別系列で代用したりせず、何が足りないかを文言で返す。
        """
        referenced = submission.strategy_indicator_names()
        available = self._series_catalog.series_for(submission.ea_name)
        missing = sorted(referenced - available)
        if missing:
            raise JobSubmissionInvalidError(
                f"戦略条件が参照する指標系列 {missing} は EA {submission.ea_name} の指標"
                f"レジストリに存在しません（登録系列={sorted(available)}）。ea_name"
                "（指標セット）の選択と条件の指標名を一致させてください"
            )

    def _reject_invalid_position_change(self, submission: JobSubmission) -> None:
        """Phase 7: trailing/partial_close が present なら **マッピング**であることを検証する。

        中身（trigger_points・close_fraction・granularity 等）の意味検証は run_job の
        `position_manager_spec_loader` が fail-stop で担う（usecase から framework へは依存
        しない・二重化しない）。ここは「投入は通ったが実行だけ落ちる」を減らすための構造検査
        に留める。
        """
        from typing import Mapping as _Mapping

        trailing, partial_close = submission.position_change_blocks()
        for name, block in (("trailing", trailing), ("partial_close", partial_close)):
            if block is not None and not isinstance(block, _Mapping):
                raise JobSubmissionInvalidError(
                    f"strategy.{name} はマッピング（key: value）である必要があります"
                    f"（指定型={type(block).__name__}）"
                )

    def _reject_trailing_granularity_mismatch(self, submission: JobSubmission) -> None:
        """🟡 トレーリングの granularity と run の実効粒度の不一致を fail-stop で拒否する。

        トレーリングは自身の設定粒度と一致する評価点（bar 経路=B2 / tick 経路=B4）でのみ
        作動する。real_ticks 実行（tick 粒度）に granularity="bar"、または bar 実行（合成
        tick_model）に granularity="tick" を与えると、どちらの評価点でも発火せず**無音で
        不作動**になる（partial_close は粒度非依存で常時作動するため気づきにくい）。受付で
        明示エラーにして「設定したのに効かない」を作らない。trailing 不在時は無検査。
        """
        wanted = submission.trailing_granularity()
        if wanted is None:
            return
        effective = submission.effective_granularity
        if wanted != effective:
            raise JobSubmissionInvalidError(
                f"トレーリングの granularity={wanted!r} は、この run の実効評価粒度"
                f"={effective!r}（tick_model 由来）と一致しないため作動しません。"
                f"bar 実行（合成 tick_model）は granularity='bar'、real_ticks 実行は"
                f" granularity='tick' を指定してください（無音の不作動を防ぐため受付で拒否）"
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
