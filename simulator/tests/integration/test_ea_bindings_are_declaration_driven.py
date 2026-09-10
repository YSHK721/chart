"""EA 追加が「EA 側モジュール 1 つ＋宣言 1 行」で済むことを機械的に固定する。

ISSUE-502 段階 4A（SOLID 精査台帳 2026-09-06 の「OCP 高: EA 追加＝main/__init__.py 内
8 種の編集点（実測 10 行＋1 ファイル）」の是正）。

固定する仕様:

    1. **Composition Root は EA を知らない**。`simulator/main/__init__.py` に EA 名・
       戦略クラス・registry ビルダ・ファクトリが 1 つも現れない。
    2. **戦略パラメータの名前は宣言が持つ**。`build_interactor` 本体に dict リテラルの
       写しが無く、配る集合は宣言の和と一致する。
    3. **公開シグネチャは 1 文字も変わっていない**（38 引数・名前・並び・既定値）。
       3 本番モジュールがこれを `inspect.signature` で反射する事実上の HTTP スキーマで
       あるため、宣言駆動化の副作用で動いていないことを独立に測る。
    4. **仮引数の束が仮引数と一致する**。`build_interactor` は `dict(locals())` で
       自分の引数束を作り、宣言が名前で引く。位置がずれれば束に局所変数が混ざるので、
       `inspect.signature` と突合して機械的に赤にする。

なぜ「編集点」を数えるか:
    OCP 違反は「動かない」形では現れない——動くが、拡張のたびに既存を開くという形で
    現れる。したがって測るのは挙動ではなく**拡張時に触るファイルと行**である。ここでは
    その代理として「Composition Root に EA 固有の語が現れないこと」を測る（語が 0 なら、
    EA を足すときにそのファイルを開く理由が無い）。
"""
from __future__ import annotations

import ast
import inspect
from pathlib import Path

import pytest

from simulator.main import build_interactor
from simulator.main.ea_bindings import (
    COMMON_STRATEGY_PARAMS,
    DEFAULT_EA_NAME,
    _EA_BINDINGS,
    known_ea_names,
    strategy_param_names,
)

_SIMULATOR_ROOT = Path(__file__).resolve().parents[2]
_MAIN_SOURCE = _SIMULATOR_ROOT / "main" / "__init__.py"
_EA_BINDINGS_DIR = _SIMULATOR_ROOT / "main" / "ea_bindings"


def _main_tree() -> ast.Module:
    return ast.parse(_MAIN_SOURCE.read_text(encoding="utf-8"))


def _build_interactor_def() -> ast.FunctionDef:
    return next(
        node
        for node in _main_tree().body
        if isinstance(node, ast.FunctionDef) and node.name == "build_interactor"
    )


class TestTheCompositionRootDoesNotKnowAnyEa:
    """1: EA 固有の語が Composition Root 本体に 1 つも無いこと。"""

    def test_no_ea_name_literal_appears_in_the_composition_root(self):
        """EA 名の文字列リテラルが本体に無いこと（既定名も含む）。

        `DEFAULT_EA_NAME` は本体で**再輸出**されるが、値は束縛側が持つ。リテラルが本体に
        戻れば、既定を変えるときに 2 箇所が同時に腐る配置へ逆戻りする（ISSUE-405 の型）。
        """
        ea_names = set(known_ea_names()) | {DEFAULT_EA_NAME}
        offenders = [
            (node.lineno, node.value)
            for node in ast.walk(_main_tree())
            if isinstance(node, ast.Constant) and node.value in ea_names
        ]
        assert offenders == [], offenders

    def test_no_strategy_class_is_imported_by_the_composition_root(self):
        """戦略クラスの import が本体に無いこと（EA 追加の編集点 1: import 行）。"""
        offenders = [
            (node.lineno, node.module)
            for node in ast.walk(_main_tree())
            if isinstance(node, ast.ImportFrom)
            and (node.module or "").startswith("simulator.adapter.strategy")
        ]
        assert offenders == [], offenders

    def test_no_registry_builder_or_factory_is_defined_in_the_composition_root(self):
        """registry ビルダ・EA ファクトリの定義が本体に無いこと（編集点 2・3）。"""
        offenders = [
            node.name
            for node in _main_tree().body
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            and (
                node.name.startswith("_factory_")
                or node.name.startswith("_build_")
                and node.name.endswith("_registry")
            )
        ]
        assert offenders == [], offenders

    def test_the_composition_root_does_not_read_data_files(self):
        """CSV 読み（pandas）が本体に無いこと（EA ごとの入力形式を本体が知らない）。"""
        imported = {
            alias.name
            for node in ast.walk(_main_tree())
            if isinstance(node, ast.Import)
            for alias in node.names
        } | {
            node.module
            for node in ast.walk(_main_tree())
            if isinstance(node, ast.ImportFrom) and node.module
        }
        assert "pandas" not in imported, sorted(imported)


