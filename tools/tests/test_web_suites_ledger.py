"""フロント（JS）テストスイート台帳と実在スイートの一致を強制する（ISSUE-280）。

なぜ必要か（実測）:
    ``unified_ui/web`` の ``node_modules`` が自己参照 symlink になり ``npm test`` が
    **exit 216・出力なし**で落ちる状態が丸一日気付かれなかった（2026-08-08）。スイートが
    スライスごとに散在し、起動方法も ``npm test`` と生の ``node --test`` が混在していたため、
    「1 つ実行されなくなった」ことが構造的に見えなかった。

本テストが固定する不変条件:
    1. ``tests/*.test.js`` を持つ web ディレクトリは、すべて台帳に載っている（新設スイートの
       登録漏れ＝静かな未実行を防ぐ）。
    2. 台帳の各スイートは ``npm test`` で起動できる（``package.json`` の ``scripts.test`` を持つ）
       ＝起動方法が 1 つに揃っている。
    3. 一括ランナーは台帳から読む（スイート名の写しを持たない）。

本テストはスイート自体を実行しない（実行は ``tools/run_web_tests.sh``）。ここで固定するのは
「どのスイートが存在し、どう起動するか」の構造だけである。
"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[2]
_LEDGER = _ROOT / "tools" / "web_suites.txt"

#: 台帳の対象外（理由つきで明示する。暗黙の除外を作らない）。
#:   prototype_*: 試作＝参照実装であり回帰対象ではない。
_EXCLUDED_PREFIXES = ("prototype_",)


def _ledger_entries() -> "list[str]":
    out: "list[str]" = []
    for raw in _LEDGER.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if line and not line.startswith("#"):
            out.append(line)
    return out


def _discovered_suites() -> "list[str]":
    """``<...>/web/tests/*.test.js`` を持つディレクトリを走査で見つける。"""
    found: "set[str]" = set()
    for path in _ROOT.glob("*/**/web/tests/*.test.js"):
        # 判定は **_ROOT からの相対**成分で行う。絶対パス成分を見ると、
        # `.claude/worktrees/<name>/` 配下の worktree で全件が「隠しディレクトリ」と
        # 判定され、走査結果が常に空になる（＝台帳検定が worktree で機能しない）。
        relative_parts = path.relative_to(_ROOT).parts
        if any(part == "node_modules" or part.startswith(".") for part in relative_parts):
            continue
        web_dir = path.parent.parent
        rel = web_dir.relative_to(_ROOT).as_posix()
        if rel.startswith(_EXCLUDED_PREFIXES):
            continue
        found.add(rel)
    return sorted(found)


def test_every_discovered_suite_is_registered():
    """走査で見つかるスイートと台帳が一致する（登録漏れ・陳腐化の双方を検出）。"""
    assert sorted(_ledger_entries()) == _discovered_suites(), (
        "web スイート台帳（tools/web_suites.txt）と実在スイートが食い違います。"
        " 新設したら台帳へ追加し、廃止したら台帳から外してください。"
    )


@pytest.mark.parametrize("suite", _ledger_entries())
def test_suite_is_launchable_by_npm_test(suite: str):
    """各スイートが ``npm test`` で起動できる（起動方法を 1 つに揃える）。"""
    pkg = _ROOT / suite / "package.json"
    assert pkg.exists(), f"{suite}/package.json がありません（npm test で起動できません）"
    scripts = json.loads(pkg.read_text(encoding="utf-8")).get("scripts") or {}
    assert scripts.get("test"), f"{suite}/package.json に scripts.test がありません"


@pytest.mark.parametrize("suite", _ledger_entries())
def test_suite_dependencies_are_usable(suite: str):
    """依存の置き場が壊れていない（自己参照 symlink＝本 ISSUE の実体を検出する）。

    ``node_modules`` は無くてもよい（node:test だけで動くスイートがある）。あるなら
    **実ディレクトリ**であること。自分自身を指す symlink は解決不能で、``npm test`` が
    出力なしのまま非ゼロ終了する。
    """
    nm = _ROOT / suite / "node_modules"
    if not nm.is_symlink() and not nm.exists():
        return
    assert nm.is_dir(), f"{suite}/node_modules が実ディレクトリではありません（壊れた symlink）"
    if nm.is_symlink():
        assert nm.resolve() != nm.parent / "node_modules", (
            f"{suite}/node_modules が自分自身を指しています（ISSUE-280 の再発）"
        )


def _tracked_files(pattern: str) -> "list[str]":
    out = subprocess.run(
        ["git", "ls-files", pattern], cwd=str(_ROOT),
        capture_output=True, text=True, check=True,
    )
    return [line for line in out.stdout.splitlines() if line]


def test_node_modules_is_not_under_version_control():
    """``node_modules`` が版管理に入っていない（ISSUE-280 の真因そのもの）。

    実際に起きたこと: ``unified_ui/.gitignore`` の ``web/node_modules/`` は**末尾スラッシュ付き
    ＝ディレクトリのみ**を無視する規則だったため、同名の **symlink** が無視から漏れ、
    自分自身を指す壊れた symlink が commit された（d8abde6）。以後 clone / worktree の
    どこでも web テストが exit 216 で動かない状態が配布されていた。
    """
    tracked = _tracked_files("*node_modules*")
    assert not tracked, f"node_modules が追跡されています: {tracked}"


@pytest.mark.parametrize("suite", _ledger_entries())
def test_suite_manifest_is_under_version_control(suite: str):
    """各スイートの ``package.json`` が追跡されている（clean clone で起動できる条件）。

    ルート ``.gitignore`` の blanket ``*.json``（データファイル向け）に巻き込まれると、
    lockfile だけ commit され manifest が無い状態になり、clean clone で ``npm ci`` が
    成立しない（実際 ``unified_ui/web/package.json`` がその状態だった）。
    """
    rel = f"{suite}/package.json"
    assert _tracked_files(rel) == [rel], (
        f"{rel} が追跡されていません（.gitignore の除外規則を確認してください）"
    )


def test_runner_reads_the_ledger():
    """一括ランナーが台帳から読む（スイート名の写しを持たない）。"""
    src = (_ROOT / "tools" / "run_web_tests.sh").read_text(encoding="utf-8")
    assert "web_suites.txt" in src, "run_web_tests.sh が台帳を読んでいません"
    # 判定対象は**実行される行**のみ（コメントの説明文に事例としてスイート名が出るのは正当）。
    code = "\n".join(
        line for line in src.splitlines() if not line.lstrip().startswith("#")
    )
    for suite in _ledger_entries():
        assert suite not in code, (
            f"run_web_tests.sh にスイート名の写しがあります: {suite}（台帳から読んでください）"
        )
