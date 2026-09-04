"""replay_ui の本番コードが sim_ui を import しないこと（ISSUE-479 Wave2b・AST 検定）。

固定する規則:
    replay_ui と sim_ui は**同格の配信殻**である。片方がもう片方の内部（framework の
    骨格・adapter の DTO）を掴むと、同格のはずの 2 殻の間に上下ができ、借りられた側は
    自分の都合で内部を動かせなくなる。共有したい純粋物は中立パッケージ api_shared が
    所有する（market_profile → indicator_ui に対する
    test_no_indicator_ui_dependency.py と同じ規律の展開）。

実測した違反（本検定を書いた時点）:
    replay_ui 本番から sim_ui への import は 4 件、その**全件**が
    `simulator.sim_ui.framework.json_get_routes.GetRouteResponder` の借用だった。

        framework/serve_replay_candles.py:27
        framework/serve_replay_catalog.py:19
        framework/serve_replay_intraday.py:19
        framework/serve_replay_profiles.py:26

    骨格 1 個を api_shared.json_get_routes へ移設すれば辺は 0 件になる。

なぜ AST か（`test_sim_ui_import_direction.py` の実測に基づく）:
    文字列検査（``"from simulator.sim_ui" not in source``）は
    ``from simulator import sim_ui`` や相対 import ``from ...sim_ui.framework import X``
    を取り逃す。docstring での言及も違反と誤判定する。検出力そのものを検定で固定する。
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

#: `simulator/replay_ui/` の根（本ファイルの parents[2]）。
_REPLAY_UI_ROOT = Path(__file__).resolve().parents[2]

#: 相対 import を絶対名へ解決するための、リポジトリ根から見た接頭辞。
_PACKAGE_ROOT = _REPLAY_UI_ROOT.parents[1]  # `simulator/` の親＝リポジトリ根

#: 借りてはならない同格の殻。
_FOREIGN_SHELL = "simulator.sim_ui"


def _production_sources() -> "list[Path]":
    """`replay_ui/` 配下の本番コード（`**/tests/**` を除く）。"""
    return sorted(
        path
        for path in _REPLAY_UI_ROOT.rglob("*.py")
        if "tests" not in path.relative_to(_REPLAY_UI_ROOT).parts
    )


def _is_foreign_shell(name: str) -> bool:
    return name == _FOREIGN_SHELL or name.startswith(_FOREIGN_SHELL + ".")


def _package_of(path: Path) -> "tuple[str, ...]":
    return path.resolve().relative_to(_PACKAGE_ROOT).with_suffix("").parts[:-1]


def _absolute_module(package: "tuple[str, ...]", node: ast.ImportFrom) -> str:
    """相対 import を絶対モジュール名へ解決する（絶対形だけを見る検出器は素通しする）。"""
    module = node.module or ""
    if not node.level:
        return module
    base = package[: len(package) - node.level + 1]
    return ".".join([*base, module]) if module else ".".join(base)


def _imports_foreign_shell(
    path: Path, *, package: "tuple[str, ...] | None" = None
) -> bool:
    """`simulator.sim_ui` への依存を全形式で検出する。

        1. ``from simulator.sim_ui.framework.json_get_routes import GetRouteResponder``
        2. ``from ...sim_ui.framework.json_get_routes import GetRouteResponder``（相対）
        3. ``importlib.import_module("simulator.sim_ui.framework.json_get_routes")``
        4. ``from simulator import sim_ui``（親から子を取る形）
        5. ``import simulator.sim_ui``

    文字列は**呼び出し引数のみ**を見る（docstring での言及を違反と誤判定しないため）。
    """
    package = _package_of(path) if package is None else package
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            module = _absolute_module(package, node)
            if _is_foreign_shell(module):
                return True
            if any(_is_foreign_shell(f"{module}.{alias.name}") for alias in node.names):
                return True
        elif isinstance(node, ast.Import):
            if any(_is_foreign_shell(alias.name) for alias in node.names):
                return True
        elif isinstance(node, ast.Call):
            if any(
                isinstance(arg, ast.Constant)
                and isinstance(arg.value, str)
                and _is_foreign_shell(arg.value)
                for arg in node.args
            ):
                return True
    return False


# --------------------------------------------------------------------------------------
# ゲートの自己検査（走査到達・検出力・誤検出しないこと）
# --------------------------------------------------------------------------------------
def test_the_scan_reaches_the_route_apps() -> None:
    """走査の取りこぼしで検定が空振りしないこと。

    列挙したのは実測で違反していた 4 ファイル。ここが走査されていなければ
    「offenders == []」は無意味になる。
    """
    scanned = {str(path.relative_to(_REPLAY_UI_ROOT)) for path in _production_sources()}
    for name in (
        "framework/serve_replay_candles.py",
        "framework/serve_replay_catalog.py",
        "framework/serve_replay_intraday.py",
        "framework/serve_replay_profiles.py",
    ):
        assert name in scanned, name


#: 依存方向違反の全形態（検出力の自己検査）。1 形態でも False なら検出の穴である。
_VIOLATION_FORMS = {
    "絶対 from": (
        "from simulator.sim_ui.framework.json_get_routes import GetRouteResponder\n"
    ),
    "相対 from": "from ...sim_ui.framework.json_get_routes import GetRouteResponder\n",
    "importlib 文字列": (
        "import importlib\n"
        'm = importlib.import_module("simulator.sim_ui.framework.json_get_routes")\n'
    ),
    "親から子を取る form": "from simulator import sim_ui\n",
    "絶対 import": "import simulator.sim_ui.framework.json_get_routes\n",
}


@pytest.mark.parametrize("form", sorted(_VIOLATION_FORMS))
def test_the_detector_sees_every_violation_form(form: str, tmp_path: Path) -> None:
    """全形態を検出できること（本番ツリーへ一時ファイルを置かずに評価する）。"""
    sample = tmp_path / "sample.py"
    sample.write_text(_VIOLATION_FORMS[form], encoding="utf-8")
    assert (
        _imports_foreign_shell(
            sample, package=("simulator", "replay_ui", "framework")
        )
        is True
    ), form


def test_the_detector_does_not_flag_the_neutral_package(tmp_path: Path) -> None:
    """誤検出しないこと（中立 api_shared・自 slice・engine への import は違反ではない）。"""
    sample = tmp_path / "sample.py"
    sample.write_text(
        "from api_shared.json_get_routes import GetRouteResponder\n"
        "from api_shared.http_contract import nested_error\n"
        "from simulator.replay_ui.framework.serve_replay import write_replay_json\n"
        "from ..usecase.causal_compute import compute\n",
        encoding="utf-8",
    )
    assert (
        _imports_foreign_shell(sample, package=("simulator", "replay_ui", "framework"))
        is False
    )


def test_the_detector_does_not_flag_docstring_mentions(tmp_path: Path) -> None:
    """docstring での言及を違反と誤判定しないこと。

    `serve_replay.py` は「ヘッダは sim 側と異なる」を docstring で説明している。
    文字列を無差別に見る検出器はこれを違反にする。
    """
    sample = tmp_path / "sample.py"
    sample.write_text(
        '"""ヘッダは sim 側（simulator.sim_ui.framework の write_json）と異なる。"""\n'
        'NOTE = "simulator.sim_ui は同格の殻であり借りない"\n',
        encoding="utf-8",
    )
    assert (
        _imports_foreign_shell(sample, package=("simulator", "replay_ui", "framework"))
        is False
    )


# --------------------------------------------------------------------------------------
# 本体
# --------------------------------------------------------------------------------------
def test_no_production_module_imports_the_sibling_shell() -> None:
    """replay_ui 本番から sim_ui への import は 0 件であること。"""
    offenders = sorted(
        str(path.relative_to(_REPLAY_UI_ROOT))
        for path in _production_sources()
        if _imports_foreign_shell(path)
    )
    assert offenders == [], (
        "replay_ui が同格の殻 sim_ui を import しています。共有したい純粋物は"
        " 中立パッケージ api_shared へ移設してください。"
    )
