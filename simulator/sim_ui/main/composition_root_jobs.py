"""Composition Root（ジョブ対応 sim core・main 層・CLEAN_ARCH §8）。

Phase 1 の `composition_root.build_sim_app`（配信面だけ）を置き換えずに**併存**させる。
本モジュールは配信面に加えてジョブ実行系（F-3）の Port 実装を結線する。

結線（DIP: usecase は抽象にのみ依存し、実装の選択はここだけが知る）:
    JobLedgerPort              → FileJobLedger（FS 台帳）
    JobLauncherPort            → SubprocessJobLauncher（子プロセス・setsid しない）
    IndicatorSeriesCatalogPort → EaRegistrySeriesCatalog（`build_ea_indicators` で実構築）
    StopLossParamCatalogPort   → EaStopLossParamCatalog（`build_ea_strategy` で実構築）
    RunOptionsPort             → SymbolSpecCatalog（`known_ea_names` を EA 名の権威に）
    必要系列を決める関数        → `simulator.usecase.sizing_ports.required_price_series`

エンジン（`simulator.main`）を知ってよいのは本モジュールと `run_job.py` だけである
（ISSUE-405・R-4 の一般化）。adapter は公開アクセサへの束縛を**注入**で受け取り、
`simulator.main` を直接 import しない。機械強制は
`sim_ui/tests/unit/test_sim_ui_import_direction.py`。

``data_root`` に既定値（`<repo>/simulator/sim_ui/data`）を持たせているのは、
`unified_ui/serve.sh` の改変を承認済みの最小範囲（E-1: import と呼び出しの約 3 行）に
収めるためである。起動スクリプトへ新しい変数を足さずに済む。
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from simulator.sim_ui.adapter.ea_build_probe import EaBuildProbe
from simulator.sim_ui.adapter.ea_registry_series_catalog import EaRegistrySeriesCatalog
from simulator.sim_ui.adapter.ea_stop_loss_param_catalog import EaStopLossParamCatalog
from simulator.sim_ui.adapter.file_job_ledger import FileJobLedger
from simulator.sim_ui.adapter.symbol_spec_catalog import SymbolSpecCatalog
from simulator.sim_ui.adapter.subprocess_job_launcher import SubprocessJobLauncher
from simulator.sim_ui.framework.serve_sim_jobs import SimJobApp

# repo 根 = simulator/sim_ui/main/composition_root_jobs.py の parents[3]。
_REPO_ROOT = Path(__file__).resolve().parents[3]


def _required_series(entry_price_basis: str) -> str:
    """約定価格基準 → 建値推定に要る指標系列名（E-3 判定に使う・§12.5）。

    サイジング側の規約が権威なので `simulator.usecase.sizing_ports` へ委譲する。
    import を関数内に置いているのは、サイジング **OFF**（既定）の経路が
    サイジング実装の読み込みに巻き込まれないようにするため（§12.1「OFF は既存挙動と
    byte 等価」）。sizing ON の投入が来て初めて解決される。
    """
    from simulator.usecase.sizing_ports import required_price_series

    return required_price_series(entry_price_basis)


def _build_ea_indicators(**spec: Any) -> Any:
    """`simulator.main.build_ea_indicators` への束縛（EA 別の指標レジストリ）。

    関数内 import にしているのは、本モジュールの import で pandas 一式を引き込まない
    ため（探索が実際に必要になった時点で解決される）。
    """
    from simulator.main import build_ea_indicators

    return build_ea_indicators(**spec)


def _build_ea_strategy(**spec: Any) -> Any:
    """`simulator.main.build_ea_strategy` への束縛（EA 別の戦略実体）。"""
    from simulator.main import build_ea_strategy

    return build_ea_strategy(**spec)


def _known_ea_names() -> "tuple[str, ...]":
    """`simulator.main.known_ea_names` への束縛（実行可能な EA 名の権威）。"""
    from simulator.main import known_ea_names

    return known_ea_names()


def build_series_catalog() -> EaRegistrySeriesCatalog:
    """E-3 判定の系列カタログ（束縛済み）。

    束縛点を関数にしてあるのは、配信面の Composition Root
    （`composition_root_display`）が同じ結線を**書き写さない**ようにするためである
    （同じ 1 行を 2 つの root に置くと、束縛先を変えたとき片方だけが腐る）。
    """
    return EaRegistrySeriesCatalog(probe=EaBuildProbe(_build_ea_indicators))


def build_stop_loss_catalog() -> EaStopLossParamCatalog:
    """§12.8 受付時 SL 検証のカタログ（束縛済み）。"""
    return EaStopLossParamCatalog(probe=EaBuildProbe(_build_ea_strategy))


def build_run_options_port() -> SymbolSpecCatalog:
    """実行指示フォームの選択肢を供給する RunOptionsPort（束縛済み）。"""
    return SymbolSpecCatalog(known_ea_names=_known_ea_names)


# 子へ素通しする `backtest` meta が注入専用に予約しているキー。JSON から渡させない。
#   `strategy_decorator` は run_job がサイジング設定から組み立てて注入する（E-2）。
#   `strategy_override` は run_job が spec.strategy から GenericConditionStrategy を組んで
#   注入する（Phase 6 F-8）。どちらも StrategyPort 実体であり JSON スカラーでは渡せない。
_INJECTED_ONLY_KEYS = frozenset({"strategy_decorator", "strategy_override"})


def allowed_backtest_keys() -> "frozenset[str]":
    """`backtest` に指定してよいキーの集合（🔴-5b）。

    **単一ソース**は `simulator.main.build_interactor` の実シグネチャ。手書きの表を
    持つと引数が増えたときに必ず取り残される（本リポジトリで繰り返し起きている
    壊れ方。`walk_forward_cli._BUILD_INTERACTOR_KEYWORDS` が実際にそれで壊れた）。
    """
    import inspect

    from simulator.main import build_interactor

    params = frozenset(inspect.signature(build_interactor).parameters)
    return params - _INJECTED_ONLY_KEYS


def required_backtest_keys() -> "frozenset[str]":
    """`backtest` に**必ず**必要なキーの集合（🟡-A）。

    `allowed_backtest_keys` と**同一ソース**（`build_interactor` の実シグネチャ）から、
    「既定値を持たない引数＝必須」として導出する。必須リストを別に手書きすると
    許可集合と二重管理になり、片方だけ腐る。

    これが無いと、必須引数の欠けた投入が 202 で受理され、子プロセスで
    `missing ... required keyword-only arguments` になる（遅い失敗）。
    """
    import inspect

    from simulator.main import build_interactor

    params = inspect.signature(build_interactor).parameters
    return frozenset(
        name
        for name, p in params.items()
        if p.default is inspect.Parameter.empty
    ) - _INJECTED_ONLY_KEYS


def build_sim_job_app(
    *,
    repo_root: Any = None,
    web_dir: Any = None,
    shared_js_root: Any = None,
    data_root: Any = None,
) -> SimJobApp:
    """配信面とジョブ実行系を結線した :class:`SimJobApp` を返す。

    ``web_dir`` / ``shared_js_root`` の規約は Phase 1 の `build_sim_app` と同一。
    ``data_root``: ジョブ台帳と結果ペイロードの根（既定 `<repo>/simulator/sim_ui/data`）。
    """
    root = Path(repo_root).resolve() if repo_root is not None else _REPO_ROOT
    shared_js = (
        Path(shared_js_root).resolve()
        if shared_js_root is not None
        else root / "indigators" / "indicator_ui" / "web"
    )
    jobs_root = (
        Path(data_root).resolve()
        if data_root is not None
        else root / "simulator" / "sim_ui" / "data"
    )

    ledger = FileJobLedger(data_root=jobs_root)
    # ジョブディレクトリの解決は台帳の採番規則をそのまま使う（FS 配置を二重定義しない）。
    launcher = SubprocessJobLauncher(job_dir_of=ledger.job_dir, repo_root=root)

    return SimJobApp(
        web_dir=web_dir,
        shared_js_root=shared_js,
        ledger=ledger,
        launcher=launcher,
        series_catalog=build_series_catalog(),
        required_series=_required_series,
        # §12.8: SL 保証の受付時検証。判定は EA 別カタログから導出する（戦略リスト不使用）。
        stop_loss_catalog=build_stop_loss_catalog(),
        allowed_backtest_keys=allowed_backtest_keys,
        required_backtest_keys=required_backtest_keys,
    )
