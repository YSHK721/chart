"""文書化された CLI 起動手順が素の venv python で成立することを固定する（ISSUE-482）。

なぜ必要か（スイートは緑のまま人間の手順だけ壊れる欠陥）:
    ISSUE-479 Wave2 フェーズ 2-7 は、CLI 群の実行時 sys.path 書き換えを撤去して解決を
    台帳（tools/dev_paths.txt）へ一本化する。ところがそれらの CLI を**スクリプトとして
    起動する**検定は 1 本も無かった。撤去してもスイートは緑のままで、docstring に書かれた
    手順どおり人間が叩いたときにだけ ModuleNotFoundError で死ぬ——本 Wave が消そうと
    している欠陥と同型である。撤去の可否ではなく、**起動できること**を検定に落とす。

前提（skipif ではなく前提検査として明示する）:
    解決の唯一源は台帳であり、素の python へその値を届ける機構は venv の
    `jp225_chart_paths.pth`（`tools/install_dev_paths.py` が書く）である。この .pth が
    無い環境では本ファイルの検定はすべて赤になる。条件付きスキップにはしない——
    スキップは「前提が無い」ことを緑で覆い隠し、ISSUE-482 の失敗（手順が暗黙知として
    失われていた）をそのまま再生産するからである。前提そのものを
    `test_the_ledger_entries_are_visible_to_a_bare_interpreter` が名指しで検査する。

実測（2026-09-03・.pth 未導入時）:
    `simulator/tools/` の 6 本（optimize_cli / run_is_oos_cli / run_scan_contacts_cli /
    walk_forward_cli / regenerate_account_engine_fixtures / export_trade_markers）は
    **撤去前から既に** ModuleNotFoundError で起動できなかった。.pth はこの 6 本も同時に
    直す（撤去の副作用を防ぐだけの措置ではない）。

副作用を出さない起動形:
    `--help` は使えない。`simulator/tools/export_account_engine_fixtures.py` は引数を
    解釈せず fixture を再生成する（ISSUE-482 付記の実測）。検定が本番の成果物を書き換えて
    しまうため、モジュール本体だけを実行し __main__ ガードの内側へは入らない形で起動する。
    import 解決の失敗は本体の実行中に起きるので、これで検出したいものは全部見える。

計算量検定（絶対命令 2026-08-28）: 起動発行 − 検査した CLI 数 = 0。対象 1 件 / 2 件の
    2 点で、発行が対象数だけで決まることを固定する（回数リテラルは焼き込まない）。
"""

from __future__ import annotations

import ast
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]
_LEDGER = _REPO_ROOT / "tools" / "dev_paths.txt"

#: CLI を置いているディレクトリ（撤去対象の 2 スライス）。
_CLI_DIRS = (
    _REPO_ROOT / "simulator" / "tools",
    _REPO_ROOT / "indigators" / "indicator_ui" / "tools",
)

#: `python <script>` で起動される入口の印。ライブラリモジュールを巻き込まない。
_ENTRY_POINT_MARK = '__name__ == "__main__"'

#: 起動を促す文言（前提が欠けたときに人間が打つべきコマンド）。
#
#: **新しいコンテナ・新しい venv では本検定は赤で始まる**（ISSUE-482）。これは仕様であり、
#: 前提の不在を緑で覆い隠さないための設計である。壊れているのは環境であってコードではない。
#: 通常は環境構築の 1 コマンド ./tools/setup_worktree.sh が .pth の登録まで済ませるので、
#: それを実行していないだけのことが多い。
_INSTALL_HINT = (
    "./tools/setup_worktree.sh（環境構築の 1 コマンド・.pth の登録まで行う）"
    " / 個別に打つなら <venv>/bin/python tools/install_dev_paths.py"
)

