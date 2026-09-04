"""フェーズ 4 差し戻し 🔴-1 / 🟡-1: EA ファクトリ選択規則の単一化と境界での Fail-Stop。

⚠️ 本モジュールは**実装より先に書いたテスト**である（Red）。

固定する仕様:

    🔴-1（選択規則の唯一化）:
        `_EA_FACTORIES`（ea_name → ファクトリの登録表）を**引く式は 1 箇所しか無い**。
        A-1 は `_select_ea_factory` を「規則の唯一の判定点」として新設したが、
        `build_ea_indicators` は経由せず生の表引きを続けていた（実測: math の kwargs で
        `DataError: 指標計算用 CSV の読み込みに失敗しました: None`）。結果、
        `sim_ui/main/run_job.py` の `_supply_contacts` が例外になり、終了コード 0 のまま
        report.json が生成されない run が生じていた。
        本検定は選択式の個数を **AST で機械的に**固定する（目視規約にしない）。

    🟡-1（規則 S が `build_interactor` 境界に届くこと）:
        `to_interactor_kwargs` を通らない投入経路（`POST /sim/jobs` → `run_backtest`）は
        `config_overrides` を素通しするため、A-1 が開いた経路が A-1 の守る不変条件の
        外側にあった（実測: `data_path=実在CSV` + `tick_model=math_calculations` で
        bars=0・exit=0・trades=0 と**警告も拒否も無く**完走した）。
        判定の宣言は `kwargs_mapper.verify_data_consistency`（規則 S）に置いたまま、
        `build_interactor` がそれを呼ぶ形で境界へ届かせる（判定を二重化しない）。
"""
from __future__ import annotations

import ast
import json
from pathlib import Path

import pytest

from marketdata.symbol_spec_snapshot import OANDA_JAPAN_MT5_LIVE, load_spec_fields
from simulator.domain.exceptions import ConfigError
from simulator.domain.tester_settings_exceptions import SettingsActivationError

_SIMULATOR_ROOT = Path(__file__).resolve().parents[2]
_MAIN_SOURCE = _SIMULATOR_ROOT / "main" / "__init__.py"

#: 表を**列挙**してよい関数（選択はしない）。選択規則は `_select_ea_factory` の 1 箇所のまま。
_ENUMERATOR = "known_ea_names"


def _private_ea_names() -> "frozenset[str]":
    """EA 構築の内部構造を表す `simulator/main/__init__.py` の私有名（単一ソース導出）。

    `_factory_*` を**手書きの一覧にしない**——登録 EA が増えるたびに取り残される。
    main の AST から module 直下の `_factory_*` 定義を拾い、固定 3 名と合わせる。
    """
    tree = ast.parse(_MAIN_SOURCE.read_text(encoding="utf-8"))
    factories = {
        node.name
        for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and node.name.startswith("_factory_")
    }
    return frozenset(factories | {"_EA_FACTORIES", "_EaBuildContext", "_select_ea_factory"})


def _production_sources() -> "list[Path]":
    """`simulator/` 配下の本番コード（`**/tests/**` を除く）。

    除外の根拠（実測）: 関数内で `_EA_FACTORIES` を読む検定が複数ある。いずれも
    「表が単一ソースであること」を固定する正当な参照であり、違反ではない。
    """
    return sorted(
        path
        for path in _SIMULATOR_ROOT.rglob("*.py")
        if "tests" not in path.relative_to(_SIMULATOR_ROOT).parts
    )


def _private_ea_references(path: Path) -> "frozenset[str]":
    """私有名への**コード上の**参照（docstring・コメントは対象外）。

    見る形式は 4 つ。現行ゲートは 1 だけを見ており、実測で 3 件中 1 件（形式 4）を
    取り逃していた（`ea_stop_loss_param_catalog` の `getattr(sim_main, "_EA_FACTORIES", {})`）。

        1. 素の名前参照   `_EA_FACTORIES.get(...)`
        2. from-import    `from simulator.main import _EA_FACTORIES`
        3. 属性参照       `sim_main._EA_FACTORIES`
        4. getattr 文字列 `getattr(sim_main, "_EA_FACTORIES", {})`
    """
    private = _private_ea_names()
    found: "set[str]" = set()
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.Name) and node.id in private:
            found.add(node.id)
        elif isinstance(node, ast.Attribute) and node.attr in private:
            found.add(node.attr)
        elif isinstance(node, ast.ImportFrom):
            found.update(alias.name for alias in node.names if alias.name in private)
        elif (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "getattr"
            and len(node.args) >= 2
            and isinstance(node.args[1], ast.Constant)
            and node.args[1].value in private
        ):
            found.add(node.args[1].value)
    return frozenset(found)

