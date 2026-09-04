"""A-SubprocessJobLauncher が import パスの供給を**注入**で受けることを固定する。

ISSUE-479 Wave2 是正 1（コーディネータ裁定）。

なぜ必要か（新たに増えた循環辺）:
    Wave2 2-2 の追補で ``simulator/sim_ui/adapter/subprocess_job_launcher.py`` が
    運用スクリプト層の install_dev_paths を module-level import した。台帳
    （tools/dev_paths.txt）を単一ソースにする方向は正しいが、運用スクリプト層は
    simulator を import する側（既存の辺）である。adapter から静的に掴み返すと
    simulator ⇄ 運用スクリプト層の循環辺が新たに 1 本増える。依存の向きは
    宣言では守れないので AST で固定する。

本 Wave の解（F-5 = A-SettingsIniValidator と同一の規律）:
    launcher は「repo 根 → import パス列」を返す関数を**注入**で受け、具象の束縛は
    sim_ui の Composition Root（``composition_root_jobs.build_sim_job_app``）が
    関数内 import で行う。**既定値は置かない**——既定値があると「注入し忘れても動く」
    経路が残り、逆流が黙って復活する。

計算量検定（絶対命令 2026-08-28）: 結線（アプリ組み立て）は台帳読込を 1 回も発行しない。
    子の環境を k 回組んだときだけ k 回発行する（発行 − 使用 = 0）。アプリ 1 個 / 4 個の
    2 点で、結線あたりの発行が増えないことを固定する。回数リテラルは焼き込まない。
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from simulator.sim_ui.adapter.subprocess_job_launcher import SubprocessJobLauncher
from simulator.sim_ui.main.composition_root_jobs import build_sim_job_app

#: `simulator/sim_ui/` の根（本ファイルの parents[2]）。
_SIM_UI_ROOT = Path(__file__).resolve().parents[2]

#: 運用スクリプト層を知ってよい唯一のパッケージ（sim_ui 側 Composition Root）。
_COMPOSITION_ROOT_PACKAGE = "main"

#: 禁じるトップレベルパッケージ根（別アクターであり、既に simulator を import する側）。
_FORBIDDEN_ROOT = "tools"


def _production_sources() -> "list[Path]":
    """`sim_ui/` 配下の本番コード（`**/tests/**` を除く）。"""
    return sorted(
        path
        for path in _SIM_UI_ROOT.rglob("*.py")
        if "tests" not in path.relative_to(_SIM_UI_ROOT).parts
    )


def _imported_roots(source: str) -> "set[str]":
    """import 文の**絶対**形からトップレベルパッケージ根の集合を返す。

    相対形（``from ..tools import x``）は自スライス内の同名サブパッケージを指すため
    対象外にする。逆に ``from simulator.report_ui.tools import x`` は根が simulator
    なので違反ではない——判定を根で行うのはこの取り違えを起こさないためである。
    関数内の遅延 import も拾う（宣言を迂回する穴を作らない）。
    """
    roots: "set[str]" = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            roots.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.module and not node.level:
                roots.add(node.module.split(".")[0])
        elif isinstance(node, ast.Call):
            # importlib.import_module("tools.install_dev_paths") 形（文字列での取得）。
            roots.update(
                arg.value.split(".")[0]
                for arg in node.args
                if isinstance(arg, ast.Constant) and isinstance(arg.value, str)
            )
    return roots


def _forbidden_roots_of(path: Path) -> "set[str]":
    return _imported_roots(path.read_text(encoding="utf-8")) & {_FORBIDDEN_ROOT}


# --------------------------------------------------------------------------------------
# ゲートの自己検査（走査と検出力）
# --------------------------------------------------------------------------------------
def test_the_scan_reaches_the_launcher_adapter() -> None:
    """走査の取りこぼしで本検定が恒真式に退化しないこと。"""
    scanned = {str(path.relative_to(_SIM_UI_ROOT)) for path in _production_sources()}
    assert "adapter/subprocess_job_launcher.py" in scanned
    assert "main/composition_root_jobs.py" in scanned


#: 依存方向違反の全形態（検出力の自己検査）。1 形態でも取り逃せば検出の穴である。
_VIOLATION_FORMS = {
    "絶対 from": "from tools.install_dev_paths import path_entries\n",
    "絶対 import": "import tools.install_dev_paths\n",
    "親から子を取る form": "from tools import install_dev_paths\n",
    "importlib 文字列": (
        "import importlib\n"
        'm = importlib.import_module("tools.install_dev_paths")\n'
    ),
}

#: 違反ではない形（誤検出の自己検査）。
_INNOCENT_FORMS = {
    "他スライスの同名サブパッケージ": (
        "from simulator.report_ui.tools.int_time_views import to_view\n"
    ),
    "相対 import": "from ..tools import helper\n",
    "stdlib": "import subprocess\n",
    "散文での言及": '"""束縛は tools.install_dev_paths が持つ。"""\n',
}


@pytest.mark.parametrize("form", sorted(_VIOLATION_FORMS))
def test_the_detector_sees_every_violation_form(form: str) -> None:
    assert _FORBIDDEN_ROOT in _imported_roots(_VIOLATION_FORMS[form]), form


@pytest.mark.parametrize("form", sorted(_INNOCENT_FORMS))
def test_the_detector_does_not_flag_innocent_forms(form: str) -> None:
    assert _FORBIDDEN_ROOT not in _imported_roots(_INNOCENT_FORMS[form]), form


# --------------------------------------------------------------------------------------
# 依存の向き（Composition Root 以外は運用スクリプト層を知らない）
# --------------------------------------------------------------------------------------
def test_only_the_composition_root_knows_the_tooling_actor() -> None:
    """adapter / usecase / framework / domain に運用スクリプト層への辺が無い。"""
    offenders = sorted(
        str(path.relative_to(_SIM_UI_ROOT))
        for path in _production_sources()
        if _forbidden_roots_of(path)
        and path.relative_to(_SIM_UI_ROOT).parts[0] != _COMPOSITION_ROOT_PACKAGE
    )
    assert offenders == [], (
        f"Composition Root 以外が別アクターを import しています: {offenders}。"
        " 具象は Composition Root で束縛し、注入で受けてください（DIP・F-5 と同規律）。"
    )


# --------------------------------------------------------------------------------------
# 注入の強制（既定値を置かない）
# --------------------------------------------------------------------------------------
def test_the_launcher_refuses_to_be_built_without_an_injected_path_source() -> None:
    """既定値を置かない（注入し忘れても動く実装は逆流を黙って復活させる）。"""
    with pytest.raises(TypeError):
        SubprocessJobLauncher(job_dir_of=lambda _id: Path("/nowhere"))  # type: ignore[call-arg]


def test_the_composition_root_binds_the_real_ledger_reader(tmp_path: Path) -> None:
    """Root が台帳の実読み手を束縛する（規則の第 2 実装を作らない）。"""
    # Arrange
    from tools.install_dev_paths import path_entries

    # Act
    app = build_sim_job_app(repo_root=tmp_path, web_dir=tmp_path / "web")
    entries = app.launcher._path_entries(tmp_path)
    # Assert（束縛先の同一性を、値の一致で示す＝関数内 import でも観測できる形）
    assert list(entries) == list(path_entries(tmp_path))
    assert entries, "台帳が空です（束縛先が実読み手でない可能性）"


def test_the_injected_source_is_the_only_supplier_of_the_child_path(
    monkeypatch, tmp_path: Path
) -> None:
    """launcher は注入されたものだけを使う（自前の台帳知識を持たない）。"""
    # Arrange
    monkeypatch.delenv("PYTHONPATH", raising=False)
    launcher = SubprocessJobLauncher(
        job_dir_of=lambda _id: tmp_path,
        repo_root=tmp_path,
        path_entries=lambda root: [root / "only-this"],
    )
    # Act
    import os

    got = launcher._child_env()["PYTHONPATH"].split(os.pathsep)
    # Assert
    assert got == [str(tmp_path / "only-this")]


# --------------------------------------------------------------------------------------
# 計算量検定（Test Spy・発行 − 使用 = 0）
# --------------------------------------------------------------------------------------
def _ledger_spy(monkeypatch) -> "list[Path]":
    """Composition Root が束縛する実読み手への発行を数える Spy を仕掛ける。"""
    import tools.install_dev_paths as ledger_module

    issued: "list[Path]" = []
    original = ledger_module.path_entries

    def _spy(root):
        issued.append(root)
        return original(root)

    monkeypatch.setattr(ledger_module, "path_entries", _spy)
    return issued


@pytest.mark.parametrize("apps_requested", [1, 4], ids=["wire_1", "wire_4"])
def test_wiring_the_app_issues_no_ledger_read(
    monkeypatch, tmp_path: Path, apps_requested: int
) -> None:
    """結線しただけでは台帳を読まない（アプリ 1 個 / 4 個の 2 点）。

    使用は 0（誰も子環境を組んでいない）なので、発行も 0 でなければ「作って捨てる」に
    なる。関数内 import・遅延読込がここで守られていることを回数で表明する。
    """
    # Arrange
    issued = _ledger_spy(monkeypatch)
    # Act
    apps = [
        build_sim_job_app(repo_root=tmp_path / f"r{i}", web_dir=tmp_path / f"r{i}" / "web")
        for i in range(apps_requested)
    ]
    # Assert
    assert len(apps) == apps_requested
    assert len(issued) - 0 == 0, f"結線だけで台帳を読みました: {issued}"


@pytest.mark.parametrize("builds_requested", [1, 8], ids=["build_1", "build_8"])
def test_the_bound_ledger_is_read_once_per_child_env(
    monkeypatch, tmp_path: Path, builds_requested: int
) -> None:
    """子環境の構築 1 回 / 8 回の 2 点で「台帳の読込 − 構築 = 0」。

    読込回数を焼き込まず、1 構築につき読み捨てが 0 であることだけを固定する。
    """
    # Arrange
    monkeypatch.delenv("PYTHONPATH", raising=False)
    issued = _ledger_spy(monkeypatch)
    app = build_sim_job_app(repo_root=tmp_path, web_dir=tmp_path / "web")
    # Act
    built = [app.launcher._child_env() for _ in range(builds_requested)]
    # Assert
    assert len(built) == builds_requested
    assert len(issued) - builds_requested == 0, (issued, builds_requested)
