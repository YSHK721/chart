"""ジョブ実行系のプレーン DTO と usecase 例外（CLEAN_ARCH §5）。

境界を跨ぐデータは全て**プレーン**（dataclass / Mapping）にする。pydantic 型・HTTP の
Request/Response・pathlib の Path は usecase へ入れない（framework/adapter に留める）。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from simulator.sim_ui.domain.simulation_job import SimulationJob
from simulator.usecase.tester_settings import TICK_MODEL_ENGINE_IDS, TickModel

#: 実効粒度が tick になるエンジン tick_model id。語彙は列挙が単一ソースであり、
#: 文字列をここに書き写さない（`Model` の値と語の対応は 1 箇所にしか無い）。
_TICK_GRANULARITY_ENGINE_ID: str = TICK_MODEL_ENGINE_IDS[TickModel.REAL_TICKS]

#: `config_overrides.tick_model` 未指定時の既定（config_loader と同じ）。
DEFAULT_TICK_MODEL: str = TICK_MODEL_ENGINE_IDS[TickModel.EVERY_TICK]


def granularity_of(*, tick_model: str, pending_lifecycle: bool) -> str:
    """実効評価粒度（"bar"|"tick"）の**唯一の判定点**。

    `RunBacktestInteractor.execute` の分岐（run_backtest.py）に忠実: ``real_ticks``
    または ``pending_lifecycle`` の run は every-tick 経路（tick 粒度・B4）、それ以外
    （every_tick / ohlc_expand / open_only 等の合成 tick_model）は bar 経路（bar 粒度・B2）。

    入力を `EffectiveSettings` でも `backtest` でもなく **engine の tick_model id** に
    しているのは、settings 経路（`Model` 由来）と現行経路（`config_overrides` 由来）を
    同じ 1 つの判定へ合流させるためである（判定を 2 箇所に書けば片方だけ腐る）。
    """
    return "tick" if (tick_model == _TICK_GRANULARITY_ENGINE_ID or pending_lifecycle) else "bar"


@dataclass(frozen=True)
class JobSubmission:
    """ジョブ投入要求（§4.2 F-3 の入力）。

    ``backtest``: `simulator.main.run_backtest` へ渡す meta（ea_name / symbol / period /
      data_path / config_overrides ...）。sim コアは中身を解釈せず素通しする（子プロセスが
      解釈する）。ただし E-3 判定に必要な 2 つだけは読む（ea_name・entry_price_basis）。
    ``sizing``: サイジング設定。``None`` または ``enabled`` が偽なら **OFF**（既定・
      §12.1 で「既定 OFF・OFF は既存挙動と byte 等価」と裁定済み）。
    ``strategy``: 戦略項目（Phase 6 F-8・TBD-11）。``entry_long`` / ``entry_short`` の
      条件配列を持つ。``None`` または空なら **OFF**（既定・strategy 不在で既存挙動 byte 等価）。
      子プロセス（run_job）が :class:`GenericConditionStrategy` を構築して
      ``build_interactor(strategy_override=...)`` へ渡す。sim コアは中身を解釈せず、
      受付検証（E-5）で参照指標名の集合だけを読む。
    ``settings``: MT5 Tester Settings ブロック（Phase 8 §18・T-4）。形は
      ``{"tester": {キー: 生トークン}, "inputs": [行原文]}``。**生トークンのまま**運ぶ
      （型付き DTO へ写すと検証の第 2 実装ができ、往復（NFR-02）が壊れる）。``None``
      なら **OFF**（既定・settings 不在で既存挙動 byte 等価）。規則 B〜Q の検証は
      `SettingsValidationPort` が framework の単一ソースへ委譲する。
    """

    backtest: Mapping[str, Any]
    sizing: "Mapping[str, Any] | None" = None
    strategy: "Mapping[str, Any] | None" = None
    settings: "Mapping[str, Any] | None" = None

    @property
    def ea_name(self) -> str:
        return str(self.backtest.get("ea_name", ""))

    @property
    def strategy_enabled(self) -> bool:
        return bool(self.strategy)

    def strategy_indicator_names(self) -> "frozenset[str]":
        """戦略条件が参照する指標系列名の集合（E-5 受付検証用）。

        entry_long / entry_short の各条件の lhs ``indicator`` と、rhs が指標参照
        （マッピング）の場合の ``indicator`` を集める。op/shift の妥当性はここでは
        検査しない（それは run_job の loader が担い、未知 op 等は実行時 fail-stop）。
        本メソッドは「どの系列が要るか」だけを保守的に読む。
        """
        names: "set[str]" = set()
        strategy = self.strategy or {}
        for side in ("entry_long", "entry_short"):
            for cond in strategy.get(side, []) or []:
                if not isinstance(cond, Mapping):
                    continue
                lhs = cond.get("indicator")
                if isinstance(lhs, str):
                    names.add(lhs)
                rhs = cond.get("rhs")
                if isinstance(rhs, Mapping):
                    ref = rhs.get("indicator")
                    if isinstance(ref, str):
                        names.add(ref)
        return frozenset(names)

    @property
    def effective_granularity(self) -> str:
        """この run の実効評価粒度（"bar"|"tick"）を返す（Phase 7・粒度ゲート用）。

        判定そのものは :func:`granularity_of` が唯一持つ（規則の本文と既定値の字形を
        ここへ写さない。既定は :data:`DEFAULT_TICK_MODEL`）。本 property は**どの
        tick_model が権威か**だけを決める:

            settings 有り（Phase 8）: `.ini` の `Model` が権威（写像層 `_config_overrides`
              と同じ優先順位＝`Model` が `config_overrides.tick_model` を上書きする）。
            settings 不在（既定）  : 現行どおり `backtest.config_overrides.tick_model`。
        """
        overrides = self.backtest.get("config_overrides") or {}
        tick_model = self._settings_tick_model()
        if tick_model is None:
            tick_model = str(overrides.get("tick_model", DEFAULT_TICK_MODEL))
        return granularity_of(
            tick_model=tick_model,
            pending_lifecycle=bool(overrides.get("pending_lifecycle", False)),
        )

    def _settings_tick_model(self) -> "str | None":
        """settings の `Model`（生トークン）→ エンジンの tick_model id。

        変換表は :data:`TICK_MODEL_ENGINE_IDS`（`usecase/tester_settings/enums.py`）が
        単一ソースであり、対応をここに書き写さない。

        ``None`` を返す 2 つの場合——settings 不在／`Model` を持たないか未知値——は
        いずれも「settings は粒度を決めない」の意であり、呼出側は現行規則へ落ちる。
        `Model` は検証層の必須キーかつ既知値のみ受理であるため、受付検証を通った投入
        では必ず値が引ける（未知値の報告は rule_id 付きの検証例外が担う）。
        """
        tester = (self.settings or {}).get("tester") or {}
        raw = tester.get("Model")
        if raw is None:
            return None
        try:
            model = TickModel(int(str(raw)))
        except ValueError:
            return None
        return TICK_MODEL_ENGINE_IDS[model]

    def trailing_granularity(self) -> "str | None":
        """strategy.trailing の granularity（省略時 "bar"）。trailing 不在は None。"""
        strategy = self.strategy or {}
        trailing = strategy.get("trailing")
        if not isinstance(trailing, Mapping):
            return None
        return str(trailing.get("granularity", "bar"))

    def position_change_blocks(self) -> "tuple[Any, Any]":
        """strategy ブロックの (trailing, partial_close) 生値を返す（Phase 7 FR-07/08）。

        受付検証（構造チェック）用。意味検証（列挙・範囲）は run_job の framework loader が
        fail-stop で担うため、ここでは中身を解釈しない（存在と型だけを保守的に読む）。
        strategy 不在は (None, None)＝OFF。
        """
        strategy = self.strategy or {}
        return strategy.get("trailing"), strategy.get("partial_close")

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
    #: この状態がもう遷移しないか（Phase 9 段階 3・§19.6 R1）。出所は domain の
    #: :attr:`JobStatus.is_terminal` ただ 1 つであり、終端集合を写した第 2 実装を
    #: 作らない（front は本フラグを読むだけで監視を止める）。
    terminal: bool = False

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
            terminal=job.status.is_terminal,
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