_MT5_FIXTURE = (
    Path(__file__).resolve().parents[1]
    / "fixtures"
    / "mt5"
    / "ma_slope_jp225_202501"
    / "input"
    / "JP225_M1_202501.csv"
)


def _math_kwargs(**overrides):
    """Settings 経路が組む math の投入 kwargs（`to_interactor_kwargs` の実出力）。"""
    from simulator.main.tester_settings.kwargs_mapper import to_interactor_kwargs
    from simulator.tests.tester_settings_engine_fixtures import (
        engine_binding,
        runnable_settings,
    )

    kwargs = to_interactor_kwargs(runnable_settings(Model="3"), engine_binding(data_path=None))
    kwargs.update(overrides)
    return kwargs


def _ma_slope_kwargs(**overrides):
    """⚠ ISSUE-445 段階 C: 本モジュールは銘柄仕様の**正しさを検証していない**。

    銘柄仕様 8 項目は供給元スナップショット
    （`marketdata/symbol_specs/OANDA-Japan-MT5-Live/JP225.json`）だけを権威とする
    （段階 B までは `contract_size=10.0` ほか 5 項目が供給元と食い違うリテラルだった）。
    本モジュールが見るのは registry の実装クラス名・bars の本数（28097）・規則 S の
    例外と終了コードだけであり、いずれも損益を計算しない。実測（2026-08-26）:
    `contract_size` だけを真値 1.0 にしても、5 項目を対で真値へ寄せても、25 検定とも
    緑のまま通った。

    したがって数値ピンを足す余地が無い。本モジュールの緑を「銘柄仕様の是正が
    正しい」根拠にしてはならない。損益への波及は
    `simulator/tests/unit/test_is_oos_barmode_index.py` の不変ピンが見る。
    """
    base = dict(
        data_path=_MT5_FIXTURE,
        symbol="JP225",
        period="M1",
        ea_name="MA_Slope_EA",
        initial_deposit=10_000.0,
        # 銘柄仕様 8 キー。ここにリテラルを書かない＝人が値を選べない。
        **load_spec_fields(OANDA_JAPAN_MT5_LIVE, "JP225"),
        ma_period=20,
        ma_method="ema",
        lot_size=0.1,
        stop_loss_points=0,
        take_profit_points=0,
    )
    base.update(overrides)
    return base


class TestFactorySelectionHasASinglePoint:
    """🔴-1: 選択式の個数を AST で固定する（複製が入った瞬間に落ちる）。"""

    @staticmethod
    def _factory_table_readers() -> "list[str]":
        """`_EA_FACTORIES` を**読む**関数名を列挙する（束縛＝定義は除く）。"""
        tree = ast.parse(_MAIN_SOURCE.read_text(encoding="utf-8"))
        readers: "list[str]" = []
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            for inner in ast.walk(node):
                if isinstance(inner, ast.Name) and inner.id == "_EA_FACTORIES":
                    if isinstance(inner.ctx, ast.Load):
                        readers.append(node.name)
                        break
        return readers

    def test_the_table_is_read_by_the_selector_and_the_enumerator_only(self):
        """表に触れてよい関数は 2 つだけ、かつ役割が違う。

        `_select_ea_factory` は**選択**（`.get(ea_name, 既定)`）、`known_ea_names` は
        **列挙**（キー集合）である。列挙は選択規則を含まない（下の `.get` 検定が
        「引く式は 1 箇所」を別途固定する）ため、規則の複製にはならない。
        `known_ea_names` を公開する動機は、外側スライス（`sim_ui`）が `set(_EA_FACTORIES)`
        を越境 import して同じ列挙を書き写していたこと（ISSUE-405 実測）である。
        3 つ目の読み手が入れば本検定が落ちる。
        """
        assert sorted(self._factory_table_readers()) == sorted(
            ["_select_ea_factory", _ENUMERATOR]
        )

    def test_the_enumerator_does_not_select(self):
        """列挙側が選択規則（既定フォールバック）を持たないこと。"""
        tree = ast.parse(_MAIN_SOURCE.read_text(encoding="utf-8"))
        enumerator = next(
            node
            for node in tree.body
            if isinstance(node, ast.FunctionDef) and node.name == _ENUMERATOR
        )
        gets = [
            inner
            for inner in ast.walk(enumerator)
            if isinstance(inner, ast.Call)
            and isinstance(inner.func, ast.Attribute)
            and inner.func.attr == "get"
        ]
        assert gets == []

    def test_the_fallback_default_appears_once_in_the_module(self):
        # 既定フォールバック（未登録 ea_name → TC 経路）も 1 箇所に限る。
        text = _MAIN_SOURCE.read_text(encoding="utf-8")
        tree = ast.parse(text)
        sites = [
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "get"
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id == "_EA_FACTORIES"
        ]
        assert len(sites) == 1


