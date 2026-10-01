"""Composition Root（ジョブ対応 sim core・main 層・CLEAN_ARCH §8）。

Phase 1 の `composition_root.build_sim_app`（配信面だけ）を置き換えずに**併存**させる。
本モジュールは配信面に加えてジョブ実行系（F-3）の Port 実装を結線する。

結線（DIP: usecase は抽象にのみ依存し、実装の選択はここだけが知る）:
    JobLedgerPort              → FileJobLedger（FS 台帳）
    JobLauncherPort            → SubprocessJobLauncher（子プロセス・setsid しない）
    IndicatorSeriesCatalogPort → EaRegistrySeriesCatalog（`build_ea_indicators` で実構築）
    StopLossParamCatalogPort   → EaStopLossParamCatalog（`build_ea_strategy` で実構築）
    RunOptionsPort             → SymbolSpecCatalog（`known_ea_names` を EA 名の権威に・
                                 建値基準の値は変換層の単一ソースから注入）
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
from typing import Any, Callable

from common import core_web_topology
from simulator.sim_ui.adapter.ea_build_probe import EaBuildProbe
from simulator.sim_ui.adapter.ea_registry_series_catalog import EaRegistrySeriesCatalog
from simulator.sim_ui.adapter.ea_stop_loss_param_catalog import EaStopLossParamCatalog
from simulator.sim_ui.adapter.file_job_ledger import FileJobLedger
from simulator.sim_ui.adapter.settings_ini_validator import SettingsIniValidator
from simulator.sim_ui.adapter.symbol_spec_catalog import SymbolSpecCatalog
from simulator.sim_ui.adapter.subprocess_job_launcher import SubprocessJobLauncher
from simulator.sim_ui.adapter.tester_settings_schema_catalog import (
    TesterSettingsSchemaCatalog,
)
from simulator.sim_ui.framework.serve_sim_jobs import SimJobApp
from simulator.sim_ui.main.composition_root import CORE_NAME
from simulator.sim_ui.usecase.job_ports import EaSubjectPort

# repo 根 = simulator/sim_ui/main/composition_root_jobs.py の parents[3]。
_REPO_ROOT = Path(__file__).resolve().parents[3]


#: ea_name → 建値推定に要る指標系列名（`None` は「要らない」）。同じ ea_name を 2 度
#: 探索しないための置き場である。探索は EA を 1 本組み立てる（使い捨てデータセットへの
#: 書き出しを伴う）ので、投入ごとに組み直すと受付が投入数に比例して重くなる——出力は
#: 1 ビットも変わらないため状態検証では落ちない浪費である
#: （`EaStopLossParamCatalog._cache` と同じ流儀）。
_REQUIRED_SERIES_CACHE: "dict[str, str | None]" = {}


def _required_series(ea_name: str) -> "str | None":
    """ea_name → 建値推定に要る指標系列名（E-3 判定に使う・§12.5）。

    **入力は ea_name であって設定値ではない**（ISSUE-533 段階 2）。建値基準の権威は戦略の
    宣言ただ 1 つなので、必要な系列もその宣言から導く。設定から導いていた是正前は、
    データ実体が載せた値で判定していたため、宣言が ``current_open`` の EA
    （`MA_Slope_Pending_EA` / `WeeklyVolBand_EA`・実測 2026-09-25）が、供給の無い実体では
    「``close``」 系列を要求されて**登録系列にあるのに拒まれる**形になっていた。

    宣言が 「`NO_BAR_BOUNDARY_DECISION`」（足境界で判定しない）なら ``None`` を返す。その戦略は
    足境界の成行を出さないので推定建値を要さず、E-3 の判定対象ではない。

    規約の所有者は sizing 側なので系列名の対応は `simulator.usecase.sizing_ports` へ委譲する。
    import を関数内に置いているのは、サイジング **OFF**（既定）の経路がサイジング実装の
    読み込みに巻き込まれないようにするため（§12.1「OFF は既存挙動と byte 等価」）。
    sizing ON の投入が来て初めて解決される。
    """
    if ea_name in _REQUIRED_SERIES_CACHE:
        return _REQUIRED_SERIES_CACHE[ea_name]

    from simulator.usecase.entry_price_basis import declared_entry_price_basis
    from simulator.usecase.sizing_ports import required_price_series

    declared = declared_entry_price_basis(EaBuildProbe(_build_ea_strategy).for_ea(ea_name))
    needed = None if declared is None else required_price_series(declared)
    _REQUIRED_SERIES_CACHE[ea_name] = needed
    return needed


def _dev_path_entries(root: Path) -> "list[Path]":
    """repo 根 → 子プロセスへ渡す import パス列（`SubprocessJobLauncher` への束縛）。

    「1 つのチェックアウトを構成する import パス」の唯一源は `tools/dev_paths.txt` で
    あり、その導出関数（.pth 生成器と同じもの）を本 Root だけが掴む。adapter に持たせると
    層ゲート（sim_ui の test_launcher_path_source_injection）を破るため、束縛はここに閉じる
    （ISSUE-479 是正 1）。

    束縛先は中立核 common.dev_paths の path_entries である（ISSUE-502 C-1）。かつては
    運用スクリプト層の install_dev_paths を掴んでおり、tools → simulator（ops が
    product を駆動する既存の向き）と合わせて **循環**していた。遅延 import で辺を封じ込めて
    いたが、封じ込めは所有権の是正ではない。読み手は stdlib だけで書かれた汎用抽象であり
    どちらのアクターにも属さないため、中立核へ移した（common の watch_loop と同じ解）。

    import を関数内に置く理由は `_build_ea_indicators` と同じではない——中立核は stdlib のみ
    なので import 自体は無害である。関数内に置くのは **台帳の読込を結線時に発行しない**ため
    （sim_ui の注入検定にある計算量検定が「結線だけでは 0 回・子環境 1 個に
    つき 1 回」を Spy で固定しており、束縛先をモジュール属性として都度解決する形が要る）。
    """
    from common.dev_paths import path_entries

    return path_entries(root)


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


def build_settings_schema_port() -> TesterSettingsSchemaCatalog:
    """Tester Settings フォームの schema を供給する SettingsSchemaPort（束縛済み・Phase 8）。

    **注入束縛の単一点**である。カタログ（adapter）は列挙（`usecase/tester_settings/enums.py`）
    しか直接知らず、外側に属する事実——字句層の標準キー順・検証層の必須キーと Expert 専用
    キー・エンジンの実行可能 EA 名・対象接尾辞・非対象の宣言表——はすべてここで束ねる。
    束縛点を関数にしてあるのは、配信面の Composition Root（`composition_root_display`）が
    同じ結線を書き写さないようにするためである（`build_run_options_port` と同じ理由）。

    import を関数内に置く理由は `_build_ea_indicators` と同じ（本モジュールの import で
    設定検証系一式を引き込まない。schema が実際に要求された時点で解決される）。
    """
    from simulator.adapter.tester_settings.ini_codec import STANDARD_KEY_ORDER
    from simulator.framework.tester_settings.validation import (
        DATE_VALUE_KEYS,
        EXPERT_ONLY_KEYS,
        FLAG_VALUE_KEYS,
        required_tester_keys,
    )
    from simulator.main.tester_settings.ea_input_map import SUBJECT_SUFFIX
    from simulator.main.tester_settings.unsupported import RULES

    return TesterSettingsSchemaCatalog(
        key_order=STANDARD_KEY_ORDER,
        required_keys=required_tester_keys(),
        expert_only_keys=EXPERT_ONLY_KEYS,
        date_keys=DATE_VALUE_KEYS,
        flag_keys=FLAG_VALUE_KEYS,
        known_ea_names=_known_ea_names,
        subject_suffix=SUBJECT_SUFFIX,
        unsupported_rules=RULES,
    )


def build_settings_validation_port() -> SettingsIniValidator:
    """Tester Settings の受付検証 Port（Phase 8 §18.4 スライス 3）。

    実装（adapter）が framework の `tester_settings_from_mapping` へ委譲するため、
    本関数は実装の**選択**だけを行う（規則をここに書かない）。

    具象検証関数の束縛点はここ 1 箇所である（ISSUE-479 F-5）。adapter 側に既定値を置くと
    adapter → framework の逆流が復活するため、必ず注入する。import を関数内に置く理由は
    `_build_ea_indicators` と同じ（本モジュールの import で設定検証系一式を引き込まない）。
    """
    from simulator.framework.tester_settings import tester_settings_from_mapping

    return SettingsIniValidator(tester_settings_from_mapping)


class _EaSubject(EaSubjectPort):
    """`ea_stem`（エンジンの単一ソース）への束縛（:class:`EaSubjectPort` 実装）。

    語幹の取り出し規則（Windows 区切り・`.ex5` 接尾辞の扱い）は
    `simulator.main.tester_settings.ea_input_map.ea_stem` が唯一持つ。usecase / adapter は
    `simulator.main` を import できない（層ゲート）ため、束縛は本 Composition Root が担う。
    """

    def stem_of(self, subject_path: str) -> str:
        from simulator.main.tester_settings.ea_input_map import ea_stem

        return ea_stem(subject_path)


def build_ea_subject_port() -> _EaSubject:
    """`Expert` → EA 名の語幹を供給する Port（束縛済み・Phase 8）。"""
    return _EaSubject()


def build_trace_window_check() -> "Callable[[Any, Any], Any]":
    """トレース期間の妥当性検査（束縛済み・ISSUE-508 段階 3 §6.4）。

    実体は `adapter/trace/trace_window.py` の `TraceWindow.of` ただ 1 つである
    （epoch_seconds による正規化・半開 `[start, end)`・`start > end` の拒否）。
    usecase（`simulator/sim_ui/usecase/submit_job.py`）は adapter を import できないため、規則を写す代わりに
    本 Root が束ねて注入する。判定を受付層へ書き写すと、同じ規則が 2 箇所になる。

    戻り値の契約（消費側 `SubmitJobInteractor._trace_window_check` の宣言と同一）:
        `(start, end)` を受け、**解釈できない境界対では例外を送出する**。返り値は
        受付段では使わない——受付が要るのは「その期間が成立するか」だけであり、
        窓の実体は子プロセスが spec から組み直す（別プロセスへ渡せないため）。
        本 Root が返すのは `TraceWindow.of` そのものなので、検査規則と組立規則が
        同一の実体であることが構造から保証される（2 つ目の判定が生まれない）。
    """
    from simulator.adapter.trace.trace_window import TraceWindow

    return TraceWindow.of


# 子へ素通しする `backtest` meta が注入専用に予約しているキー。JSON から渡させない。
#   `strategy_decorator` は run_job がサイジング設定から組み立てて注入する（E-2）。
#   `strategy_override` は run_job が spec.strategy から GenericConditionStrategy を組んで
#   注入する（Phase 6 F-8）。どちらも StrategyPort 実体であり JSON スカラーでは渡せない。
#   `run_tracer` は run_job が spec.trace から列トレース（`simulator/adapter/trace/columnar_run_trace.py`）を組んで注入する
#   （ISSUE-508 段階 3・是正 D-2）。`allowed_backtest_keys()` は
#   `inspect.signature(build_interactor)` の反射であるため、ここへ足さないと JSON から
#   `backtest.run_tracer` を投入できてしまう——受け取れば必ず実行段で壊れる形である。
_INJECTED_ONLY_KEYS = frozenset(
    {"strategy_decorator", "strategy_override", "run_tracer"}
)


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
        else core_web_topology.primary_fallback_root(CORE_NAME, root)
    )
    jobs_root = (
        Path(data_root).resolve()
        if data_root is not None
        else root / "simulator" / "sim_ui" / "data"
    )

    ledger = FileJobLedger(data_root=jobs_root)
    # ジョブディレクトリの解決は台帳の採番規則をそのまま使う（FS 配置を二重定義しない）。
    # 子へ渡す import パスの導出も同じく注入する（束縛点は本 Root 1 箇所・是正 1）。
    launcher = SubprocessJobLauncher(
        job_dir_of=ledger.job_dir,
        path_entries=_dev_path_entries,
        repo_root=root,
    )

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
        # Phase 8 §18: settings ブロックを持つ投入だけが使う 2 Port。
        settings_validator=build_settings_validation_port(),
        ea_subject=build_ea_subject_port(),
        # ISSUE-508 段階 3 §6.4: trace ブロックの期間検査。規則の実体は adapter が
        # 唯一持ち（`TraceWindow.of`）、usecase は adapter を import できないため
        # **束縛は本 Composition Root が担う**（required_series と同一様式）。
        trace_window_check=build_trace_window_check(),
    )