class TestAddingAnEaTouchesOneModuleAndOneLine:
    """1: 「EA 側モジュール 1 つ＋宣言 1 行」であることを構造で示す。"""

    def test_every_registered_ea_owns_exactly_one_module(self):
        """登録 EA の数と、宣言（各 EA モジュールの BINDING）を持つモジュールの数が対応すること。"""
        declaring = sorted(
            path.stem
            for path in _EA_BINDINGS_DIR.glob("*.py")
            if path.stem not in ("__init__", "binding", "sources")
        )
        # 登録 5 本＋既定 TC 経路＋データを読まない構成 = 宣言モジュール 7 本。
        assert len(declaring) == len(_EA_BINDINGS) + 2, declaring

    def test_the_registration_is_a_single_declaration_list(self):
        """登録が 1 つの宣言（ea_bindings の _EA_MODULES）に集まっていること。

        表そのもの（`_EA_BINDINGS`）は宣言からの**導出**であり、キーの文字列を書き写す
        場所は無い（EA 名は各 EA モジュールの `EaBinding.name` が唯一持つ）。
        """
        tree = ast.parse((_EA_BINDINGS_DIR / "__init__.py").read_text(encoding="utf-8"))
        assignments = [
            node
            for node in tree.body
            if isinstance(node, ast.Assign)
            and any(
                isinstance(t, ast.Name) and t.id == "_EA_MODULES" for t in node.targets
            )
        ]
        assert len(assignments) == 1
        # 宣言の中身はモジュール名だけ（EA 名の文字列も、ファクトリ関数も現れない）。
        assert all(
            isinstance(element, ast.Name) for element in assignments[0].value.elts
        )

    def test_each_binding_names_itself(self):
        """表のキーが宣言の `name` と一致すること（キーを写していない）。"""
        assert {key: binding.name for key, binding in _EA_BINDINGS.items()} == {
            key: key for key in _EA_BINDINGS
        }


