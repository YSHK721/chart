"""ISSUE-449: `dashboard_ui` の依存方向を機械強制する（宣言では守れない）。

固定する規則（arch-spec §1 の依存方向: main → framework → adapter → usecase → domain → common）:

    R1: `domain/**` が知ってよいのは **stdlib / numpy / common / dashboard_ui.domain** だけ。
    R2: `usecase/**` は R1 に `dashboard_ui.usecase` を加えたものだけ。特に **pandas・HTTP・
        指標パッケージ・bridge** を知ってはならない（pandas は adapter に閉じる）。

なぜ AST か（前例の実測・`simulator/sim_ui/tests/unit/test_sim_ui_import_direction.py`）:
    文字列検査（``"import pandas" not in source``）は `from x import pandas as pd` や
    相対 import、`importlib.import_module("...")` を**取り逃す**。ISSUE-405 では実際に
    取り逃しており、`ea_stop_loss_param_catalog.py` がその形で越境していた。
    本検定は同じ 6 形式を AST で見る。検出力そのものも検定で固定する。
"""
from __future__ import annotations

import ast
import sys
from pathlib import Path

import pytest

#: `dashboard_ui/` の根（本ファイルの parents[2]）。
_ROOT = Path(__file__).resolve().parents[2]

#: 相対 import を絶対名へ解決するための、リポジトリ根から見たパッケージ接頭辞。
_PACKAGE_ROOT = _ROOT.parent

#: どの層でも許す外部依存（domain の許可集合）。
_DOMAIN_ALLOWED = frozenset({"numpy", "common", "dashboard_ui"})

#: 層ごとに許す `dashboard_ui.*` サブパッケージ。
_ALLOWED_SUBPACKAGES = {
    "domain": frozenset({"dashboard_ui.domain"}),
    "usecase": frozenset({"dashboard_ui.domain", "dashboard_ui.usecase"}),
}

#: 内側の層が知ってはならないもの（越境の代表例。許可集合の補集合に含まれるが明示する）。
_FORBIDDEN_ROOTS = frozenset(
    {"pandas", "http", "urllib", "requests", "indigators", "simulator",
     "unified_ui", "flask", "django"}
)


def _production_sources(layer: str) -> "list[Path]":
    return sorted(
        path
        for path in (_ROOT / layer).rglob("*.py")
        if "tests" not in path.relative_to(_ROOT).parts
    )


def _package_of(path: Path) -> "tuple[str, ...]":
    return path.resolve().relative_to(_PACKAGE_ROOT).with_suffix("").parts[:-1]


def _absolute_module(package: "tuple[str, ...]", node: ast.ImportFrom) -> str:
    """相対 import（``from ..domain.reach import X``）を絶対モジュール名へ解決する。

    相対形は `node.module` に絶対名が現れないため、絶対形だけを見る検出器は素通しする。
    """
    module = node.module or ""
    if not node.level:
        return module
    base = package[: len(package) - node.level + 1]
    return ".".join([*base, module]) if module else ".".join(base)


def imported_modules(
    path: Path, *, package: "tuple[str, ...] | None" = None, source: "str | None" = None
) -> "frozenset[str]":
    """依存の 6 形式すべてを絶対モジュール名として集める。

        1. ``from pandas import DataFrame``                 絶対 from
        2. ``from ...adapter.gateway import X``             **相対** from
        3. ``importlib.import_module("pandas")``            文字列によるモジュール取得
        4. ``getattr(pd, "DataFrame")``                     （5 と併せて検出）
        5. ``from dashboard_ui import adapter``             親から子モジュールを取る形
        6. ``import pandas``                                絶対 import

    文字列は**呼び出し引数のみ**を見る（docstring の言及を違反と誤判定しないため）。
    """
    package = _package_of(path) if package is None else package
    tree = ast.parse(source if source is not None else path.read_text(encoding="utf-8"))
    found: "set[str]" = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            module = _absolute_module(package, node)
            if module:
                found.add(module)
                found.update(f"{module}.{alias.name}" for alias in node.names)
        elif isinstance(node, ast.Import):
            found.update(alias.name for alias in node.names)
        elif isinstance(node, ast.Call) and _is_import_call(node):
            # 文字列は**import 機構の呼び出し引数だけ**を見る。あらゆる文字列を見ると
            # `params.get("timeframe")` や `step_events(..., "episode", ...)` を
            # モジュール名と誤判定する（実測で誤検出した）。
            found.update(
                arg.value
                for arg in node.args
                if isinstance(arg, ast.Constant) and isinstance(arg.value, str)
            )
    return frozenset(found)