class TestPrivateEaNamesStayInsideTheEngineCompositionRoot:
    """ISSUE-405: 私有名の越境参照を `simulator/` 全体（tests を除く）で 0 に固定する。

    従来のゲートの射程は `simulator/main/__init__.py` **1 ファイルだけ**だった。その外側で
    `sim_ui/adapter` の 3 モジュールが `_EA_FACTORIES` / `_factory_tc24051901` /
    `_EaBuildContext` を越境 import し、EA ファクトリ選択のフォールバック規則
    （`_EA_FACTORIES.get(ea_name, _factory_tc24051901)`）を**書き写して**いた。
    規則が 1 箇所であることを main の内側だけで固定しても、外側の複製は素通りする。
    """

    def test_the_scan_reaches_the_sim_ui_adapters(self):
        """走査対象の取りこぼしで検定が空振りしないこと（ゲートの自己検査）。"""
        scanned = {
            str(path.relative_to(_SIMULATOR_ROOT)) for path in _production_sources()
        }
        assert "sim_ui/adapter/ea_registry_series_catalog.py" in scanned
        assert "sim_ui/adapter/ea_stop_loss_param_catalog.py" in scanned
        assert "sim_ui/adapter/symbol_spec_catalog.py" in scanned

    def test_the_detector_sees_the_getattr_string_form(self, tmp_path):
        """文字列リテラル引数の `getattr` を検出できること（形式 4 の自己検査）。

        現行実装（`ast.Name` だけを見る）はこの形を取り逃す。取り逃す検出器で
        「違反 0」を主張しても意味がないため、検出器の能力自体を固定する。
        """
        sample = tmp_path / "sample.py"
        sample.write_text(
            'from simulator import main as m\n'
            'v = getattr(m, "_EA_FACTORIES", {})\n',
            encoding="utf-8",
        )
        assert _private_ea_references(sample) == frozenset({"_EA_FACTORIES"})

    def test_the_detector_sees_the_attribute_form(self, tmp_path):
        sample = tmp_path / "sample.py"
        sample.write_text(
            'from simulator import main as m\nv = m._factory_tc24051901\n',
            encoding="utf-8",
        )
        assert _private_ea_references(sample) == frozenset({"_factory_tc24051901"})

    def test_the_factory_names_are_derived_from_the_module(self):
        """私有名の一覧を手書きしていないこと（登録 EA が増えたら自動で射程に入る）。"""
        names = _private_ea_names()
        assert "_factory_tc24051901" in names
        assert "_factory_dataless" in names
        assert "_EA_FACTORIES" in names

    def test_no_production_module_outside_main_touches_a_private_ea_name(self):
        offenders = {
            str(path.relative_to(_SIMULATOR_ROOT)): sorted(_private_ea_references(path))
            for path in _production_sources()
            if path != _MAIN_SOURCE
        }
        assert {name: refs for name, refs in offenders.items() if refs} == {}

    def test_only_engine_side_tests_may_reference_the_private_table(self):
        """`**/tests/**` の残存参照が「エンジン自身の検定」だけであることを実測で示す。

        除外の正当性は「テストだから見逃す」ではない。**表の所有スライス
        （`simulator/tests/**`）の検定が、表を単一ソースとして固定する**ための参照だけが
        残っている、という状態を固定する:

            * `test_ea_factory_registry.py`          — 表そのものの単体検定
            * `test_unsupported_n01_ea_name_source.py` — 注入集合 ⊇ 表のキー、という関係の固定

        ISSUE-405 の是正で `sim_ui/tests/**` 側の参照は 0 になった（本番コードの越境を
        テストへ移し替えただけ、にはなっていないことの実証）。
        """
        referencing = sorted(
            str(path.relative_to(_SIMULATOR_ROOT))
            for path in _SIMULATOR_ROOT.rglob("*.py")
            if "tests" in path.relative_to(_SIMULATOR_ROOT).parts
            and _private_ea_references(path)
        )
        assert referencing == [
            "tests/unit/test_ea_factory_registry.py",
            "tests/unit/test_unsupported_n01_ea_name_source.py",
        ]
        assert [
            path
            for path in referencing
            if (_SIMULATOR_ROOT / path) in _production_sources()
        ] == []


