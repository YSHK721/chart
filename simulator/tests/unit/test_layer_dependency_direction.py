"""内側 4 層が `simulator.main` を import しないことを構文木で固定するゲート。

固定する仕様（依存の向き）:
    `simulator.adapter` / `simulator.usecase` / `simulator.domain` /
    `simulator.framework` の全モジュールは `simulator.main` を import しない。
    `simulator.main` は Composition Root（最も外側）であり、内側から外側への依存は
    依存方向の反転そのものである。`adapter/controller.py` の docstring が
    「adapter 層は usecase + domain のみに依存する（framework / main は import しない）」
    と宣言しており、本ゲートはその宣言をリポジトリ全体へ機械的に拡張する。

なぜ 1 ファイル限定の既存検査では足りないか（実測）:
    既存の同種検査は走査対象を固定パスの列挙で持つ——
    `test_is_oos_dependency_direction.py`（`usecase/run_is_oos.py` の 1 件）・
    `test_optimize_dependency.py`（`usecase/optimize*.py` の 3 件）・
    `test_weekly_vol_band_dependency_direction.py`（`usecase` の 3 件）。
    列挙に載っていない新規モジュールは、違反しても永久に検出されない。本ゲートは
    列挙を持たず、4 層のディレクトリを走査して全 `.py` を対象にする（対象数は
    `test_the_gate_actually_scans_every_module_of_every_layer` が下限で固定する）。

なぜ構文木で測るか:
    import 実行時の副作用（`sys.modules` 観測）では、条件分岐の下に隠れた import や
    実行されなかった分岐を取りこぼす。import 文の所在は構文木にしか現れないので、
    構文木を数える。

検出する 3 形態:
    1. `import simulator.main` / `import simulator.main.x as y`
    2. `from simulator.main import x` / `from simulator.main.x import y`
    3. 相対 import（`from ..main import x` 等）を絶対名へ解決したもの
    4. `importlib.import_module("simulator.main...")`（文字列リテラル引数のみ）
       ——現状の内側 4 層に `importlib` の使用は 0 件（実測）だが、動的 import は
       1.〜3. と同じ「import する」行為であり、静的形態だけを塞ぐと素通りする。
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

#: リポジトリ内の `simulator` パッケージ本体。
_SIMULATOR_DIR = Path(__file__).resolve().parents[2]

#: 走査対象（内側 4 層）。`main` は Composition Root なので対象外。
_INNER_LAYERS = ("adapter", "usecase", "domain", "framework")

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
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    package = _package_of(path)
    modules: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.level == 0:
                if node.module:
                    modules.append(node.module)
            else:
                resolved = _resolve_relative(package, node.level, node.module)
                if resolved:
                    modules.append(resolved)
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


def _layer_modules(layer: str) -> "list[Path]":
    """1 層の全 `.py`（`__pycache__` を除く）。"""
    return sorted(
        path
        for path in (_SIMULATOR_DIR / layer).rglob("*.py")
        if "__pycache__" not in path.parts
    )


class TestInnerLayersDoNotImportTheCompositionRoot:
    """内側 4 層 → `simulator.main` の依存が 1 件も無いこと。"""

    @pytest.mark.parametrize("layer", _INNER_LAYERS)
    def test_the_layer_never_imports_simulator_main(self, layer):
        violations = [
            f"{path.relative_to(_SIMULATOR_DIR.parent)}: {module}"
            for path in _layer_modules(layer)
            for module in _imported_absolute_modules(path)
            if _is_forbidden(module)
        ]
        assert violations == [], (
            f"{layer} 層から simulator.main への依存（依存方向の反転）: " + "; ".join(violations)
        )


class TestTheGateHasDetectionPower:
    """ゲート自身が「検出できる状態」にあること（空振りしていないこと）。"""

    @pytest.mark.parametrize("layer", _INNER_LAYERS)
    def test_the_gate_actually_scans_every_module_of_every_layer(self, layer):
        # 走査対象が 0 件なら、上の検査は常に通る（＝ゲートとして無意味）。
        assert len(_layer_modules(layer)) > 0

    def test_the_absolute_form_is_detected(self):
        assert _is_forbidden("simulator.main")
        assert _is_forbidden("simulator.main.tester_settings.exit_codes")

    def test_a_similarly_named_module_is_not_a_false_positive(self):
        assert not _is_forbidden("simulator.maintenance")

    def test_the_relative_form_is_resolved_to_the_absolute_name(self):
        # simulator/adapter/controller.py の `from ..main import x` は simulator.main
        assert _resolve_relative("simulator.adapter", 2, "main") == "simulator.main"
        # simulator/adapter/tester_settings/x.py の `from ...main import y` も同じ
        assert (
            _resolve_relative("simulator.adapter.tester_settings", 3, "main")
            == "simulator.main"
        )
        # 自パッケージ内の相対 import は禁止先に解決されない
        assert not _is_forbidden(_resolve_relative("simulator.adapter", 1, "controller"))

    def test_the_dynamic_form_is_detected(self):
        call = ast.parse('importlib.import_module("simulator.main")').body[0].value
        assert _dynamic_import_targets(call) == ["simulator.main"]