def _is_import_call(node: ast.Call) -> bool:
    """``importlib.import_module(...)`` / ``__import__(...)`` か。"""
    func = node.func
    if isinstance(func, ast.Attribute):
        return func.attr in {"import_module", "__import__"}
    if isinstance(func, ast.Name):
        return func.id in {"import_module", "__import__"}
    return False


def violations(modules: "frozenset[str]", layer: str) -> "frozenset[str]":
    """その層が知ってはならないモジュール名を返す。"""
    allowed_sub = _ALLOWED_SUBPACKAGES[layer]
    offenders: "set[str]" = set()
    for module in modules:
        root = module.split(".")[0]
        if root in sys.stdlib_module_names:
            continue
        if root == "dashboard_ui":
            if module == "dashboard_ui":
                continue
            if not any(
                module == allowed or module.startswith(allowed + ".")
                for allowed in allowed_sub
            ):
                offenders.add(module)
            continue
        if root not in _DOMAIN_ALLOWED:
            offenders.add(module)
    return frozenset(offenders)


# ------------------------------------------------------------ ゲートの自己検査
def test_the_scan_reaches_the_domain_modules() -> None:
    """走査の取りこぼしで検定が空振りしないこと。"""
    scanned = {str(path.relative_to(_ROOT)) for path in _production_sources("domain")}

    assert "domain/reach.py" in scanned
    assert "domain/continuous_quantile.py" in scanned
    assert "domain/price_value_map.py" in scanned


def test_the_scan_reaches_the_usecase_modules() -> None:
    scanned = {str(path.relative_to(_ROOT)) for path in _production_sources("usecase")}

    assert "usecase/build_reach_sheet.py" in scanned
    assert "usecase/update_reach_sheet.py" in scanned


#: 依存方向違反の全形態（検出力の自己検査）。1 形態でも取り逃せば検出の穴である。
_VIOLATION_FORMS = {
    "絶対 from": "from pandas import DataFrame\n",
    "相対 from": "from ..adapter.gateway import Gateway\n",
    "importlib 文字列": (
        "import importlib\n" 'm = importlib.import_module("pandas")\n'
    ),
    "getattr 文字列": (
        "from dashboard_ui import adapter as a\n" 'v = getattr(a, "Gateway", None)\n'
    ),
    "親から子を取る form": "from dashboard_ui import adapter\n",
    "絶対 import": "import pandas\n",
}


@pytest.mark.parametrize("form", sorted(_VIOLATION_FORMS))
def test_the_detector_sees_every_violation_form(form: str, tmp_path: Path) -> None:
    """全形態を検出できること（本番ツリーへ一時ファイルを置かずに評価する）。"""
    modules = imported_modules(
        tmp_path / "sample.py",
        package=("dashboard_ui", "domain"),
        source=_VIOLATION_FORMS[form],
    )

    assert violations(modules, "domain") != frozenset(), form


def test_the_detector_does_not_flag_the_allowed_dependencies(tmp_path: Path) -> None:
    """誤検出しないこと（numpy / common / stdlib / 自層は依存方向違反ではない）。"""
    modules = imported_modules(
        tmp_path / "sample.py",
        package=("dashboard_ui", "domain"),
        source=(
            "from __future__ import annotations\n"
            "import math\n"
            "import numpy as np\n"
            "from common import gpd as _gpd\n"
            "from common.marod_bands import rolling_causal_pointwise\n"
            "from dashboard_ui.domain.bar import Bar\n"
            "from .reach import LevelSide\n"
        ),
    )

    assert violations(modules, "domain") == frozenset()


def test_the_detector_does_not_flag_docstring_mentions(tmp_path: Path) -> None:
    """docstring での言及を違反と誤判定しないこと（本 slice は設計書を docstring で引く）。"""
    modules = imported_modules(
        tmp_path / "sample.py",
        package=("dashboard_ui", "domain"),
        source=(
            '"""pandas はここでは使わない（adapter に閉じる）。"""\n'
            'NOTE = "pandas は adapter だけが知る"\n'
        ),
    )

    assert violations(modules, "domain") == frozenset()


