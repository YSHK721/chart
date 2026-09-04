"""setup_worktree.sh が .pth の登録まで済ませること（ISSUE-482 残承認事項 (b)）。

解く問題:
    ISSUE-482 で .pth（tools/install_dev_paths.py）を導入したが、それを**呼ぶ運用手順**が
    リポジトリに無かった。結果として新しいコンテナ・新しい venv では
    tools/tests/test_cli_entrypoints_resolve_without_pythonpath.py の前提検査が
    赤で始まる。環境構築の 1 コマンド（setup_worktree.sh）が .pth の設置まで面倒を見る。

**最重要の安全性（本ファイルが存在する主たる理由）**:
    install_dev_paths.py は「自分の置かれたチェックアウト」（``__file__`` の parents[1]）を
    登録し、書き込み先は ``site.getsitepackages()[0]``＝**起動に使った python の venv** である。
    一方 setup_worktree.sh は worktree から起動され、venv は**本チェックアウトのもの**を指す。

    したがって worktree 側の install_dev_paths.py を本 venv の python で起動すると、
    **本チェックアウトの venv の .pth が worktree のパスで上書きされる**。以後、本
    チェックアウトで素の python を使うと worktree の実装が読まれる——共有資源を
    worktree から壊す事故であり、ISSUE-279 / ISSUE-363 と同型である
    （pyproject.toml が「絶対パスを書かないこと」と警告しているのと同じ壊れ方）。

    ゆえに起動するのは **MAIN_ROOT 側の** install_dev_paths.py でなければならない。
    そうすれば「venv とそれを所有するチェックアウト」の対応が保たれる（.pth の位置づけ
    ＝本チェックアウトでの対話シェル用フォールバック、という install_dev_paths.py の
    docstring とも整合する）。本ファイルはこの対応を機械的に固定する。

検定の方式:
    実 worktree は作らない。偽の「本チェックアウト」（venv python の身代わりが argv を
    記録する）と偽の worktree を tmp_path に組み、--main-root を明示して実行する。
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]
_SETUP = _REPO_ROOT / "tools" / "setup_worktree.sh"


def _make_main_root(base: Path, *, installer_exit: int = 0) -> "tuple[Path, Path]":
    """偽の本チェックアウトを組む。venv python の身代わりは argv を記録する。

    返すのは (main_root, argv 記録ファイル)。
    """
    main_root = base / "main"
    (main_root / "lightweight-charts-python-main" / ".venv" / "bin").mkdir(parents=True)
    (main_root / "data" / "marketdata").mkdir(parents=True)
    (main_root / "tools").mkdir(parents=True)
    # MAIN_ROOT 側にも install_dev_paths.py が居る（実体と同じ配置）。
    (main_root / "tools" / "install_dev_paths.py").write_text("", encoding="utf-8")

    log = base / "argv.log"
    py = main_root / "lightweight-charts-python-main" / ".venv" / "bin" / "python"
    py.write_text(
        "#!/usr/bin/env bash\n"
        f'printf "%s\\n" "$@" >> {log}\n'
        f"exit {installer_exit}\n",
        encoding="utf-8",
    )
    py.chmod(0o755)
    return main_root, log


def _make_worktree(base: Path) -> Path:
    """偽の worktree を組む（本物の setup_worktree.sh と install_dev_paths.py を置く）。"""
    tree = base / "wt"
    (tree / "tools").mkdir(parents=True)
    setup = tree / "tools" / "setup_worktree.sh"
    setup.write_text(_SETUP.read_text(encoding="utf-8"), encoding="utf-8")
    setup.chmod(0o755)
    # worktree 側にも同名スクリプトが存在する（これを起動してはならない）。
    (tree / "tools" / "install_dev_paths.py").write_text("", encoding="utf-8")
    return tree


def _run(tree: Path, main_root: Path) -> subprocess.CompletedProcess:
    env = dict(os.environ)
    env.pop("PYTHONPATH", None)
    return subprocess.run(
        [
            "bash",
            str(tree / "tools" / "setup_worktree.sh"),
            "--main-root",
            str(main_root),
        ],
        capture_output=True,
        text=True,
        timeout=60,
        env=env,
    )


# --------------------------------------------------------------------------------------
# 1. .pth の設置が手順に組み込まれている
# --------------------------------------------------------------------------------------
def test_setup_worktree_installs_the_dev_paths_pth(tmp_path: Path) -> None:
    """環境構築の 1 コマンドが .pth の登録まで済ませること。"""
    # Arrange
    main_root, log = _make_main_root(tmp_path)
    tree = _make_worktree(tmp_path)
    # Act
    proc = _run(tree, main_root)
    # Assert
    assert proc.returncode == 0, proc.stderr[-800:]
    assert log.exists(), (
        "venv python が一度も起動されていません（.pth の登録が手順に入っていない）。"
        f" stdout: {proc.stdout[-500:]}"
    )
    invoked = log.read_text(encoding="utf-8").split()
    assert any(a.endswith("install_dev_paths.py") for a in invoked), invoked


# --------------------------------------------------------------------------------------
# 2. 共有資源（本 venv）を worktree のパスで壊さない ← 本ファイルの中心
# --------------------------------------------------------------------------------------
def test_the_installer_that_runs_is_the_one_in_the_main_checkout(tmp_path: Path) -> None:
    """起動するのは MAIN_ROOT 側の install_dev_paths.py であること。

    worktree 側を起動すると、本 venv の .pth が worktree のパスで上書きされ、
    本チェックアウトの素の python が worktree の実装を読むようになる。
    """
    # Arrange
    main_root, log = _make_main_root(tmp_path)
    tree = _make_worktree(tmp_path)
    # Act
    _run(tree, main_root)
    # Assert
    invoked = [
        a
        for a in log.read_text(encoding="utf-8").split()
        if a.endswith("install_dev_paths.py")
    ]
    assert invoked, "install_dev_paths.py が起動されていません"
    for arg in invoked:
        assert Path(arg) == main_root / "tools" / "install_dev_paths.py", (
            "worktree 側の install_dev_paths.py を本 venv の python で起動しています。"
            " 本チェックアウトの .pth が worktree のパスで上書きされます（共有資源の破壊）。"
            f" 実際に渡された値: {arg}"
        )
        assert tree not in Path(arg).parents, arg


# --------------------------------------------------------------------------------------
# 2b. 計算量検定（Test Spy・発行 − 使用 = 0）
# --------------------------------------------------------------------------------------
def test_one_setup_run_launches_the_installer_exactly_once(tmp_path: Path) -> None:
    """1 回の環境構築で登録器の起動は 1 回だけ（捨てる起動を作らない）。

    固定するのは「無駄の不在」であって実装詳細ではない。登録は冪等なので複数回
    呼んでも結果は同じになり、**出力の正しさでは検出できない**（状態検証では
    原理的に落ちない類の浪費）。起動回数を Test Spy で数えて差を 0 に固定する。
    """
    # Arrange
    main_root, log = _make_main_root(tmp_path)
    tree = _make_worktree(tmp_path)
    # Act
    _run(tree, main_root)
    # Assert（登録に必要な起動は 1 回）
    launched = [
        a
        for a in log.read_text(encoding="utf-8").split()
        if a.endswith("install_dev_paths.py")
    ]
    required = 1
    assert len(launched) - required == 0, launched


# --------------------------------------------------------------------------------------
# 3. 既存の責務（環境変数ファイルの生成）は不変（加法であること）
# --------------------------------------------------------------------------------------
def test_the_env_file_is_still_generated_unchanged(tmp_path: Path) -> None:
    """.pth の追加は加法であり、従来の生成物が指す値を変えないこと。

    生成物の**文字列**ではなく、それを source して得られる**値**で検定する
    （書式を変えただけで落ちる／中身が壊れても通る、のどちらにもならない）。
    """
    # Arrange
    main_root, _ = _make_main_root(tmp_path)
    tree = _make_worktree(tmp_path)
    # Act
    proc = _run(tree, main_root)
    assert proc.returncode == 0, proc.stderr[-800:]
    sourced = subprocess.run(
        [
            "bash",
            "-c",
            f'source "{tree / "dev_paths.local.sh"}" '
            '&& printf "%s\\n%s\\n" "$VENV_PYTHON" "$MARKETDATA_DATA_DIR"',
        ],
        capture_output=True,
        text=True,
        timeout=60,
    )
    # Assert
    assert sourced.returncode == 0, sourced.stderr[-500:]
    venv_python, data_dir = sourced.stdout.splitlines()
    assert Path(venv_python) == (
        main_root / "lightweight-charts-python-main" / ".venv" / "bin" / "python"
    )
    assert Path(data_dir) == main_root / "data" / "marketdata"


# --------------------------------------------------------------------------------------
# 4. 登録に失敗しても環境構築そのものは完了扱いにし、手当てを名指しする
# --------------------------------------------------------------------------------------
def test_a_failing_installer_warns_but_does_not_abort_the_setup(tmp_path: Path) -> None:
    """.pth の登録失敗で環境構築を失敗扱いにしないこと（ただし黙らない）。

    .pth は install_dev_paths.py 自身の docstring が言うとおり**権威ではなく
    フォールバック**である。権威である 2 経路（serve.sh → dev_paths.sh、
    pytest → pyproject の pythonpath）は .pth の失敗に影響されないため、
    生成に成功した環境変数ファイルまで失敗扱いにするのは過剰である。
    一方で黙って続けると ISSUE-482 の「赤で始まる」が再現するので、
    打つべきコマンドを名指しする。
    """
    # Arrange
    main_root, _ = _make_main_root(tmp_path, installer_exit=1)
    tree = _make_worktree(tmp_path)
    # Act
    proc = _run(tree, main_root)
    # Assert
    assert proc.returncode == 0, "登録失敗で環境構築全体を落としています"
    combined = proc.stdout + proc.stderr
    assert "install_dev_paths.py" in combined, (
        "登録に失敗したのに、手当ての方法を出力していません（黙って続けている）"
    )


# --------------------------------------------------------------------------------------
# 5. 前提検査の失敗メッセージが「新しい venv では赤で始まる」を説明すること
# --------------------------------------------------------------------------------------
def test_the_prerequisite_check_explains_the_fresh_venv_case() -> None:
    """ISSUE-482 (b): 新しいコンテナ・新しい venv で赤くなる事象を失敗文言が説明する。

    この検定が無いと「赤の意味が分からず環境が壊れたと誤診する」状態に戻る。
    名指しすべきは (1) 新しい venv/コンテナで起こること (2) 1 コマンドの直し方。

    ソース文字列ではなく、失敗文言を組み立てる**値そのもの**を import して検定する。
    """
    from tools.tests.test_cli_entrypoints_resolve_without_pythonpath import (
        _FRESH_ENV_NOTE,
        _INSTALL_HINT,
    )

    assert "install_dev_paths.py" in _INSTALL_HINT
    assert "setup_worktree.sh" in _INSTALL_HINT, (
        "直し方として環境構築の 1 コマンドを名指ししていません"
    )
    assert any(w in _FRESH_ENV_NOTE for w in ("新しい venv", "新しいコンテナ")), (
        "「新しい環境では赤で始まる」ことを説明していません"
    )
    assert "退行ではありません" in _FRESH_ENV_NOTE, (
        "赤をコードの退行と誤診させない説明がありません"
    )