#: 赤の意味を取り違えないための説明（環境が壊れたと誤診させない）。
_FRESH_ENV_NOTE = (
    "新しい venv・新しいコンテナでは、この検定は .pth を登録するまで赤で始まります"
    "（前提の不在を緑で隠さない設計）。コードの退行ではありません。"
)

#: モジュール本体だけを実行する起動形（__main__ ガードの内側へは入らない）。
#: スクリプト起動と同じく、スクリプトのあるディレクトリを sys.path の先頭へ置く。
_PROBE = (
    "import os, runpy, sys\n"
    "script = sys.argv[1]\n"
    "sys.path.insert(0, os.path.dirname(os.path.abspath(script)))\n"
    "runpy.run_path(script, run_name='__cli_import_probe__')\n"
)

#: 起動オプション。`-I`（isolated）が要る理由は**検出力**である。素の `-c` は cwd を
#: sys.path の先頭へ置くため、リポジトリ根から起動しただけで `import simulator` が
#: 通ってしまい、台帳の解決が壊れていても緑になる（実測: 本検定の初版が
#: regenerate_account_engine_fixtures の失敗を取り逃した）。実際の `python <script>` は
#: cwd ではなく**スクリプトのあるディレクトリ**を先頭へ置く。`-I` で cwd と呼出側の
#: 環境を落とし、上の `_PROBE` がスクリプトのディレクトリを明示的に置くことで、
#: 起動条件を実物へ揃える。site-packages の .pth は `-I` でも読まれる（＝前提は効く）。
_PROBE_FLAGS = ("-I",)


def _ledger_entries() -> "list[str]":
    """台帳の有効行を絶対パスへ解決する（検定側も値を書き写さない）。"""
    out: "list[str]" = []
    for raw in _LEDGER.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if line and not line.startswith("#"):
            out.append(str(_REPO_ROOT if line == "." else _REPO_ROOT / line))
    return out


def _cli_scripts() -> "list[Path]":
    """対象ディレクトリ配下の入口スクリプト（__main__ ガードを持つもの）。

    表を手書きしない。手書きすると CLI を 1 本足したときに検査から漏れ、
    「テストは緑だが手順は死ぬ」がその 1 本で再発する。
    """
    return sorted(
        path
        for directory in _CLI_DIRS
        for path in directory.glob("*.py")
        if _ENTRY_POINT_MARK in path.read_text(encoding="utf-8")
    )


def _bare_env() -> "dict[str, str]":
    """呼出側の PYTHONPATH を持ち込まない環境（人間が素のシェルで叩く条件）。"""
    return {k: v for k, v in os.environ.items() if k != "PYTHONPATH"}


def _probe(script: Path) -> subprocess.CompletedProcess:
    """CLI を 1 本起動する（起動の単一の入口＝計算量検定が発行を数える点）。"""
    return subprocess.run(
        [sys.executable, *_PROBE_FLAGS, "-c", _PROBE, str(script)],
        cwd=str(_REPO_ROOT),
        env=_bare_env(),
        capture_output=True,
        text=True,
        timeout=180,
    )


def _probe_all(scripts: "list[Path]") -> "list[subprocess.CompletedProcess]":
    return [_probe(script) for script in scripts]


# --------------------------------------------------------------------------------------
# 前提（.pth）— 条件付きスキップにせず、名指しで検査する
# --------------------------------------------------------------------------------------
def test_the_scan_finds_the_cli_entry_points() -> None:
    """走査が空なら本ファイルの検定は恒真式に退化する（ゲートの自己検査）。"""
    found = {str(path.relative_to(_REPO_ROOT)) for path in _cli_scripts()}
    assert "simulator/tools/export_account_engine_fixtures.py" in found
    assert "indigators/indicator_ui/tools/export_jp225_m1.py" in found