class TestANewEaNeedsNoChangeToTheCompositionRoot:
    """OCP の**実証**: 宣言を 1 つ足すだけで列挙・選択・構築・配布が追随すること。

    構造の検定（上のクラス群）は「今の状態」を測る。本クラスは「拡張したときに何が
    起きるか」を実際に動かして測る——是正前は同じことをするのに
    `simulator/main/__init__.py` の 8 箇所を編集する必要があり、宣言だけでは EA が
    増えなかった（それが OCP 違反の実体である）。
    """

    @pytest.fixture()
    def probe(self, monkeypatch):
        """登録表へ宣言を 1 つ足す（＝新 EA モジュールを 1 本置いて 1 行登録した状態）。"""
        from simulator.main import ea_bindings
        from simulator.main.ea_bindings.binding import EaBinding, EaBuildContext

        seen: "list[object]" = []

        def _build(ctx: EaBuildContext):
            seen.append(ctx.param("ma_period"))
            return "probe-strategy", "probe-registry", "probe-market-data"

        binding = EaBinding(
            name="Probe_EA", build=_build, strategy_params=("entry_type",)
        )
        monkeypatch.setitem(ea_bindings._EA_BINDINGS, binding.name, binding)
        return binding, seen

    def test_the_new_ea_is_enumerated(self, probe):
        from simulator.main import known_ea_names

        binding, _seen = probe
        assert binding.name in known_ea_names()

    def test_the_new_ea_is_selected_by_its_name(self, probe):
        from simulator.main.ea_bindings import select_ea_binding

        binding, _seen = probe
        assert select_ea_binding(binding.name, tick_model="every_tick") is binding

    def test_the_new_ea_is_built_with_the_job_spec(self, probe):
        from simulator.main.ea_bindings import build_ea_components

        binding, seen = probe
        components = build_ea_components(
            binding.name,
            tick_model="every_tick",
            data_path=None,
            params={"ma_period": 7},
        )
        assert components == ("probe-strategy", "probe-registry", "probe-market-data")
        assert seen == [7]

    def test_the_declared_parameter_is_distributed_to_strategies(self, probe):
        from simulator.main.ea_bindings import strategy_param_names

        binding, _seen = probe
        assert set(binding.strategy_params) <= set(strategy_param_names())

    def test_a_newly_declared_parameter_joins_the_distributed_set(self, monkeypatch):
        """宣言に無かった名前を足すと、配る集合がそのぶんだけ伸びること（負の対照）。

        伸びなければ「宣言が飾りで、実際は別のどこかが名前を持っている」ことになる。
        """
        from simulator.main import ea_bindings
        from simulator.main.ea_bindings.binding import EaBinding
        from simulator.main.ea_bindings import strategy_param_names

        before = strategy_param_names()
        monkeypatch.setitem(
            ea_bindings._EA_BINDINGS,
            "Probe2_EA",
            EaBinding(
                name="Probe2_EA",
                build=lambda ctx: (None, None, None),
                strategy_params=("leverage",),  # 既存の宣言に無い build_interactor 引数
            ),
        )
        after = strategy_param_names()
        assert set(after) - set(before) == {"leverage"}

    def test_the_composition_root_file_is_not_involved(self, probe):
        """上の 5 件が通るあいだ、Composition Root は 1 文字も変わっていないこと。

        「宣言だけで増えた」を主張する以上、増やすために本体を触っていないことを
        同じテストの中で示す（触っていれば、この検定は本体の中身を見て赤になる）。
        """
        binding, _seen = probe
        tree = _main_tree()
        named = [
            node.lineno
            for node in ast.walk(tree)
            if isinstance(node, ast.Constant) and node.value == binding.name
        ]
        reads_table = [
            node.lineno
            for node in ast.walk(tree)
            if isinstance(node, ast.Name) and node.id == "_EA_BINDINGS"
        ]
        assert named == [], named
        assert reads_table == [], reads_table