class TestBuildEaIndicatorsGoesThroughTheRule:
    """🔴-1: `build_ea_indicators` が判定点を経由する（data-less 規則を知る）。"""

    def test_math_kwargs_do_not_raise_a_data_error(self):
        from simulator.main import build_ea_indicators

        registry = build_ea_indicators(**_math_kwargs())
        assert registry.__class__.__name__ == "NullIndicatorRegistry"

    def test_bar_consuming_kwargs_keep_the_pandas_registry(self):
        from simulator.main import build_ea_indicators

        registry = build_ea_indicators(**_ma_slope_kwargs())
        assert registry.__class__.__name__ == "PandasIndicatorRegistry"

    def test_both_entry_points_agree_on_consuming_data(self):
        """同じ投入 kwargs なら 2 つの公開入口が同じ「データ消費」判定に落ちる。

        観測できる形（`build_interactor` の bars と `build_ea_indicators` の系列有無）で
        突合する。非公開属性へは到達しない（🟡-5 と同じ規律をテスト側にも適用する）。
        """
        from simulator.domain.exceptions import IndicatorBufferError
        from simulator.main import build_ea_indicators, build_interactor

        _c, math_request = build_interactor(**_math_kwargs())
        assert list(math_request.bars) == []
        with pytest.raises(IndicatorBufferError):
            build_ea_indicators(**_math_kwargs()).get("ema")

        _c2, bar_request = build_interactor(**_ma_slope_kwargs())
        assert len(list(bar_request.bars)) > 0
        assert len(build_ea_indicators(**_ma_slope_kwargs()).get("ema")) > 0


class TestRuleSReachesTheBuildInteractorBoundary:
    """🟡-1: 規則 S が `config_overrides` 素通し経路にも効く。"""

    def test_data_is_rejected_for_a_model_that_consumes_none(self):
        from simulator.main import build_interactor

        with pytest.raises(SettingsActivationError) as excinfo:
            build_interactor(
                **_ma_slope_kwargs(config_overrides={"tick_model": "math_calculations"})
            )
        assert excinfo.value.context["rule_id"] == "S"
        assert excinfo.value.context["tick_model"] == "math_calculations"

    def test_missing_data_is_rejected_for_a_model_that_consumes_bars(self):
        from simulator.main import build_interactor

        with pytest.raises(SettingsActivationError) as excinfo:
            build_interactor(**_ma_slope_kwargs(data_path=None))
        assert excinfo.value.context["rule_id"] == "S"

    def test_the_rejection_is_a_config_error(self):
        # 終了コード翻訳（ConfigError → 2）へそのまま載ること。
        from simulator.adapter.exit_codes import exit_code_for
        from simulator.main import build_interactor

        with pytest.raises(ConfigError) as excinfo:
            build_interactor(**_ma_slope_kwargs(data_path=None))
        assert exit_code_for(excinfo.value) == 2

    def test_run_backtest_translates_the_rejection_to_exit_code_two(self):
        from simulator.main import run_backtest

        exit_code, result = run_backtest(**_ma_slope_kwargs(data_path=None))
        assert exit_code == 2
        assert result is None

    def test_the_judgment_is_declared_once(self):
        """判定の宣言は `verify_data_consistency` 側にあり、main は呼ぶだけ。"""
        tree = ast.parse(_MAIN_SOURCE.read_text(encoding="utf-8"))
        raises = [
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.Raise)
            and isinstance(node.exc, ast.Call)
            and isinstance(node.exc.func, ast.Name)
            and node.exc.func.id == "SettingsActivationError"
        ]
        assert raises == []

    def test_the_matching_combinations_still_build(self):
        from simulator.main import build_interactor

        _c, request = build_interactor(**_ma_slope_kwargs())
        assert len(list(request.bars)) == 28097
        _c2, request2 = build_interactor(**_math_kwargs())
        assert list(request2.bars) == []