def test_the_ledger_entries_are_visible_to_a_bare_interpreter() -> None:
    """台帳の全エントリが、素の venv python の sys.path に載っている。

    これが本ファイルの他の検定すべての前提である。満たされないのは環境構築の
    手順が実行されていないときであり、直し方は 1 つしかないのでそれを名指しする。
    """
    # Arrange / Act（-I: 呼出側の PYTHONPATH も cwd も混ぜない＝site 設定だけを見る）
    proc = subprocess.run(
        [sys.executable, "-I", "-c", "import json, sys; print(json.dumps(sys.path))"],
        cwd=str(_REPO_ROOT),
        env=_bare_env(),
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert proc.returncode == 0, proc.stderr[-800:]
    path = json.loads(proc.stdout)
    # Assert
    missing = [entry for entry in _ledger_entries() if entry not in path]
    assert missing == [], (
        f"台帳のエントリが素の python から見えません: {missing}\n"
        f"  {_FRESH_ENV_NOTE}\n"
        f"  この環境では次を 1 回実行してください: {_INSTALL_HINT}\n"
        f"  素の sys.path: {path}"
    )


# --------------------------------------------------------------------------------------
# 文書化された起動手順（1 CLI = 1 起動）
# --------------------------------------------------------------------------------------
@pytest.mark.parametrize(
    "script", _cli_scripts(), ids=lambda p: f"{p.parent.name}/{p.stem}"
)
def test_the_cli_resolves_its_imports_when_launched_as_a_script(script: Path) -> None:
    """PYTHONPATH 無しで起動しても import が解決する（ModuleNotFoundError にならない）。

    識別力: 台帳の解決（.pth）を外し、かつ CLI 側の実行時 sys.path 書き換えも無い状態に
    すると `No module named 'simulator'` / `'marketdata'` で赤になる（ISSUE-482 実測）。
    """
    # Act
    proc = _probe(script)
    # Assert
    assert proc.returncode == 0, (
        f"{script.relative_to(_REPO_ROOT)} が素の python で起動できません。\n"
        f"  stderr（末尾）: {proc.stderr[-800:]}\n"
        f"  前提が欠けている場合の対処: {_INSTALL_HINT}"
    )
    assert "ModuleNotFoundError" not in proc.stderr, proc.stderr[-800:]


def test_the_launch_puts_the_script_directory_first_and_not_the_working_directory(
    tmp_path: Path,
) -> None:
    """起動形の自己検査: 先頭は**スクリプトのディレクトリ**で、cwd は載らない。

    実測（本検定の初版の欠陥）: 素の `-c` は cwd を sys.path の先頭へ置くため、
    リポジトリ根から起動しただけで `import simulator` が通り、台帳の解決が壊れていても
    緑になった。検出力そのものを検定で固定する。
    """
    # Arrange
    script = tmp_path / "show_path.py"
    script.write_text("import json, sys\nprint(json.dumps(sys.path))\n", encoding="utf-8")
    # Act
    proc = _probe(script)
    # Assert
    assert proc.returncode == 0, proc.stderr[-800:]
    path = json.loads(proc.stdout)
    assert path[0] == str(tmp_path), path
    assert "" not in path, f"cwd が探索パスに載っています（検出力が消えます）: {path}"


def test_launching_the_module_body_does_not_run_the_command(tmp_path: Path) -> None:
    """起動形の自己検査: __main__ ガードの内側は実行されない（本番成果物を書き換えない）。

    これが崩れると本ファイルは検定ではなく「fixture 再生成器」になる（ISSUE-482 付記）。
    """
    # Arrange
    script = tmp_path / "guarded.py"
    script.write_text(
        "import pathlib\n"
        "pathlib.Path(__file__).with_name('body.txt').write_text('body')\n"
        'if __name__ == "__main__":\n'
        "    pathlib.Path(__file__).with_name('main.txt').write_text('main')\n",
        encoding="utf-8",
    )
    # Act
    proc = _probe(script)
    # Assert
    assert proc.returncode == 0, proc.stderr[-800:]
    assert (tmp_path / "body.txt").exists(), "本体が実行されていません（検査が空振り）"
    assert not (tmp_path / "main.txt").exists(), "ガードの内側まで実行されました"


# --------------------------------------------------------------------------------------
# 構造（実行時 sys.path 書き換えを持たない）
# --------------------------------------------------------------------------------------
#: 実行時 sys.path 書き換えを維持してよい唯一の CLI と、その理由。
#: `indicator_ui/api` が挿すのは adapter / framework / domain という**汎用名**であり、
#: スライス間で衝突するため台帳へ載せられない（載せてよいのは衝突しない固有名だけ）。
#: 同じ規律で replay_ui の bridge も _ensure_paths を維持している。
_PATH_REWRITE_EXEMPTIONS = {
    "indigators/indicator_ui/tools/prototype_inject_marketdata.py": (
        "api が挿すのは汎用名（adapter/framework/domain）で台帳へ載せられない（bridge と同規律）"
    ),
}


def _rewrites_sys_path(path: Path) -> bool:
    """`sys.path.insert` / `sys.path.append` の呼出を持つか（AST・コメントは対象外）。"""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
            continue
        if node.func.attr not in {"insert", "append", "extend"}:
            continue
        target = node.func.value
        if (
            isinstance(target, ast.Attribute)
            and target.attr == "path"
            and isinstance(target.value, ast.Name)
            and target.value.id == "sys"
        ):
            return True
    return False


def test_the_detector_sees_a_synthetic_path_rewrite(tmp_path: Path) -> None:
    """検出力の自己検査（違反 2 形態を検出し、非違反を誤検出しない）。"""
    for source in ("import sys\nsys.path.insert(0, 'x')\n", "import sys\nsys.path.append('x')\n"):
        sample = tmp_path / "offender.py"
        sample.write_text(source, encoding="utf-8")
        assert _rewrites_sys_path(sample) is True, source
    innocent = tmp_path / "innocent.py"
    innocent.write_text(
        "import sys\nprint(sys.path)\nxs = []\nxs.insert(0, 'x')\n", encoding="utf-8"
    )
    assert _rewrites_sys_path(innocent) is False


def test_the_cli_entry_points_do_not_rewrite_sys_path_at_runtime() -> None:
    """解決は台帳が持つ。CLI 側で書き換えると起動位置で解決先が変わる（ISSUE-279）。"""
    offenders = sorted(
        str(path.relative_to(_REPO_ROOT))
        for path in _cli_scripts()
        if _rewrites_sys_path(path)
    )
    assert offenders == sorted(_PATH_REWRITE_EXEMPTIONS), (
        f"実行時 sys.path 書き換えを持つ CLI: {offenders}\n"
        f"  許される例外は次の 1 件のみ: {_PATH_REWRITE_EXEMPTIONS}"
    )


# --------------------------------------------------------------------------------------
# 計算量検定（Test Spy・発行 − 使用 = 0）
# --------------------------------------------------------------------------------------
@pytest.mark.parametrize("targets_requested", [1, 2], ids=["probe_1", "probe_2"])
def test_each_cli_is_launched_exactly_once(monkeypatch, targets_requested: int) -> None:
    """対象 1 件 / 2 件の 2 点で「起動発行 − 検査した CLI 数 = 0」。

    起動回数そのものを期待値に焼き込まない。固定するのは無駄の不在——同じ CLI を
    2 度立ち上げて片方を捨てる形になっていないことだけである。
    """
    # Arrange
    launched: "list[str]" = []

    def _spy(argv, **kwargs):
        launched.append(argv[-1])
        return subprocess.CompletedProcess(argv, 0, "", "")

    monkeypatch.setattr(subprocess, "run", _spy)
    scripts = _cli_scripts()[:targets_requested]
    # Act
    results = _probe_all(scripts)
    # Assert
    assert len(results) == len(scripts)
    assert len(launched) - len(scripts) == 0, launched
