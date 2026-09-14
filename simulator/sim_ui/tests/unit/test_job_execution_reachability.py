"""実行到達性ゲート（Phase 9 段階 2・§19.5）: 「受付拒否＝実行到達 0」の不変条件。

段階 2 の裁定は、受付（`SubmitJobInteractor.execute`）で拒否した投入が台帳にも
子プロセスにも 1 度も届かないことを前提にしている。その前提は `execute` の本文を
読めば今は成立しているが、**別の入口が後から生えたら黙って崩れる**（例: controller
や framework が直接 launcher を叩く近道を足す）。本検定はその崩れを構造で検出する。

固定する不変条件:
    1. sim_ui の本番コード（`tests/` を除く）で `JobLauncherPort.launch` を呼ぶ箇所は
       `usecase/submit_job.py` の **1 箇所だけ**である。
    2. 同じく `JobLedgerPort.create` を呼ぶ箇所も `usecase/submit_job.py` の 1 箇所だけ。
    3. 検出器そのものが働くこと（自己検査）——別ファイル相当の合成ソースに違反形
       （`self._launcher.launch(...)` / `self._ledger.create(...)`）を置くと検出される。
    4. 走査が空振りしていないこと——走査対象ファイルが実際に複数存在する。

判定は AST で行う（文字列 grep はコメント・文字列リテラル中の記述を拾う）。検出は
**属性名**で行う: 呼び先の型を静的に決めることはできないので、同名の属性呼出を
すべて拾う（過検出側に倒す＝ゲートとして安全側）。現状 sim_ui の本番コードに
`.launch(` / `.create(` は上記 2 箇所しか無く、過検出は 0 である（実測）。
"""
from __future__ import annotations

import ast
from pathlib import Path

#: sim_ui の本番コード根（`tests/` を除いた全 .py が走査対象）。
_SIM_UI = Path(__file__).resolve().parents[2]

#: 実行到達点の属性名（Port の抽象メソッド名）。
_LAUNCH = "launch"
_CREATE = "create"

#: 受付の唯一の入口（この 1 ファイルだけが実行到達点を持ってよい）。
_ONLY_CALLER = "usecase/submit_job.py"


def _production_files() -> "list[Path]":
    """sim_ui の本番 .py（`tests/` と `__pycache__` を除く）を列挙する。"""
    return sorted(
        path
        for path in _SIM_UI.rglob("*.py")
        if "tests" not in path.relative_to(_SIM_UI).parts
        and "__pycache__" not in path.parts
    )


def call_sites(source: str, attr: str) -> "list[int]":
    """``source`` 中の ``*.attr(...)`` 呼出の行番号を返す（AST 判定）。"""
    tree = ast.parse(source)
    return [
        node.lineno
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == attr
    ]


def _production_call_sites(attr: str) -> "list[tuple[str, int]]":
    found: "list[tuple[str, int]]" = []
    for path in _production_files():
        rel = path.relative_to(_SIM_UI).as_posix()
        for lineno in call_sites(path.read_text(encoding="utf-8"), attr):
            found.append((rel, lineno))
    return found


# --- 1. 走査が空振りしていないこと -------------------------------------------

def test_走査対象の本番ファイルが実在する() -> None:
    # Arrange / Act
    files = _production_files()
    # Assert: 射程の穴（0 件走査で全検定が緑）を遮断する
    assert len(files) >= 10, f"走査対象が少なすぎる（射程の穴の疑い）: {len(files)} 件"
    assert any(f.relative_to(_SIM_UI).as_posix() == _ONLY_CALLER for f in files)


# --- 2. 実行到達点は submit_job の各 1 箇所だけ --------------------------------

def test_launchの本番呼出はsubmit_jobの1箇所だけ() -> None:
    # Arrange / Act
    sites = _production_call_sites(_LAUNCH)
    # Assert
    assert len(sites) == 1, f"launch の本番呼出が 1 箇所ではない: {sites}"
    assert sites[0][0] == _ONLY_CALLER, f"launch の呼出元が受付以外にある: {sites}"


def test_createの本番呼出はsubmit_jobの1箇所だけ() -> None:
    # Arrange / Act
    sites = _production_call_sites(_CREATE)
    # Assert
    assert len(sites) == 1, f"create の本番呼出が 1 箇所ではない: {sites}"
    assert sites[0][0] == _ONLY_CALLER, f"create の呼出元が受付以外にある: {sites}"


# --- 3. 検出器の自己検査（違反形を検出できること） ------------------------------

_VIOLATION = '''
class Sneaky:
    def go(self, job_id):
        self._ledger.create(job_id)
        self._launcher.launch(job_id)
'''

_CLEAN = '''
class Innocent:
    def go(self, job_id):
        return self._ledger.load(job_id)
'''


def test_検出器は別ファイル相当の違反形を検出する() -> None:
    # Arrange: 受付以外のファイルに実行到達点が生えた形（合成 AST）
    # Act
    launch_sites = call_sites(_VIOLATION, _LAUNCH)
    create_sites = call_sites(_VIOLATION, _CREATE)
    # Assert
    assert len(launch_sites) == 1, "launch の違反形を検出できない（検出器が働いていない）"
    assert len(create_sites) == 1, "create の違反形を検出できない（検出器が働いていない）"


def test_検出器は無関係な呼出を拾わない() -> None:
    # Arrange / Act / Assert: 過検出で常時赤になる検出器ではない
    assert call_sites(_CLEAN, _LAUNCH) == []
    assert call_sites(_CLEAN, _CREATE) == []


def test_検出器はコメントと文字列リテラルを拾わない() -> None:
    # Arrange: grep なら拾う記述（AST では拾わない）
    source = '"""self._launcher.launch(x)"""\nX = "self._ledger.create(y)"  # .launch(z)\n'
    # Act / Assert
    assert call_sites(source, _LAUNCH) == []
    assert call_sites(source, _CREATE) == []
