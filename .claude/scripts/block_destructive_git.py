#!/usr/bin/env python3
"""PreToolUse フック: CLAUDE.md の禁止 git コマンドを**実行前に機械的に遮断する**。

なぜスクリプトなのか（ISSUE-450 の教訓）:
    禁止事項は CLAUDE.md に文章で書かれていたが、文章は守られないことがある。実際
    ISSUE-363 では worktree の symlink を `git add -A` で巻き込んでコミットし、本
    チェックアウトの venv・データ参照を壊した。規約は宣言でなく**機械的検査で強制する**。
    本フックは Bash 実行の前段で禁止形を検出し、`permissionDecision: deny` を返して止める。

遮断する形（CLAUDE.md「禁止事項」より）:
    - ``git add -A`` / ``git add --all`` / ``git add .``（未追跡の環境ファイルを無差別に拾う）
    - ``git checkout --`` / ``git restore`` / ``git reset --hard`` / ``git stash``
      （作業ツリーの未コミット変更を消す。並行作業中は他エージェントの成果も消える）

遮断しない形:
    ``git add <path>``（``git add ./foo`` を含む明示パス）・``git checkout <branch>``・
    ``git reset``（--hard 以外）・``git stashes`` のような別語。

契約: stdin に PreToolUse の JSON（``tool_input.command``）。標準出力へ判定 JSON を返す。
      判定不能・入力不正のときは何も言わず通す（フックが作業を止める側の事故を作らない）。
"""
from __future__ import annotations

import json
import re
import shlex
import sys

#: (正規表現, 説明, 代替手段)。command を ; && || | と改行で分割した各節に当てる。
_FORBIDDEN: "list[tuple[re.Pattern[str], str, str]]" = [
    (re.compile(r"^git\s+(?:-C\s+\S+\s+)?add\s+(?:.*\s)?(?:-A|--all)(?:\s|$)"),
     "git add -A / --all は未追跡の環境ファイル（symlink・venv・データ実体）を無差別に拾う",
     "パスを明示して `git add <path>` する。commit 前に `git diff --cached --stat` を読む"),
    (re.compile(r"^git\s+(?:-C\s+\S+\s+)?add\s+(?:.*\s)?\.(?:\s|$)"),
     "git add . は未追跡の環境ファイルを無差別に拾う",
     "パスを明示して `git add <path>` する（`git add ./foo` のような明示パスは可）"),
    (re.compile(r"^git\s+(?:-C\s+\S+\s+)?checkout\s+(?:.*\s)?--(?:\s|$)"),
     "git checkout -- は作業ツリーの未コミット変更を消す",
     "巻き戻しは Edit ツールで行う"),
    (re.compile(r"^git\s+(?:-C\s+\S+\s+)?restore(?:\s|$)"),
     "git restore は作業ツリーの未コミット変更を消す",
     "巻き戻しは Edit ツールで行う"),
    (re.compile(r"^git\s+(?:-C\s+\S+\s+)?reset\s+(?:.*\s)?--hard(?:\s|$)"),
     "git reset --hard は作業ツリーの未コミット変更を消す",
     "巻き戻しは Edit ツールで行う"),
    (re.compile(r"^git\s+(?:-C\s+\S+\s+)?stash(?:\s|$)"),
     "git stash は作業ツリーの未コミット変更を退避し、並行作業の成果を巻き込む",
     "巻き戻しは Edit ツールで行う"),
]

#: 節の区切り。``&&`` ``||`` ``;`` ``|`` 改行 で分ける（`cd x && git add -A` を見逃さない）。
_SPLIT = re.compile(r"&&|\|\||[;\n|]")

#: ヒアドキュメントの開始（``<<EOF`` / ``<<'EOF'`` / ``<<-"EOF"``）。
_HEREDOC_START = re.compile(r"<<-?\s*(['\"]?)([A-Za-z_][A-Za-z0-9_]*)\1")


def strip_heredocs(command: str) -> str:
    """ヒアドキュメントの**本文**を取り除く（開始行は残す）。

    本文はシェルが実行するコマンドではなく、次のプロセスへ渡すデータである（コミット
    メッセージ・設定ファイル・ドキュメント）。取り除かずに行で分割すると、``git add -A``
    と *書いてある文章* を実行だと誤判定する。実際に本フックが自分のコミットメッセージを
    遮断した（2026-08-28・導入直後に実測）。誤遮断するフックは外され、結局規約が守られなく
    なるため、見逃しと同じ重さで潰す。
    """
    lines = (command or "").split("\n")
    out: "list[str]" = []
    i = 0
    while i < len(lines):
        line = lines[i]
        out.append(line)
        delimiters = [m.group(2) for m in _HEREDOC_START.finditer(line)]
        i += 1
        for delimiter in delimiters:
            while i < len(lines) and lines[i].strip() != delimiter:
                i += 1
            i += 1        # 終端行そのものも落とす（データ領域の終わり）
    return "\n".join(out)


def violations(command: str) -> "list[tuple[str, str]]":
    """``command`` に含まれる禁止形を ``[(理由, 代替手段), ...]`` で返す（無ければ空）。"""
    found: "list[tuple[str, str]]" = []
    for part in _SPLIT.split(strip_heredocs(command)):
        clause = part.strip()
        if not clause:
            continue
        # `env FOO=1 git ...` や `sudo git ...` のような前置きを剥がして先頭語を git に揃える。
        try:
            words = shlex.split(clause)
        except ValueError:
            words = clause.split()
        while words and (words[0] in {"sudo", "env", "nohup", "time"} or "=" in words[0]):
            words = words[1:]
        if not words or words[0] != "git":
            continue
        normalized = " ".join(words)
        for pattern, why, instead in _FORBIDDEN:
            if pattern.search(normalized):
                found.append((why, instead))
    return found


def main() -> int:
    try:
        payload = json.load(sys.stdin)
    except Exception:
        return 0                       # 入力を読めないときは黙って通す（作業を止めない）
    if payload.get("tool_name") != "Bash":
        return 0
    command = (payload.get("tool_input") or {}).get("command") or ""
    hits = violations(command)
    if not hits:
        return 0
    reason = "CLAUDE.md の禁止事項に該当するため実行を遮断しました。\n" + "\n".join(
        f"  - {why}\n    → {instead}" for why, instead in hits)
    json.dump({"hookSpecificOutput": {
        "hookEventName": "PreToolUse",
        "permissionDecision": "deny",
        "permissionDecisionReason": reason,
    }}, sys.stdout, ensure_ascii=False)
    return 0


if __name__ == "__main__":
    sys.exit(main())