def test_the_usecase_layer_may_depend_on_the_domain_layer(tmp_path: Path) -> None:
    """規則が「何も import できない」に退化していないこと。"""
    modules = imported_modules(
        tmp_path / "sample.py",
        package=("dashboard_ui", "usecase"),
        source="from dashboard_ui.domain.reach import reach_state\n",
    )

    assert violations(modules, "usecase") == frozenset()


def test_the_domain_layer_may_not_depend_on_the_usecase_layer(tmp_path: Path) -> None:
    """依存の向きが逆流していないこと（domain は usecase を知らない）。"""
    modules = imported_modules(
        tmp_path / "sample.py",
        package=("dashboard_ui", "domain"),
        source="from dashboard_ui.usecase.sheet_models import SheetInstance\n",
    )

    assert violations(modules, "domain") != frozenset()


# ------------------------------------------------------------------ 本番の検定
def test_the_domain_layer_only_knows_stdlib_numpy_and_common() -> None:
    offenders = {
        str(path.relative_to(_ROOT)): sorted(violations(imported_modules(path), "domain"))
        for path in _production_sources("domain")
        if violations(imported_modules(path), "domain")
    }

    assert offenders == {}


def test_the_usecase_layer_knows_nothing_outside_its_own_layer_and_the_domain() -> None:
    offenders = {
        str(path.relative_to(_ROOT)): sorted(violations(imported_modules(path), "usecase"))
        for path in _production_sources("usecase")
        if violations(imported_modules(path), "usecase")
    }

    assert offenders == {}


@pytest.mark.parametrize("layer", ["domain", "usecase"])
def test_no_forbidden_root_is_reachable_from_the_inner_layers(layer: str) -> None:
    """代表的な越境先（pandas・HTTP・指標パッケージ）を名指しでも塞ぐ。"""
    reached = set()
    for path in _production_sources(layer):
        reached.update(
            module.split(".")[0] for module in imported_modules(path)
        )

    assert reached & _FORBIDDEN_ROOTS == set()


# ------------------------------------------------------- R3 / R4 / R5（外側の層）
#: 外側の技術。**adapter だけ**が知ってよい（pandas・指標計算 Facade・素材・bridge）。
_ADAPTER_ONLY_ROOTS = (
    "pandas",
    "marketdata",
    "adapter.compute",
    # ISSUE-479 Wave2b: ロード面の所有者は供給側スライスへ移り、旧位置（再公開層）は
    #   削除された。塞ぐ対象は所有者 1 点で足りる——存在しない旧位置を許容集合へ
    #   書き続ける必要はない（許容集合は小さいほど強い）。
    "indigators.indicator_ui.api_loader",
)


def technology_offenders(modules: "frozenset[str]") -> "frozenset[str]":
    """adapter だけに許される技術への参照を拾う（R3）。"""
    return frozenset(
        module
        for module in modules
        for allowed in _ADAPTER_ONLY_ROOTS
        if module == allowed or module.startswith(allowed + ".")
    )


@pytest.mark.parametrize(
    "form",
    [
        "import pandas as pd\n",
        "from indigators.indicator_ui import api_loader\n",
        "from adapter.compute import full_compute\n",
        "from marketdata.tf_meta import period_start_unix\n",
        "from adapter.compute.live_tick_tails import is_incremental\n",
    ],
)
def test_the_technology_detector_sees_the_bridge_and_pandas(
    form: str, tmp_path: Path
) -> None:
    """検出力の自己検査（R3 が空振りしないこと）。技術の入口を 1 つずつ見る。"""
    modules = imported_modules(
        tmp_path / "sample.py", package=("dashboard_ui", "framework"), source=form
    )

    assert technology_offenders(modules) != frozenset(), form


def test_the_technology_detector_allows_the_shared_static_file_server(tmp_path: Path) -> None:
    """誤検出しないこと（共有の静的配信器は技術の越境ではない）。"""
    modules = imported_modules(
        tmp_path / "sample.py",
        package=("dashboard_ui", "framework"),
        source=(
            "from simulator.replay_ui.framework.static_file_server import "
            "StaticFileServer\n"
        ),
    )

    assert technology_offenders(modules) == frozenset()


@pytest.mark.parametrize("layer", ["domain", "usecase", "framework", "main"])
def test_only_the_adapter_layer_knows_pandas_and_the_compute_bridge(layer: str) -> None:
    """R3: pandas・指標計算 Facade・素材・bridge は adapter に閉じる。"""
    offenders = {
        str(path.relative_to(_ROOT)): sorted(technology_offenders(imported_modules(path)))
        for path in _production_sources(layer)
        if technology_offenders(imported_modules(path))
    }

    assert offenders == {}


