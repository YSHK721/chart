"""禁止 git コマンドの遮断フックが、実際に遮断することを固定する（ISSUE-450）。

なぜ必要か:
    CLAUDE.md の「禁止事項」は文章であり、文章は守られないことがある。実際 ISSUE-363 では
    worktree の symlink を ``git add -A`` で巻き込んでコミットし、本チェックアウトの
    venv・データ参照を壊した。**規約は宣言でなく機械的検査で強制する**という裁定
    （2026-08-11）を、PreToolUse フックという実行前の遮断へ落とした。

本テストが固定する不変条件:
    1. 禁止形は必ず deny になる（見逃しゼロ）。
    2. 正常な git 操作は通る（誤遮断ゼロ）。誤遮断するフックは外され、結局守られなくなる。
    3. 配線が生きている（settings.json の PreToolUse/Bash がこのスクリプトを指している）。

構造: Arrange-Act-Assert（AAA）。
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[2]
_HOOK = _ROOT / ".claude" / "scripts" / "block_destructive_git.py"
_SETTINGS = _ROOT / ".claude" / "settings.json"

#: 遮断されなければならない形（CLAUDE.md 禁止事項）。複合コマンド・前置き付きも含む。
_MUST_DENY = [
    "git add -A",
    "git add --all",
    "git add .",
    "git add --all .",
    "git add -A .",
    "cd /tmp && git add -A",
    "ls; git add .",
    "git checkout -- foo.py",
    "git checkout -- .",
    "git restore foo.py",
    "git restore --staged foo.py",
    "git reset --hard",
    "git reset --hard origin/main",
    "git stash",
    "git stash push -m wip",
    "sudo git stash",
    "git -C /workspaces/app add -A",
]

#: 通らなければならない形（誤遮断は運用を壊す）。
_MUST_ALLOW = [
    "git add ISSUE.md",
    "git add ./tools/x.py",
    "git add tools/ marketdata/",
    "git status",
    "git diff --cached --stat",
    "git commit -m 'x'",
    "git log --oneline -5",
    "git reset HEAD~1",
    "git reset",
    "git checkout main",
    "git checkout -b feature/x",
    "ls -la",
    "echo 'git stash は禁止です'",
    "python3 -m pytest tools/tests",
    # ヒアドキュメント本文は「渡すデータ」であって実行するコマンドではない。
    #   取り除かずに行で割ると、禁止形と *書いてある文章* を実行だと誤判定する。
    #   実際に本フックが自分のコミットメッセージを遮断した（2026-08-28 実測）。
    "git commit -F - <<'EOF'\nfix: git add -A の誤用を直す\n\ngit add . も git stash も禁止である。\nEOF",
    "cat <<'EOF' > /tmp/note.md\ngit reset --hard は使わない\ngit restore も使わない\nEOF",
    "python3 - <<EOF\nprint('git checkout -- x')\nEOF",
    "cat <<-'DOC'\n\tgit stash push\nDOC",
]


def _run(command: str) -> "dict | None":
    """フックへ PreToolUse ペイロードを渡し、返った判定 JSON（無ければ None）を返す。"""
    payload = json.dumps({"tool_name": "Bash", "tool_input": {"command": command}})
    proc = subprocess.run([sys.executable, str(_HOOK)], input=payload,
                          capture_output=True, text=True, timeout=30)
    assert proc.returncode == 0, f"フックが異常終了した: {proc.stderr}"
    out = proc.stdout.strip()
    return json.loads(out) if out else None


@pytest.mark.parametrize("command", _MUST_DENY)
def test_forbidden_commands_are_denied(command: str) -> None:
    """禁止形は deny になり、理由が返る。"""
    decision = _run(command)

    assert decision is not None, f"遮断されなかった: {command!r}"
    specific = decision["hookSpecificOutput"]
    assert specific["hookEventName"] == "PreToolUse"
    assert specific["permissionDecision"] == "deny", command
    assert specific["permissionDecisionReason"].strip(), "理由が空では何を直せばよいか分からない"


@pytest.mark.parametrize("command", _MUST_ALLOW)
def test_ordinary_commands_are_not_blocked(command: str) -> None:
    """正常な操作は通る（誤遮断ゼロ）。"""
    assert _run(command) is None, f"誤って遮断された: {command!r}"


def test_forbidden_command_after_a_heredoc_is_still_denied() -> None:
    """本文を飛ばしたあとの**実コマンド**は、これまでどおり遮断される。

    ヒアドキュメント除去が「終端以降も見なくなる」実装だと、見逃しへ反転する。
    誤遮断を潰す修正が見逃しを生んでいないことを、ここで固定する。
    """
    command = "cat <<'EOF' > /tmp/x\ngit add -A と書いてあるだけ\nEOF\ngit stash"

    decision = _run(command)

    assert decision is not None, "終端後の実コマンドを見逃した"
    assert decision["hookSpecificOutput"]["permissionDecision"] == "deny"


def test_non_bash_tools_are_ignored() -> None:
    """Bash 以外のツールには介入しない。"""
    payload = json.dumps({"tool_name": "Edit", "tool_input": {"command": "git add -A"}})
    proc = subprocess.run([sys.executable, str(_HOOK)], input=payload,
                          capture_output=True, text=True, timeout=30)

    assert proc.returncode == 0 and proc.stdout.strip() == ""


def test_malformed_input_does_not_block_work() -> None:
    """入力が壊れていても作業を止めない（フック自身が事故源にならない）。"""
    proc = subprocess.run([sys.executable, str(_HOOK)], input="not json",
                          capture_output=True, text=True, timeout=30)

    assert proc.returncode == 0 and proc.stdout.strip() == ""


def test_the_hook_is_actually_wired_in_settings() -> None:
    """配線が生きている。スクリプトがあってもフックに載っていなければ何も守らない。"""
    settings = json.loads(_SETTINGS.read_text(encoding="utf-8"))

    commands = [
        h["command"]
        for entry in settings["hooks"]["PreToolUse"]
        if entry.get("matcher") == "Bash"
        for h in entry["hooks"] if h.get("type") == "command"
    ]

    assert any("block_destructive_git.py" in c for c in commands), (
        "PreToolUse/Bash に遮断スクリプトが結線されていない")
