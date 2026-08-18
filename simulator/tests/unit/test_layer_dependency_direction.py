"""内側 4 層が `simulator.main` を import しないことを構文木で固定するゲート。

固定する仕様（依存の向き）:
    `simulator` パッケージ内に存在する**すべてのスライス**（エンジン本体・`sim_ui`・
    `replay_ui`・`report_ui`）の内側 4 層（`adapter` / `usecase` / `domain` /
    `framework`）は `simulator.main` を import しない。`simulator.main` は
    Composition Root（最も外側）であり、内側から外側への依存は依存方向の反転
    そのものである。`adapter/controller.py` の docstring が「adapter 層は usecase +
    domain のみに依存する（framework / main は import しない）」と宣言しており、
    本ゲートはその宣言をリポジトリ全体へ機械的に拡張する。

なぜ「走査対象の列挙」を持たないか（ISSUE-405 の構造的原因）:
    既存の同種検査は走査対象を固定パスの列挙で持つ——
    `test_is_oos_dependency_direction.py`（`usecase/run_is_oos.py` の 1 件）・
    `test_optimize_dependency.py`（`usecase/optimize*.py` の 3 件）・
    `test_weekly_vol_band_dependency_direction.py`（`usecase` の 3 件）。
    列挙に載っていない新規モジュールは、違反しても永久に検出されない。
    本ゲート自身も当初は `_SIMULATOR_DIR/{adapter,usecase,domain,framework}` という
    スライス 1 個ぶんの列挙で走査対象を持っており、`sim_ui/adapter` は最初から対象外
    だった。ISSUE-405（`sim_ui/adapter` の 3 ファイルが `simulator.main` の私有名を
    越境 import していた）が長期間検出されなかったのはこの射程欠落が原因である
    （実測: 拡張前のゲートに 5 形態の違反を `sim_ui/adapter` へ注入しても 12 passed）。
    そこで走査対象は「名前の表」ではなく**構造**から導く——`_INNER_LAYER_NAMES` の
    いずれかの名前を持ち、かつ Python パッケージである（`__init__.py` を持つ）
    ディレクトリはすべて内側層とみなす。スライスが増えても本ファイルの分岐は
    書き換わらない（OCP）。`web/js/adapter` 等の非 Python ディレクトリは
    `__init__.py` を持たないので自動的に外れる。

なぜ構文木で測るか:
    import 実行時の副作用（`sys.modules` 観測）では、条件分岐の下に隠れた import や
    実行されなかった分岐を取りこぼす。import 文の所在は構文木にしか現れないので、
    構文木を数える。

検出する 5 形態（`TestTheGateHasDetectionPower` が実コードで固定する）:
    1. `import simulator.main` / `import simulator.main.x as y`
    2. `from simulator.main import x` / `from simulator.main.x import y`
    3. 相対 import（`from ...main import x` 等）を絶対名へ解決したもの
    4. `from simulator import main as sim_main`（`from <親> import <サブモジュール>`）
       ——モジュール名は `simulator` にしか現れないので、`node.module` だけを見る
       実装はこの形を取り逃す。ISSUE-405 の `ea_stop_loss_param_catalog.py` が実際に
       使っていた形式である。
    5. `importlib.import_module("simulator.main...")`（文字列リテラル引数のみ）

    `getattr(sim_main, "_EA_FACTORIES", {})` の文字列形式（ISSUE-405 が現行 AST 検査を
    すり抜けた実績のある形）は、それ自体は import ではない。getattr するには対象の
    モジュールオブジェクトを先に束縛する必要があり、その束縛経路は 1.〜5. のいずれか
    である。したがって束縛を塞げば getattr 経路も塞がる（
    `test_the_getattr_string_form_cannot_bypass_the_gate` が実測で固定する）。
    唯一の例外は「モジュールオブジェクトを引数で受け取る」経路だが、それは注入
    （DIP）であって依存方向の反転ではない。
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

#: リポジトリ内の `simulator` パッケージ本体。
_SIMULATOR_DIR = Path(__file__).resolve().parents[2]

#: 「内側」の定義。走査対象の唯一の宣言であり、スライス名の表はどこにも持たない。
#: `main` は Composition Root なので含めない。
_INNER_LAYER_NAMES = ("adapter", "usecase", "domain", "framework")

#: 禁止する依存先。前方一致で判定する（`simulator.maintenance` を誤検出しないよう
#: 「完全一致またはドット区切りの接頭辞」で比較する）。
_FORBIDDEN_MODULE = "simulator.main"


def _is_forbidden(module_name: str) -> bool:
    """`simulator.main` そのもの、またはその配下モジュールか。"""
    return module_name == _FORBIDDEN_MODULE or module_name.startswith(
        _FORBIDDEN_MODULE + "."
    )


def _package_of(path: Path) -> str:
    """モジュールファイルの所属パッケージ名（相対 import の基点）を返す。

    事前条件: `path` は `_SIMULATOR_DIR` 配下の `.py` である。
    事後条件: `__init__.py` は自身のディレクトリを、それ以外は親ディレクトリを
             ドット区切りのパッケージ名で返す（`simulator` を先頭に含む）。
    """
    relative = path.relative_to(_SIMULATOR_DIR.parent)
    parts = list(relative.parts)
    if parts[-1] == "__init__.py":
        parts = parts[:-1]
    else:
        parts = parts[:-1]
    return ".".join(parts)


def _resolve_relative(package: str, level: int, module: "str | None") -> str:
    """相対 import を絶対モジュール名へ解決する（`from ... import` の意味論）。

    `level=1` は自パッケージ、`level=2` は親パッケージ……を基点にする。
    基点より上へ遡る指定は解決不能として空文字を返す（＝禁止判定に掛からない）。
    """
    base_parts = package.split(".")
    if level - 1 > len(base_parts):
        return ""
    base = base_parts[: len(base_parts) - (level - 1)]
    if module:
        base = base + module.split(".")
    return ".".join(base)


def _imported_absolute_modules(path: Path) -> "list[str]":
    """1 ファイルが import する全モジュールを**絶対名**で列挙する。"""
    return _imported_modules_in_source(
        path.read_text(encoding="utf-8"), package=_package_of(path), filename=str(path)
    )


def _imported_modules_in_source(
    source: str, package: str, filename: str = "<source>"
) -> "list[str]":
    """ソース 1 本が import する全モジュールを**絶対名**で列挙する。

    事前条件: `package` は `source` が置かれるパッケージ名（相対 import の基点）。
    事後条件: 検出 5 形態（モジュール docstring 参照）のすべてを絶対名で返す。
    """
    tree = ast.parse(source, filename=filename)
    modules: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.level == 0:
                base = node.module or ""
            else:
                base = _resolve_relative(package, node.level, node.module)
            if not base:
                continue
            modules.append(base)
            # `from simulator import main as sim_main` は、モジュール名が `names` 側に
            # しか現れない。`base.<name>` も候補に積む（サブモジュールでない属性名は
            # 存在しないモジュール名になるだけで、禁止判定には掛からない）。
            modules.extend(f"{base}.{alias.name}" for alias in node.names)
        elif isinstance(node, ast.Call):
            modules.extend(_dynamic_import_targets(node))
    return modules


def _dynamic_import_targets(node: ast.Call) -> "list[str]":
    """`importlib.import_module("...")` / `__import__("...")` の文字列引数を拾う。"""
    func = node.func
    is_dynamic_import = (
        isinstance(func, ast.Attribute) and func.attr == "import_module"
    ) or (isinstance(func, ast.Name) and func.id in {"__import__", "import_module"})
    if not is_dynamic_import:
        return []
    return [
        arg.value
        for arg in node.args
        if isinstance(arg, ast.Constant) and isinstance(arg.value, str)
    ]


def _inner_layer_dirs() -> "list[Path]":
    """内側層ディレクトリを**構造**から発見する（スライス名の表を持たない）。

    条件: 名前が `_INNER_LAYER_NAMES` のいずれかであり、かつ `__init__.py` を持つ
    （＝ Python パッケージである）こと。`simulator/*/web/js/adapter` のような
    非 Python ディレクトリはこの条件で自動的に外れる。スライスがどの深さに増えても
    本関数は変わらない。
    """
    return sorted(
        path
        for path in _SIMULATOR_DIR.rglob("*")
        if path.is_dir()
        and path.name in _INNER_LAYER_NAMES
        and "__pycache__" not in path.parts
        and (path / "__init__.py").is_file()
    )


def _layer_id(layer_dir: Path) -> str:
    """parametrize の ID（リポジトリ相対パス）。"""
    return str(layer_dir.relative_to(_SIMULATOR_DIR.parent))


def _layer_modules(layer_dir: Path) -> "list[Path]":
    """1 層の全 `.py`（`__pycache__` を除く）。"""
    return sorted(
        path
        for path in layer_dir.rglob("*.py")
        if "__pycache__" not in path.parts
    )


#: ISSUE-405 の 4 形態 + 取り逃していた 1 形態。`(形態名, ソース)`。
_VIOLATION_FORMS = (
    ("静的 import（from 形）", "from simulator.main import build_interactor"),
    ("静的 import（絶対形）", "import simulator.main as sim_main"),
    ("相対 import", "from ...main.run_config import RunConfig"),
    ("from <親> import <サブモジュール>", "from simulator import main as sim_main"),
    ("importlib（文字列リテラル）", 'importlib.import_module("simulator.main.x")'),
)


class TestInnerLayersDoNotImportTheCompositionRoot:
    """全スライスの内側 4 層 → `simulator.main` の依存が 1 件も無いこと。"""

    @pytest.mark.parametrize("layer_dir", _inner_layer_dirs(), ids=_layer_id)
    def test_the_layer_never_imports_simulator_main(self, layer_dir):
        violations = [
            f"{path.relative_to(_SIMULATOR_DIR.parent)}: {module}"
            for path in _layer_modules(layer_dir)
            for module in _imported_absolute_modules(path)
            if _is_forbidden(module)
        ]
        assert violations == [], (
            f"{_layer_id(layer_dir)} から simulator.main への依存（依存方向の反転）: "
            + "; ".join(violations)
        )


class TestTheGateHasDetectionPower:
    """ゲート自身が「検出できる状態」にあること（空振りしていないこと）。"""

    @pytest.mark.parametrize("layer_dir", _inner_layer_dirs(), ids=_layer_id)
    def test_the_gate_actually_scans_every_module_of_every_layer(self, layer_dir):
        # 走査対象が 0 件なら、上の検査は常に通る（＝ゲートとして無意味）。
        assert len(_layer_modules(layer_dir)) > 0

    def test_the_scan_is_not_confined_to_a_single_slice(self):
        # スライス 1 個ぶんの列挙に退化していないこと（ISSUE-405 の構造的原因）。
        outside_the_engine_slice = [
            _layer_id(layer_dir)
            for layer_dir in _inner_layer_dirs()
            if layer_dir.parent != _SIMULATOR_DIR
        ]
        assert outside_the_engine_slice != []

    def test_no_python_directory_named_as_an_inner_layer_is_silently_skipped(self):
        """走査規則（`__init__.py` を持つこと）が Python 実体のある層を落としていないこと。

        `__init__.py` を持たない名前空間パッケージで層を作ると、走査から静かに外れて
        ISSUE-405 と同じ「射程の穴」が再発する。外れてよいのは Python ファイルを 1 つも
        持たないディレクトリ（`web/js/adapter` 等）だけである。
        """
        scanned = set(_inner_layer_dirs())
        skipped_but_has_python = [
            str(path.relative_to(_SIMULATOR_DIR.parent))
            for path in _SIMULATOR_DIR.rglob("*")
            if path.is_dir()
            and path.name in _INNER_LAYER_NAMES
            and "__pycache__" not in path.parts
            and path not in scanned
            and any("__pycache__" not in py.parts for py in path.rglob("*.py"))
        ]
        assert skipped_but_has_python == [], (
            "内側層の名前を持ち Python 実体もあるのに走査されていない: "
            + "; ".join(skipped_but_has_python)
        )

    def test_the_slice_that_escaped_issue_405_is_scanned(self):
        # 回帰の錨: 実際に越境していた `sim_ui/adapter` が走査対象に入っていること。
        assert _SIMULATOR_DIR / "sim_ui" / "adapter" in _inner_layer_dirs()

    @pytest.mark.parametrize(
        "form,source", _VIOLATION_FORMS, ids=[form for form, _ in _VIOLATION_FORMS]
    )
    def test_every_violation_form_is_detected(self, form, source):
        modules = _imported_modules_in_source(
            source, package="simulator.sim_ui.adapter"
        )
        assert any(_is_forbidden(module) for module in modules), (form, modules)

    def test_the_getattr_string_form_cannot_bypass_the_gate(self):
        # ISSUE-405 の実際の形。getattr 自体は import ではないが、対象モジュールの
        # 束縛（ここでは `from simulator import main as sim_main`）が検出に掛かる。
        source = (
            "from simulator import main as sim_main\n"
            'factories = getattr(sim_main, "_EA_FACTORIES", {})\n'
        )
        modules = _imported_modules_in_source(
            source, package="simulator.sim_ui.adapter"
        )
        assert any(_is_forbidden(module) for module in modules), modules

    def test_the_absolute_form_is_detected(self):
        assert _is_forbidden("simulator.main")
        assert _is_forbidden("simulator.main.tester_settings.exit_codes")

    def test_a_similarly_named_module_is_not_a_false_positive(self):
        assert not _is_forbidden("simulator.maintenance")

    def test_a_sibling_composition_root_is_not_a_false_positive(self):
        # スライス自身の Composition Root（`simulator.sim_ui.main`）は別物。
        # 本ゲートが固定するのはエンジンの Composition Root への依存だけである。
        assert not _is_forbidden("simulator.sim_ui.main")
        modules = _imported_modules_in_source(
            "from ..main import composition_root_jobs",
            package="simulator.sim_ui.adapter",
        )
        assert not any(_is_forbidden(module) for module in modules), modules

    def test_the_relative_form_is_resolved_to_the_absolute_name(self):
        # simulator/adapter/controller.py の `from ..main import x` は simulator.main
        assert _resolve_relative("simulator.adapter", 2, "main") == "simulator.main"
        # simulator/adapter/tester_settings/x.py の `from ...main import y` も同じ
        assert (
            _resolve_relative("simulator.adapter.tester_settings", 3, "main")
            == "simulator.main"
        )
        # simulator/sim_ui/adapter/x.py の `from ...main import y` も simulator.main
        assert (
            _resolve_relative("simulator.sim_ui.adapter", 3, "main") == "simulator.main"
        )
        # 自パッケージ内の相対 import は禁止先に解決されない
        assert not _is_forbidden(_resolve_relative("simulator.adapter", 1, "controller"))

    def test_the_dynamic_form_is_detected(self):
        call = ast.parse('importlib.import_module("simulator.main")').body[0].value
        assert _dynamic_import_targets(call) == ["simulator.main"]