def test_the_adapter_layer_actually_uses_those_technologies() -> None:
    """R3 が「誰も使っていないから緑」になっていないこと（規則の意味の自己検査）。"""
    reached: "set[str]" = set()
    for path in _production_sources("adapter"):
        reached.update(technology_offenders(imported_modules(path)))

    assert reached


@pytest.mark.parametrize("layer", ["framework", "adapter"])
def test_the_framework_and_the_adapter_do_not_know_each_other(layer: str) -> None:
    """R4: framework と adapter を同時に知ってよいのは main だけ。"""
    other = "dashboard_ui.adapter" if layer == "framework" else "dashboard_ui.framework"
    offenders = {
        str(path.relative_to(_ROOT))
        for path in _production_sources(layer)
        if any(
            module == other or module.startswith(other + ".")
            for module in imported_modules(path)
        )
    }

    assert offenders == set()


def test_the_composition_root_binds_both_sides() -> None:
    """R5: 束縛点が実在し、そこだけが両側を知る。"""
    root = _ROOT / "main" / "composition_root.py"
    modules = imported_modules(root)

    assert root.is_file()
    assert any(module.startswith("dashboard_ui.adapter") for module in modules)
    assert any(module.startswith("dashboard_ui.framework") for module in modules)


@pytest.mark.parametrize(
    "concrete",
    ["IndicatorUiComputeGateway", "ForwardEvaluationGateway", "ReachSheetController",
     "BreakpointRegistry", "SeriesRoleTable", "ElapsedComparisonGateway"],
)
def test_the_concrete_pieces_are_wired_only_at_the_binding_point(concrete: str) -> None:
    """R5: 具象を組み立ててよいのは main だけ（他の層は Protocol 越しに受け取る）。"""
    importers = {
        str(path.relative_to(_ROOT))
        for layer in ("domain", "usecase", "adapter", "framework")
        for path in _production_sources(layer)
        if any(module.endswith("." + concrete) for module in imported_modules(path))
    }

    assert importers <= {"adapter/breakpoints/__init__.py"}


def top_level_modules(path: Path, *, source: "str | None" = None) -> "frozenset[str]":
    """**モジュール本体**（トップレベル）の import だけを集める。

    関数の中の import は「実行時に初めて要る依存」であり、層の依存関係とは別物である。
    プロセス起動点（serve_dashboard の main）が Composition Root を呼ぶのはこの形になる。
    """
    tree = ast.parse(source if source is not None else path.read_text(encoding="utf-8"))
    found: "set[str]" = set()
    for node in tree.body:
        if isinstance(node, ast.ImportFrom) and node.module:
            found.add(node.module)
            found.update(f"{node.module}.{alias.name}" for alias in node.names)
        elif isinstance(node, ast.Import):
            found.update(alias.name for alias in node.names)
    return frozenset(found)


def test_the_top_level_scan_ignores_imports_inside_functions(tmp_path: Path) -> None:
    """検出力の自己検査（トップレベルだけを見ていること）。"""
    source = (
        "import json\n"
        "def main():\n"
        "    from dashboard_ui.main.composition_root import build_dashboard_app\n"
    )

    modules = top_level_modules(tmp_path / "sample.py", source=source)

    assert "json" in modules
    assert not any(module.startswith("dashboard_ui") for module in modules)


def test_the_framework_body_does_not_bind_anything() -> None:
    """framework の本体は `dashboard_ui` の中身を知らない（束縛は main だけが行う）。

    プロセス起動点（`python -m dashboard_ui.framework.serve_dashboard <port>`）だけが
    Composition Root を関数の中で呼ぶ。unified_ui/serve.sh がこの形で起動する。
    """
    bound = {
        str(path.relative_to(_ROOT)): sorted(
            module for module in top_level_modules(path)
            if module.startswith("dashboard_ui")
        )
        for path in _production_sources("framework")
        if any(module.startswith("dashboard_ui") for module in top_level_modules(path))
    }

    assert bound == {}


def test_the_only_main_reference_from_the_framework_is_the_process_entry_point() -> None:
    entry = _ROOT / "framework" / "serve_dashboard.py"

    referenced = sorted(
        module for module in imported_modules(entry)
        if module.startswith("dashboard_ui")
    )

    assert referenced == [
        "dashboard_ui.main.composition_root",
        "dashboard_ui.main.composition_root.build_dashboard_app",
    ]