class TestMathJobProducesAReportPayload:
    """🔴-1 end-to-end: math ジョブが report.json を生成する（run_job の該当経路）。"""

    @pytest.fixture()
    def job_dir(self, tmp_path):
        from simulator.sim_ui.main import run_job

        spec = {"backtest": _serialisable(_math_kwargs())}
        (tmp_path / "spec.json").write_text(
            json.dumps(spec, ensure_ascii=False), encoding="utf-8"
        )
        assert run_job.main(["--job-dir", str(tmp_path)]) == 0
        return tmp_path

    def test_report_json_is_written(self, job_dir):
        assert (job_dir / "report.json").exists()

    def test_no_report_payload_error_is_recorded(self, job_dir):
        """`_supply_contacts`（→ `build_ea_indicators`）が例外を出していないこと。

        是正前はここで `DataError` になり、終了コード 0 のまま report.json が生成されず
        `report_payload_error.json` だけが残った（UI からは「成功したのに結果が出ない
        ジョブ」に見える）。
        """
        assert not (job_dir / "report_payload_error.json").exists()

    def test_the_payload_carries_the_run_summary(self, job_dir):
        payload = json.loads((job_dir / "report.json").read_text(encoding="utf-8"))
        assert payload["summary"]["single"]["trades"] == 0
        assert payload["segments"]["single"]["trades"] == []

    def test_the_job_reports_no_trades(self, job_dir):
        stats = json.loads((job_dir / "stats.json").read_text(encoding="utf-8"))
        assert stats["stats"]["trades"] == 0
        assert stats["trade_count"] == 0


def _serialisable(kwargs: dict) -> dict:
    """`spec.json` へ載せる形（Path → str）。値の意味は変えない。"""
    return {
        key: (str(value) if isinstance(value, Path) else value)
        for key, value in kwargs.items()
    }


class TestControllerExposesMarketData:
    """🟡-5: 非公開属性へ到達せずに注入実体を読める公開取得点。"""

    def test_market_data_is_the_injected_instance(self):
        from simulator.adapter.controller import BacktestController

        sentinel = object()
        controller = BacktestController(market_data=sentinel, interactor=object())
        assert controller.market_data is sentinel

    def test_market_data_is_read_only(self):
        from simulator.adapter.controller import BacktestController

        controller = BacktestController(market_data=object(), interactor=object())
        with pytest.raises(AttributeError):
            controller.market_data = object()

    def test_the_new_window_test_reaches_no_private_attribute(self):
        """A-3 で追加した検定が非公開属性へ到達しないこと（AST で機械的に固定する）。

        文字列検索では `windowed_market_data`（モジュール名）と区別できないため、
        属性アクセス節点だけを見る。docstring 中の説明文（`self._market_data.load` の
        引用）は AST の対象外なので誤検出しない。
        """
        path = Path(__file__).resolve().parent / "test_marketdata_window_mt5_path.py"
        tree = ast.parse(path.read_text(encoding="utf-8"))
        private = sorted(
            {
                node.attr
                for node in ast.walk(tree)
                if isinstance(node, ast.Attribute) and node.attr.startswith("_")
            }
        )
        assert private == []