class TestTheStrategyParametersComeFromTheDeclarations:
    """2: 戦略パラメータの名前が宣言側にあること。"""

    def test_the_composition_root_holds_no_strategy_param_dict_literal(self):
        """`build_interactor` 本体に戦略パラメータ名の dict リテラルが無いこと。

        是正前は 14 行の dict リテラルが本体に在り、EA が参照するパラメータを増やす
        たびにここを開いていた（EA 追加の 8 編集点のうち 1 つ）。
        """
        names = set(strategy_param_names())
        offenders = [
            (node.lineno, key.value)
            for node in ast.walk(_build_interactor_def())
            if isinstance(node, ast.Dict)
            for key in node.keys
            if isinstance(key, ast.Constant) and key.value in names
        ]
        assert offenders == [], offenders

    def test_the_distributed_set_is_the_union_of_the_declarations(self):
        """配る集合＝共通＋全宣言の和（過不足なし・重複なし）。"""
        declared = set(COMMON_STRATEGY_PARAMS)
        for binding in _EA_BINDINGS.values():
            declared |= set(binding.strategy_params)
        from simulator.main.ea_bindings import _DATALESS_BINDING, _DEFAULT_BINDING

        declared |= set(_DEFAULT_BINDING.strategy_params)
        declared |= set(_DATALESS_BINDING.strategy_params)
        got = strategy_param_names()
        assert set(got) == declared
        assert len(got) == len(set(got)), got

    def test_every_declared_parameter_is_a_build_interactor_argument(self):
        """宣言した名前が投入 API（公開シグネチャ）に実在すること。

        宣言が実在しない名前を挙げると、`build_interactor` は `KeyError` で落ちる。
        その失敗は run の実行時ではなく**宣言を書いた時点**で分かるべきである。
        """
        parameters = set(inspect.signature(build_interactor).parameters)
        missing = [name for name in strategy_param_names() if name not in parameters]
        assert missing == [], missing

    def test_the_distributed_set_is_unchanged_from_before_the_split(self):
        """配る 14 名が是正前の dict リテラルと**並びまで**同一であること（byte 等価ピン）。

        並びを固定するのは、run-config が dict を写して保持するためである（順序が
        観測に出る経路が将来生まれても、ここで気づける）。
        """
        assert strategy_param_names() == (
            "lot_size",
            "stop_loss_points",
            "take_profit_points",
            "point_size",
            "slope_shift",
            "slope_min_points",
            "volume_min",
            "volume_max",
            "volume_step",
            "digits",
            "stops_level",
            "entry_offset_points",
            "entry_type",
            "adx_min",
        )


class TestThePublicSignatureIsUntouched:
    """3: 事実上の HTTP スキーマ（39 引数）が 1 文字も動いていないこと。

    引数の本数は、ISSUE-508 段階 3 で末尾へ `run_tracer` を足した時点で 38 → 39 に
    なった（設計書 §6.6.3 が指示する仕様変更への追随であり、既存 38 名の**並びは
    1 文字も動いていない**）。
    """

    #: 是正前の実測（`inspect.signature(build_interactor).parameters` の並び）。
    _EXPECTED = (
        "data_path", "symbol", "period", "ea_name", "initial_deposit", "contract_size",
        "volume_min", "volume_max", "volume_step", "stops_level", "digits", "point_size",
        "leverage", "ma_period", "ma_method", "lot_size", "stop_loss_points",
        "take_profit_points", "config_overrides", "stop_out_level", "slope_shift",
        "slope_min_points", "entry_offset_points", "entry_type", "trading_start",
        "tick_store_root", "tick_start", "tick_end", "weekly_forecast", "weekly_p_tp",
        "weekly_capital", "weekly_f_risk", "adx_min", "adx_period", "marketdata_window",
        "strategy_decorator", "strategy_override", "position_manager",
        # ISSUE-508 段階 3（RUN_TRACE_BASIC_DESIGN §6.6.3・是正 F-6）で末尾へ追加。
        #   仕様変更への追随であり、既存 38 名の並びは 1 文字も動いていない。
        "run_tracer",
    )
    #: 既定値を持たない引数＝反射側の「必須キー」集合（是正前の実測）。
    _EXPECTED_REQUIRED = frozenset(_EXPECTED[:18])

    def test_the_parameter_names_and_order_are_unchanged(self):
        assert tuple(inspect.signature(build_interactor).parameters) == self._EXPECTED

    def test_the_required_key_set_is_unchanged(self):
        parameters = inspect.signature(build_interactor).parameters
        required = frozenset(
            name
            for name, parameter in parameters.items()
            if parameter.default is inspect.Parameter.empty
        )
        assert required == self._EXPECTED_REQUIRED

    def test_every_parameter_is_keyword_only(self):
        assert all(
            parameter.kind is inspect.Parameter.KEYWORD_ONLY
            for parameter in inspect.signature(build_interactor).parameters.values()
        )

    @pytest.mark.parametrize(
        "derive",
        [
            "simulator.sim_ui.main.composition_root_jobs:allowed_backtest_keys",
            "simulator.sim_ui.main.composition_root_jobs:required_backtest_keys",
        ],
    )
    def test_the_reflecting_modules_still_derive_the_same_sets(self, derive):
        """反射する本番モジュールが導く許容／必須集合が是正前と一致すること。

        投入 API の実体はこの 2 集合である（sim_ui の受付検証がここで弾く）。
        シグネチャを触っていなくても、束の作り方を誤れば形が変わりうるので独立に測る。
        """
        import importlib

        module_name, function_name = derive.split(":")
        function = getattr(importlib.import_module(module_name), function_name)
        injected_only = {"strategy_decorator", "strategy_override", "run_tracer"}
        expected = (
            set(self._EXPECTED)
            if function_name == "allowed_backtest_keys"
            else set(self._EXPECTED_REQUIRED)
        ) - injected_only
        assert function() == frozenset(expected)

    def test_the_settings_mapper_derives_the_same_sets(self):
        from simulator.main.tester_settings.kwargs_mapper import interactor_key_sets

        allowed, required = interactor_key_sets()
        assert allowed == frozenset(self._EXPECTED)
        assert required == self._EXPECTED_REQUIRED

    @pytest.mark.parametrize(
        "param,expected",
        [
            ("ma_period", int),
            ("ma_method", str),
            ("lot_size", float),
            ("stop_loss_points", float),
        ],
    )
    def test_the_ini_binding_still_resolves_the_same_scalar_types(self, param, expected):
        """`.ini` 入力束縛が引く**型注釈**が動いていないこと。

        `ea_input_map` は ``inspect.signature(..., eval_str=True)`` で注釈を型オブジェクト
        として読み、そこから文字列→値の変換器を選ぶ。注釈が 1 つでも変われば投入 `.ini` の
        解釈が変わる（数値が文字列のまま渡る等）。許容/必須キーの集合だけでは捉えられない
        面なので、独立に測る。
        """
        from simulator.main.tester_settings.ea_input_map import scalar_converter_for

        converter = scalar_converter_for(param)
        sample = {int: "7", float: "1.5", str: "ema"}[expected]
        assert isinstance(converter(sample), expected)


