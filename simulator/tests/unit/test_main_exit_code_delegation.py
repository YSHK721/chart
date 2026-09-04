"""`main` 層に終了コード表の**複製**が残っていないことを構文木で固定するゲート（A-6 後半）。

固定する仕様:
    1. `simulator/main` 配下の本番モジュールにおいて、**例外ハンドラ（`except` 節）の
       本体から整数リテラルを `return` しない**。例外を終了コードへ写す行為は
       `simulator.adapter.exit_codes`（唯一の宣言場所）の責務であり、ハンドラが
       `return 2` と書いた時点でその表が 2 箇所に分かれる。
    2. 終了コードを返す 2 つの入口（`simulator.main.run_backtest` /
       `simulator.main.__main__.main`）は共有の翻訳（`exit_code_for`）を呼ぶ。
    3. `simulator/` 配下の**本番**モジュール（`tests` 配下と定義元 `adapter/controller.py`
       を除く）は `BacktestController` の非公開属性 `_interactor` へ到達しない
       （公開の実行点 `execute()` / 取得点 `interactor` を使う）。
       ISSUE-398 で射程を `simulator/main` → `simulator/` へ広げた: `main` だけを見る
       検査は `tools/` と `report_ui/` に残った本番 3 件を構造的に見逃していた。

なぜ「except 節の中の整数リテラル return」で測るか（列挙にしない理由）:
    対象関数名を列挙する検査は、列挙に載っていない新規モジュールを永久に見逃す
    （`test_layer_dependency_direction.py` の冒頭が同じ理由を述べる）。一方で
    「`main` 層のあらゆる整数 return を禁止する」まで広げると、終了コードと無関係な
    関数（件数を返す等）まで巻き込む。「例外を捕捉した文脈で整数を返す」ことは
    **例外 → 終了コードの写像そのもの**であり、複製と 1:1 で対応する述語である。

なぜ AST で測るか:
    「値が等しいこと」（`2 == exit_code_for(ConfigError("x"))`）は複製が 2 箇所あっても
    成立するため、複製の不在を検出できない。リテラルの所在は構文木にしか現れない。
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

#: `simulator` パッケージ本体。
_SIMULATOR_DIR = Path(__file__).resolve().parents[2]

#: 走査対象（Composition Root）。
_MAIN_DIR = _SIMULATOR_DIR / "main"

#: 終了コードを返す入口（`exit_code_for` への委譲を固定する対象）。
_ENTRY_POINTS = (
    (_MAIN_DIR / "__init__.py", "run_backtest"),
    (_MAIN_DIR / "__main__.py", "main"),
)


def _main_layer_modules() -> "list[Path]":
    """`simulator/main` 配下の全 `.py`（`__pycache__` を除く）。"""
    return sorted(
        path for path in _MAIN_DIR.rglob("*.py") if "__pycache__" not in path.parts
    )


#: `_interactor` の**定義元**。自分の属性を持つのは当然であり検査対象から外す。
_INTERACTOR_OWNER = _SIMULATOR_DIR / "adapter" / "controller.py"


def _production_modules() -> "list[Path]":
    """`simulator/` 配下の**本番**モジュール（`tests` 配下と定義元を除く）。

    ISSUE-398 で射程を `simulator/main` → `simulator/` へ広げた理由（実測）:
        A-5（ISSUE-395）は `main` 層の非公開到達だけを塞いだが、カプセル化の破れは
        消えていなかった。`main` の外に本番 3 件——`tools/run_is_oos_cli.py` /
        `tools/export_trade_markers.py` / `report_ui/tools/export_report_payload.py`
        ——が `controller._interactor.execute(...)` で到達したままだったためである。
        `main` だけを見る検査は、この 3 件を構造的に見逃す（射程の穴）。

    除外の根拠:
        - `tests` 配下: 検定は構成の内部（`_strategy` / `_indicators` 等）を測ることが
          仕事であり、本ゲートの対象ではない。
        - `adapter/controller.py`: `self._interactor` を保持する定義元。ここを含めると
          ゲートは定義そのものを違反と呼ぶ（常時赤＝検出力ゼロ）。
    """
    return sorted(
        path
        for path in _SIMULATOR_DIR.rglob("*.py")
        if "__pycache__" not in path.parts
        and "tests" not in path.parts
        and path != _INTERACTOR_OWNER
    )


def _tree(path: Path) -> ast.Module:
    return ast.parse(path.read_text(encoding="utf-8"), filename=str(path))


def _returned_int_constants(node: ast.AST) -> "list[int]":
    """`node` 配下の `return` が返す整数リテラルを列挙する。

    `return 2` と `return 2, None`（タプルの要素）の双方を拾う。`bool` は `int` の
    サブクラスだが終了コードではないため除外する。
    """
    found: list[int] = []
    for child in ast.walk(node):
        if not isinstance(child, ast.Return) or child.value is None:
            continue
        candidates = (
            list(child.value.elts)
            if isinstance(child.value, ast.Tuple)
            else [child.value]
        )
        found.extend(
            value.value
            for value in candidates
            if isinstance(value, ast.Constant)
            and isinstance(value.value, int)
            and not isinstance(value.value, bool)
        )
    return found


def _literal_returning_handlers(tree: ast.Module) -> "list[tuple[int, list[int]]]":
    """整数リテラルを `return` する `except` 節を (行番号, 値) で列挙する。"""
    violations: list[tuple[int, list[int]]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.ExceptHandler):
            continue
        literals = [
            literal for stmt in node.body for literal in _returned_int_constants(stmt)
        ]
        if literals:
            violations.append((node.lineno, literals))
    return violations


def _function(path: Path, name: str) -> ast.FunctionDef:
    for node in ast.walk(_tree(path)):
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return node
    raise AssertionError(f"{path.name}:{name} が見つかりません")


def _called_names(node: ast.AST) -> "set[str]":
    return {
        child.func.id
        for child in ast.walk(node)
        if isinstance(child, ast.Call) and isinstance(child.func, ast.Name)
    }


def _accessed_attributes(tree: ast.Module) -> "set[str]":
    return {
        node.attr for node in ast.walk(tree) if isinstance(node, ast.Attribute)
    }


class TestTheMainLayerKeepsNoExitCodeTable:
    """`main` 層の例外ハンドラが終了コードを直書きしていないこと。"""

    def test_no_except_handler_returns_an_integer_literal(self):
        violations = [
            f"{path.relative_to(_SIMULATOR_DIR.parent)}:{lineno}: return {literals}"
            for path in _main_layer_modules()
            for lineno, literals in _literal_returning_handlers(_tree(path))
        ]
        assert violations == [], (
            "main 層に終了コード表の複製（例外ハンドラ内の整数リテラル return）: "
            + "; ".join(violations)
        )

    @pytest.mark.parametrize(
        "path, function",
        _ENTRY_POINTS,
        ids=[f"{path.name}:{name}" for path, name in _ENTRY_POINTS],
    )
    def test_the_entry_point_delegates_to_the_shared_translation(self, path, function):
        assert "exit_code_for" in _called_names(_function(path, function))


class TestProductionCodeUsesThePublicInteractorAccess:
    """本番コードが `BacktestController` の非公開属性へ到達しないこと（ISSUE-398）。

    射程は `simulator/` 全体（`tests` 配下と定義元 `adapter/controller.py` を除く）。
    公開の到達点は `execute()`（実行）と `interactor`（取得）である。
    """

    def test_no_production_module_reaches_the_private_interactor_attribute(self):
        violations = [
            str(path.relative_to(_SIMULATOR_DIR.parent))
            for path in _production_modules()
            if "_interactor" in _accessed_attributes(_tree(path))
        ]
        assert violations == [], (
            "本番コードから BacktestController の非公開属性 _interactor へ到達: "
            + "; ".join(violations)
        )

    def test_run_backtest_executes_through_the_public_method(self):
        """`run_backtest` は公開の実行点 `controller.execute` を呼ぶ。

        従来は `controller.run(...)` ＋ `controller.interactor.last_result` だった。
        `execute` は結果を返すため `last_result` の読み出しが不要になる（ISSUE-398）。
        """
        tree = ast.Module(body=[_function(*_ENTRY_POINTS[0])], type_ignores=[])
        accessed = _accessed_attributes(tree)
        assert "execute" in accessed
        # 二重ロード（`controller.run` は market_data を読み直す）へ戻っていないこと。
        assert "run" not in accessed
        # 結果は execute の戻り値で受ける（`last_result` を読み直さない）。
        assert "last_result" not in accessed


class TestTheOutputStageCannotRaiseConfigError:
    """出力段の委譲が挙動を変えないための前提を機械的に固定する（A-6 後半）。

    **固定対象の不変条件（不変）**: `_present_outputs` から `ConfigError` が出ないこと。
    `run_backtest` の出力段は従来 `except BacktestError: return 1, result` と
    終了コードを直書きしていた。これを `exit_code_for(error)` へ委譲すると、
    もし `_present_outputs` から `ConfigError` が出れば 1 ではなく 2 になる。
    委譲が観測挙動を変えないことは「その入力が到達し得ない」ことに依存するため、
    依存している不変条件そのものをここで固定する（一度の実測では腐る）。

    実測（baseline 47e869e）: `_present_outputs` が呼ぶプロジェクト内コードは
    `JsonPresenter.present_json` / `MarkdownPresenter.present_markdown` の 2 つで、
    そこから到達する `simulator.*` モジュールは 7 件。閉包内の `raise` は 28 件、
    送出型は `NotImplementedError` 19 / `InvalidPriceError` 6 / `OHLCInvalidError` 2 /
    `ExecutionError` 1 であり、`ConfigError` は 0 件。残る呼出（`Path.mkdir` /
    `write_text` / `setattr`）は標準ライブラリで、`simulator` 配下に `__setattr__` の
    再定義は 0 件（実測）。

    **閉包の TYPE_CHECKING 誤認の是正（ISSUE-411・2026-08-18）**:
    本検査は長らく閉包を過大に計算していた。`_project_imports` が
    ``if TYPE_CHECKING:`` ガード配下の import を実行時 import と同一視していたためである。
    `usecase/ports.py:14-16` は `Bar` / `Order` を型注釈専用に import しており
    （``if TYPE_CHECKING:  # 型注釈専用（実行時 import 不要・domain のみ）``）、
    これが閉包へ混入して `domain/bar.py` `domain/order.py` `domain/exceptions.py`
    `domain/_shared.py` `domain/bar_time.py` を引き込み、閉包は **8 モジュール**に膨れていた。

    除外の正当性は**言語仕様**である。`typing.TYPE_CHECKING` は実行時 ``False`` であり
    （実測: ``python -c "import typing; print(typing.TYPE_CHECKING)"`` → ``False``）、
    ガード配下の文は実行時に評価されない。したがってガード配下の import は
    「出力段が実行時に到達するコード」ではなく、閉包に含めてはならない。

    是正後の閉包は **3 モジュール**（`presenter/json.py` / `presenter/markdown.py` /
    `usecase/ports.py`）であり、実行時の実測と一致する（`import` 後の ``sys.modules``
    に `simulator.domain.bar` は現れない）。この閉包の `ConfigError` raise は **0 件**である。

    なお ISSUE-411 で `Bar.__post_init__` へ time 型契約の表明（違反は `ConfigError`）を
    入れた際、本検査が落ちた。当初これを「近似が過大だから許可リストで通す」方向で
    扱いかけたが、**真因は閉包計算のバグ**であり、`domain/bar.py` はそもそも出力段の
    実行時閉包に存在しない。無条件検査（閉包内に `ConfigError` の raise 0 件）のまま
    正しい閉包を与えるのが抜本策である。
    """

    #: `_present_outputs` が呼ぶプロジェクト内コードの入口。
    _PRESENTER_SEEDS = (
        _SIMULATOR_DIR / "adapter" / "presenter" / "json.py",
        _SIMULATOR_DIR / "adapter" / "presenter" / "markdown.py",
    )

    #: 是正後の実行時閉包（実測値）。膨張・縮小のいずれもここで気付ける。
    _RUNTIME_CLOSURE = (
        _SIMULATOR_DIR / "adapter" / "presenter" / "json.py",
        _SIMULATOR_DIR / "adapter" / "presenter" / "markdown.py",
        _SIMULATOR_DIR / "usecase" / "ports.py",
    )

    def _module_path(self, module_name: str) -> "Path | None":
        parts = module_name.split(".")
        if parts[0] != "simulator":
            return None
        base = _SIMULATOR_DIR.parent.joinpath(*parts)
        for candidate in (base.with_suffix(".py"), base / "__init__.py"):
            if candidate.is_file():
                return candidate
        return None

    @staticmethod
    def _is_type_checking_guard(node: ast.AST) -> bool:
        """``if TYPE_CHECKING:`` / ``if typing.TYPE_CHECKING:`` か。"""
        if not isinstance(node, ast.If):
            return False
        test = node.test
        return (isinstance(test, ast.Name) and test.id == "TYPE_CHECKING") or (
            isinstance(test, ast.Attribute) and test.attr == "TYPE_CHECKING"
        )

    def _project_imports(self, path: Path) -> "set[str]":
        """実行時に評価される `simulator.*` の import を列挙する。

        ``if TYPE_CHECKING:`` ガード配下の import は**除外する**。除外の正当性は
        言語仕様である: `typing.TYPE_CHECKING` は実行時 ``False`` であり、ガード配下の
        文は実行時に評価されない（型検査器だけが真とみなす）。除外しないと型注釈専用の
        import が「出力段が実行時に到達するコード」として扱われ、閉包が過大になる
        （ISSUE-411: `usecase/ports.py` の型注釈専用 import が `domain/*` 5 件を
        引き込み、閉包が 3 → 8 モジュールへ膨れていた）。

        除外するのは **`body`（真側）のみ**。`else:` 節（`orelse`）は `TYPE_CHECKING`
        が実行時 ``False`` であるため**必ず実行される**——`ast.walk(if_node)` で
        If ノード全体を除外すると `else:` 側の実行時 import まで閉包から落ち、
        ゲートが無音で通る（再レビュー 🟡-新-1 の実測: `else:` 節へ実行時 import を
        注入した変異が素通りした）。
        """
        tree = _tree(path)
        guarded = {
            id(sub)
            for node in ast.walk(tree)
            if self._is_type_checking_guard(node)
            for stmt in node.body
            for sub in ast.walk(stmt)
        }
        modules: set[str] = set()
        for node in ast.walk(tree):
            if id(node) in guarded:
                continue
            if isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
                modules.add(node.module)
            elif isinstance(node, ast.Import):
                modules.update(alias.name for alias in node.names)
        return {m for m in modules if m.split(".")[0] == "simulator"}

    def _closure(self) -> "set[Path]":
        seen = set(self._PRESENTER_SEEDS)
        queue = list(self._PRESENTER_SEEDS)
        while queue:
            for module_name in self._project_imports(queue.pop()):
                path = self._module_path(module_name)
                if path is not None and path not in seen:
                    seen.add(path)
                    queue.append(path)
        return seen

    def _raised_type_names(self, path: Path) -> "list[str]":
        names = []
        for node in ast.walk(_tree(path)):
            if isinstance(node, ast.Raise) and node.exc is not None:
                exc = node.exc
                names.append(
                    ast.unparse(exc.func)
                    if isinstance(exc, ast.Call)
                    else ast.unparse(exc)
                )
        return names

    def test_the_presenter_closure_never_raises_config_error(self):
        violations = [
            f"{path.relative_to(_SIMULATOR_DIR.parent)}: {name}"
            for path in sorted(self._closure())
            for name in self._raised_type_names(path)
            if "ConfigError" in name
        ]
        assert violations == [], (
            "出力段から ConfigError が到達し得る（run_backtest 出力段の委譲が "
            "exit 1 → exit 2 の挙動変化を起こす）: " + "; ".join(violations)
        )

    def test_the_closure_is_exactly_the_runtime_reachable_modules(self):
        """閉包が実行時到達集合と一致する（TYPE_CHECKING 誤認の再発検出）。

        識別力: `_project_imports` の TYPE_CHECKING 除外を外すと閉包が 8 件へ膨れ、
        本検定が落ちる。逆に seed が失われて閉包が縮んでも落ちる。
        期待値は実行時の実測（`presenter` を import した後の `sys.modules`）と一致する。
        """
        assert self._closure() == set(self._RUNTIME_CLOSURE)

    def test_the_closure_is_not_empty(self):
        # 閉包が空なら上の検査は常に通る（＝ゲートとして無意味）。
        closure = self._closure()
        assert set(self._PRESENTER_SEEDS) <= closure
        assert _SIMULATOR_DIR / "usecase" / "ports.py" in closure

    def test_an_else_branch_of_a_type_checking_guard_stays_in_the_closure(self, tmp_path):
        """`if TYPE_CHECKING: ... else: <実行時 import>` の else 側は閉包に**残る**。

        `TYPE_CHECKING` は実行時 False なので `else:` 節は必ず実行される。除外集合が
        If ノード全体（`ast.walk(if_node)`）だと else 側の実行時 import まで落ち、
        ゲートが無音化する（再レビュー 🟡-新-1 で変異が素通りした実測あり）。
        本検定はその再発を固定する。
        """
        probe = tmp_path / "probe.py"
        probe.write_text(
            "from typing import TYPE_CHECKING\n"
            "if TYPE_CHECKING:\n"
            "    from simulator.domain.bar import Bar\n"
            "else:\n"
            "    from simulator.usecase.run_backtest import RunBacktestRequest\n",
            encoding="utf-8",
        )
        imports = self._project_imports(probe)
        assert "simulator.usecase.run_backtest" in imports  # else 側＝実行時に走る
        assert "simulator.domain.bar" not in imports  # body 側＝型検査専用

    def test_no_module_redefines_setattr(self):
        # `setattr(result, ...)` が ConfigError を出す経路を塞ぐ。
        violations = [
            str(path.relative_to(_SIMULATOR_DIR.parent))
            for path in _SIMULATOR_DIR.rglob("*.py")
            if "__pycache__" not in path.parts
            and any(
                isinstance(node, ast.FunctionDef) and node.name == "__setattr__"
                for node in ast.walk(_tree(path))
            )
        ]
        assert violations == []


class TestTheGateHasDetectionPower:
    """ゲート自身が「検出できる状態」にあること（空振りしていないこと）。"""

    def test_the_gate_actually_scans_the_main_layer(self):
        scanned = _main_layer_modules()
        assert len(scanned) > 0
        # 対象の 2 入口が走査対象に含まれること（走査漏れで常時緑になるのを防ぐ）。
        for path, _name in _ENTRY_POINTS:
            assert path in scanned

    def test_a_bare_literal_return_in_a_handler_is_detected(self):
        source = "def f():\n    try:\n        g()\n    except ValueError:\n        return 2\n"
        assert _literal_returning_handlers(ast.parse(source)) == [(4, [2])]

    def test_a_tuple_literal_return_in_a_handler_is_detected(self):
        source = (
            "def f():\n    try:\n        g()\n    except ValueError:\n"
            "        return 1, None\n"
        )
        assert _literal_returning_handlers(ast.parse(source)) == [(4, [1])]

    def test_a_delegating_handler_is_not_a_false_positive(self):
        source = (
            "def f():\n    try:\n        g()\n    except ValueError as e:\n"
            "        return exit_code_for(e)\n"
        )
        assert _literal_returning_handlers(ast.parse(source)) == []

    def test_an_integer_return_outside_a_handler_is_not_a_false_positive(self):
        # 終了コードと無関係な整数 return（件数等）は対象外である。
        assert _literal_returning_handlers(ast.parse("def n():\n    return 0\n")) == []

    def test_a_boolean_return_is_not_treated_as_an_exit_code(self):
        source = "def f():\n    try:\n        g()\n    except ValueError:\n        return False\n"
        assert _literal_returning_handlers(ast.parse(source)) == []

    def test_a_type_checking_only_import_is_excluded_from_the_closure(self, tmp_path):
        """閉包計算が `if TYPE_CHECKING:` 配下の import を実行時 import と混同しない。

        識別力: 除外判定を外すと本検定が落ちる。`usecase/ports.py` の型注釈専用 import が
        `domain/*` を閉包へ引き込んでいた欠陥（ISSUE-411）の再発検出点である。
        """
        gate = TestTheOutputStageCannotRaiseConfigError()
        module = tmp_path / "guarded.py"
        module.write_text(
            "from typing import TYPE_CHECKING\n"
            "import simulator.usecase.ports\n"
            "if TYPE_CHECKING:\n"
            "    from simulator.domain.bar import Bar\n",
            encoding="utf-8",
        )
        imported = gate._project_imports(module)
        assert "simulator.usecase.ports" in imported  # 実行時 import は残る
        assert "simulator.domain.bar" not in imported  # 型注釈専用は除外される

    def test_a_typing_qualified_type_checking_guard_is_also_excluded(self, tmp_path):
        """`if typing.TYPE_CHECKING:`（属性形式）も除外する。"""
        gate = TestTheOutputStageCannotRaiseConfigError()
        module = tmp_path / "guarded_attr.py"
        module.write_text(
            "import typing\n"
            "if typing.TYPE_CHECKING:\n"
            "    from simulator.domain.bar import Bar\n",
            encoding="utf-8",
        )
        assert gate._project_imports(module) == set()

    def test_the_private_attribute_probe_detects_the_violation(self):
        assert "_interactor" in _accessed_attributes(
            ast.parse("x = controller._interactor.last_result\n")
        )

    def test_the_extended_scope_covers_the_sites_the_main_only_scope_missed(self):
        """射程拡張が実際に「見逃していた 3 件」を捕捉範囲に入れたこと（ISSUE-398）。

        `simulator/main` だけを見る旧射程では、この 3 件は永久に検出されなかった。
        パスが将来動いたらこの検定が落ち、射程の穴が再発する前に気付ける。
        """
        previously_missed = [
            _SIMULATOR_DIR / "tools" / "run_is_oos_cli.py",
            _SIMULATOR_DIR / "tools" / "export_trade_markers.py",
            _SIMULATOR_DIR / "report_ui" / "tools" / "export_report_payload.py",
        ]
        scanned = set(_production_modules())
        missing = [str(p) for p in previously_missed if p not in scanned]
        assert missing == [], f"射程外に落ちている: {missing}"
        # 旧射程（main 配下）では 1 件も捕捉できなかったことを同時に固定する。
        assert not any(p in set(_main_layer_modules()) for p in previously_missed)

    def test_the_definition_site_is_excluded_from_the_scope(self):
        # 定義元を含めるとゲートは常時赤（検出力ゼロ）になる。
        assert _INTERACTOR_OWNER.is_file()
        assert "_interactor" in _accessed_attributes(_tree(_INTERACTOR_OWNER))
        assert _INTERACTOR_OWNER not in set(_production_modules())

    def test_tests_are_excluded_from_the_scope(self):
        scanned = _production_modules()
        assert scanned, "本番モジュールの走査結果が空（ゲートとして無意味）"
        assert not [p for p in scanned if "tests" in p.parts]
        # 本ファイル自身（tests 配下）が対象外であること。
        assert Path(__file__).resolve() not in set(scanned)
