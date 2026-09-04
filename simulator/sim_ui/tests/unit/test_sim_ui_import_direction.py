"""ISSUE-405: `sim_ui` の依存方向を機械強制する（宣言では守れない）。

固定する規則:

    R-4 の一般化。**エンジンの Composition Root（`simulator.main`）を知ってよいのは
    `sim_ui` の Composition Root（`sim_ui/main/**`）だけ**である。adapter / usecase /
    framework / domain が `simulator.main` を直接掴むと依存が外向き（adapter → main）に
    なり、束縛の差し替え点が消える。

    前例（R-4・`test_report_payload_writer.py`）: 足の供給を `simulator.main.build_interactor`
    へ既定束縛せず、`run_job.py`（main 層）が注入する形にした。本検定はその規律を
    ファイル単位の文字列検査ではなく **AST で slice 全体に**広げる。

なぜ AST か（実測）: 文字列検査（``"from simulator.main import" not in source``）は
    `from simulator import main as sim_main` を取り逃す。ISSUE-405 で実際に取り逃していた
    形式であり、`ea_stop_loss_param_catalog.py` はこの形で越境していた。
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

#: `simulator/sim_ui/` の根（本ファイルの parents[2]）。
_SIM_UI_ROOT = Path(__file__).resolve().parents[2]

#: エンジンの Composition Root を知ってよい唯一のパッケージ（sim_ui 側 Composition Root）。
_COMPOSITION_ROOT_PACKAGE = "main"


def _production_sources() -> "list[Path]":
    """`sim_ui/` 配下の本番コード（`**/tests/**` を除く）。"""
    return sorted(
        path
        for path in _SIM_UI_ROOT.rglob("*.py")
        if "tests" not in path.relative_to(_SIM_UI_ROOT).parts
    )


#: 監視対象のモジュール（エンジンの Composition Root）。
_ENGINE_MODULE = "simulator.main"

#: 相対 import を絶対名へ解決するための、リポジトリ根から見たパッケージ接頭辞。
_PACKAGE_ROOT = _SIM_UI_ROOT.parents[1]  # `simulator/` の親＝リポジトリ根


def _is_engine_module(name: str) -> bool:
    return name == _ENGINE_MODULE or name.startswith(_ENGINE_MODULE + ".")


def _package_of(path: Path) -> "tuple[str, ...]":
    """ファイルを含むパッケージの絶対名（`simulator.sim_ui.adapter` 等）。"""
    return path.resolve().relative_to(_PACKAGE_ROOT).with_suffix("").parts[:-1]


def _absolute_module(package: "tuple[str, ...]", node: ast.ImportFrom) -> str:
    """相対 import（``from ...main.run_config import X``）を絶対モジュール名へ解決する。

    相対形は `node.module` にも文字列にも "simulator.main" が現れないため、絶対形だけを
    見る検出器は**素通しする**（実測で確認した穴）。所属パッケージから解決する。
    """
    module = node.module or ""
    if not node.level:
        return module
    base = package[: len(package) - node.level + 1]
    return ".".join([*base, module]) if module else ".".join(base)


def _imports_engine_composition_root(
    path: Path, *, package: "tuple[str, ...] | None" = None
) -> bool:
    """`simulator.main` への依存を 6 形式すべてで検出する。

        1. ``from simulator.main import build_interactor``      絶対 from
        2. ``from ...main.run_config import RunConfig``         **相対** from
        3. ``importlib.import_module("simulator.main...")``     文字列によるモジュール取得
        4. ``getattr(sim_main, "_EA_FACTORIES", {})``           （5 と併せて検出）
        5. ``from simulator import main as sim_main``           親から子モジュールを取る形
        6. ``import simulator.main``                            絶対 import

    形式 2・3 は当初の実装が取り逃していた（他エージェントの検出力プローブで判明）。
    文字列は**呼び出し引数のみ**を見る——docstring 内の `simulator.main` への言及
    （本 slice の adapter は束縛先を docstring で説明している）を違反と誤判定しないため。
    """
    package = _package_of(path) if package is None else package
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            module = _absolute_module(package, node)
            if _is_engine_module(module):
                return True
            # `from simulator import main` / `from .. import main`（親から子を取る形）
            if any(
                _is_engine_module(f"{module}.{alias.name}") for alias in node.names
            ):
                return True
        elif isinstance(node, ast.Import):
            if any(_is_engine_module(alias.name) for alias in node.names):
                return True
        elif isinstance(node, ast.Call):
            # `importlib.import_module("simulator.main")` / `__import__("simulator.main")`
            if any(
                isinstance(arg, ast.Constant)
                and isinstance(arg.value, str)
                and _is_engine_module(arg.value)
                for arg in node.args
            ):
                return True
    return False


def test_the_scan_reaches_the_adapters() -> None:
    """走査の取りこぼしで検定が空振りしないこと（ゲートの自己検査）。"""
    scanned = {str(path.relative_to(_SIM_UI_ROOT)) for path in _production_sources()}
    assert "adapter/ea_registry_series_catalog.py" in scanned
    assert "adapter/ea_stop_loss_param_catalog.py" in scanned
    assert "adapter/symbol_spec_catalog.py" in scanned


#: 依存方向違反の全形態（検出力の自己検査）。1 形態でも False なら検出の穴である。
_VIOLATION_FORMS = {
    "絶対 from": "from simulator.main import build_interactor\n",
    "相対 from": "from ...main.run_config import RunConfig\n",
    "importlib 文字列": (
        "import importlib\n"
        'm = importlib.import_module("simulator.main.tester_settings")\n'
    ),
    "getattr 文字列": (
        "from simulator import main as m\n" 'v = getattr(m, "_EA_FACTORIES", {})\n'
    ),
    "親から子を取る form": "from simulator import main as sim_main\n",
    "絶対 import": "import simulator.main\n",
}


@pytest.mark.parametrize("form", sorted(_VIOLATION_FORMS))
def test_the_detector_sees_every_violation_form(form: str, tmp_path: Path) -> None:
    """全形態を検出できること。

    当初の実装は「相対 from」と「importlib 文字列」を取り逃していた（実測）。
    絶対形だけを見る検出器は、相対 import では `node.module` に "simulator.main" が
    現れないため素通しする。検出力そのものを検定で固定する。

    相対形の解決には所属パッケージが要るため、`sim_ui/adapter/` 相当を明示して評価する
    （本番ツリーへ一時ファイルを置かない）。
    """
    sample = tmp_path / "sample.py"
    sample.write_text(_VIOLATION_FORMS[form], encoding="utf-8")
    assert _imports_engine_composition_root(
        sample, package=("simulator", "sim_ui", "adapter")
    ) is True, form


def test_the_detector_does_not_flag_other_simulator_packages(tmp_path: Path) -> None:
    """誤検出しないこと（`simulator.usecase` 等の import は依存方向違反ではない）。"""
    sample = tmp_path / "sample.py"
    sample.write_text(
        "from simulator.usecase.sizing_ports import required_price_series\n"
        "from simulator.domain.exceptions import IndicatorBufferError\n"
        "from ..usecase.job_ports import IndicatorSeriesCatalogPort\n",
        encoding="utf-8",
    )
    assert _imports_engine_composition_root(
        sample, package=("simulator", "sim_ui", "adapter")
    ) is False


def test_the_detector_does_not_flag_docstring_mentions(tmp_path: Path) -> None:
    """docstring での言及を違反と誤判定しないこと。

    本 slice の adapter は「束縛の実体は `simulator.main.build_ea_indicators`」のように
    束縛先を docstring で説明している。文字列を無差別に見る検出器はこれを違反にする。
    """
    sample = tmp_path / "sample.py"
    sample.write_text(
        '"""束縛の実体は `simulator.main.build_ea_strategy` である。"""\n'
        'NOTE = "simulator.main は Composition Root だけが知る"\n',
        encoding="utf-8",
    )
    assert _imports_engine_composition_root(
        sample, package=("simulator", "sim_ui", "adapter")
    ) is False


def test_only_the_composition_root_knows_the_engine_composition_root() -> None:
    offenders = sorted(
        str(path.relative_to(_SIM_UI_ROOT))
        for path in _production_sources()
        if _imports_engine_composition_root(path)
        and path.relative_to(_SIM_UI_ROOT).parts[0] != _COMPOSITION_ROOT_PACKAGE
    )
    assert offenders == []


def test_the_composition_root_actually_binds_the_engine() -> None:
    """規則が「誰も import しない」に退化していないこと（束縛点が実在する）。"""
    binders = [
        path
        for path in _production_sources()
        if path.relative_to(_SIM_UI_ROOT).parts[0] == _COMPOSITION_ROOT_PACKAGE
        and _imports_engine_composition_root(path)
    ]
    assert binders != []