class TestTheDeclarationDrivenRootDoesNotIssueExtraWork:
    """計算量（プロジェクト絶対命令 2026-08-28）: 宣言駆動化で発行が増えないこと。

    測るのは時間ではなく回数。宣言から導く形は「毎回 表を畳み直す」形になりやすく、
    しかも出力は 1 ビットも変わらないので状態検証では落ちない。
    """

    @staticmethod
    def _kwargs(tmp_path, ea_name="MA_Slope_EA"):
        from simulator.tests.unit.test_ea_factory_registry import (
            _mt5_kwargs,
            _write_mt5_csv,
        )

        return _mt5_kwargs(_write_mt5_csv(tmp_path / "mt5.csv"), ea_name)

    def test_the_declarations_are_folded_once_per_build_not_once_per_parameter(
        self, tmp_path, monkeypatch
    ):
        """宣言の畳み込み（名前の和の導出）が 1 回の構築につき 1 回であること。

        発行（畳み込み）− 使用（組んだ戦略パラメータ dict 1 つ）= 0。
        """
        import simulator.main as sim_main
        from simulator.main import ea_bindings

        folds: "list[int]" = []
        original = ea_bindings.strategy_param_names
        monkeypatch.setattr(
            sim_main, "strategy_param_names",
            lambda: (folds.append(1), original())[1],
        )
        _controller, request = sim_main.build_interactor(**self._kwargs(tmp_path))
        used = 1 if request.config.strategy_params else 0
        assert len(folds) - used == 0, folds

    def test_the_binding_is_selected_once_per_build_not_once_per_parameter(
        self, tmp_path, monkeypatch
    ):
        """束縛の選択と構築が 1 回の構築につき 1 回であること（EA ごとに引き直さない）。"""
        from simulator.main import ea_bindings

        selected: "list[str]" = []
        original = ea_bindings.select_ea_binding
        monkeypatch.setattr(
            ea_bindings, "select_ea_binding",
            lambda ea_name, **kw: (selected.append(ea_name), original(ea_name, **kw))[1],
        )
        import simulator.main as sim_main

        _controller, request = sim_main.build_interactor(**self._kwargs(tmp_path))
        # 使用（run に載った戦略・registry・market_data の 3 点組 1 つ）に対し発行 1。
        assert len(selected) - 1 == 0, selected
        assert len(list(request.bars)) > 0

    def test_the_fold_count_does_not_grow_with_the_number_of_bars(
        self, tmp_path, monkeypatch
    ):
        """バー 15 本 / 45 本の 2 点で畳み込み発行が変わらないこと（オーダーの表明）。

        上限が 60 行なのは合成 MT5 CSV の時刻列が分単位で作られているためである
        （`_write_mt5_csv`）。3 倍差があればオーダーの表明には足りる。
        """
        import simulator.main as sim_main
        from simulator.main import ea_bindings
        from simulator.tests.unit.test_ea_factory_registry import (
            _mt5_kwargs,
            _write_mt5_csv,
        )

        original = ea_bindings.strategy_param_names
        measured = {}
        for bar_count in (15, 45):
            folds: "list[int]" = []
            monkeypatch.setattr(
                sim_main, "strategy_param_names",
                lambda: (folds.append(1), original())[1],
            )
            csv = _write_mt5_csv(tmp_path / f"mt5_{bar_count}.csv", n=bar_count)
            _c, request = sim_main.build_interactor(
                **_mt5_kwargs(csv, "MA_Slope_EA")
            )
            measured[bar_count] = (len(folds), len(list(request.bars)))
        assert measured[45][0] - measured[15][0] == 0, measured
        # 負の対照: バー数そのものは実際に増えている（測定が空振りしていない）。
        assert measured[45][1] - measured[15][1] > 0, measured


class TestTheArgumentBundleMatchesTheSignature:
    """4: `dict(locals())` が仮引数だけを捉えていること。"""

    def test_the_bundle_is_taken_as_the_first_executable_statement(self):
        """束を取る文が docstring の直後（最初の実行文）であること。

        1 文でも前に局所変数が入ると、束に仮引数以外が混ざる。位置は目視規約にせず
        構文木で固定する。
        """
        body = _build_interactor_def().body
        statements = [
            node
            for node in body
            if not (isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant))
        ]
        first = statements[0]
        assert isinstance(first, ast.Assign)
        assert [t.id for t in first.targets] == ["job"]
        assert isinstance(first.value, ast.Call)
        assert isinstance(first.value.func, ast.Name) and first.value.func.id == "dict"
        inner = first.value.args[0]
        assert isinstance(inner, ast.Call)
        assert isinstance(inner.func, ast.Name) and inner.func.id == "locals"

    def test_the_bundle_carries_every_signature_parameter(self):
        """束のキー集合が公開シグネチャの引数集合と一致すること（実測）。

        `build_interactor` を実際に呼び、束から引かれる戦略パラメータが全部揃うことを
        観測できる形（`request.config` の subscript）で確かめる。
        """
        from simulator.tests.unit.test_ea_factory_registry import (
            _mt5_kwargs,
            _write_mt5_csv,
        )

        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            csv = _write_mt5_csv(Path(tmp) / "mt5.csv")
            kwargs = _mt5_kwargs(csv, "MA_Slope_EA")
            _controller, request = build_interactor(**kwargs)
            # 欠落なら KeyError（run-config は黙殺しない）。値も投入と一致することまで見る
            # ——束が「名前は揃うが値がずれる」形（例: 位置ずれで別の局所変数を拾う）を
            # 名前の有無だけでは捉えられない。
            resolved = {name: request.config[name] for name in strategy_param_names()}
            assert set(resolved) == set(strategy_param_names())
            supplied = {
                name: value for name, value in kwargs.items() if name in resolved
            }
            assert {name: resolved[name] for name in supplied} == supplied
