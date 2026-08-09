"""走査対象ファイルの決定と読み込み。

対象・除外のパターンは ``tools/codescan_scope.txt``（唯一源）から読む。
CLI の ``--include/--exclude`` は台帳の**後ろに追記**される（台帳を置き換えない）ので、
既定の除外（ベンダ・生成物）を意図せず外してしまうことがない。
"""
from __future__ import annotations

import os
import re
from pathlib import Path

from .model import ModuleFacts
from .registry import AnalyzerRegistry

SCOPE_LEDGER = "tools/codescan_scope.txt"


def _glob_to_regex(pattern: str) -> "re.Pattern[str]":
    """``**`` / ``*`` / ``?`` を扱う最小 glob を正規表現へ変換する。"""
    out: "list[str]" = ["^"]
    i, n = 0, len(pattern)
    while i < n:
        char = pattern[i]
        if pattern.startswith("**/", i):
            out.append("(?:.*/)?")
            i += 3
        elif pattern.startswith("**", i):
            out.append(".*")
            i += 2
        elif char == "*":
            out.append("[^/]*")
            i += 1
        elif char == "?":
            out.append("[^/]")
            i += 1
        else:
            out.append(re.escape(char))
            i += 1
    out.append("$")
    return re.compile("".join(out))


class Scope:
    """``+``/``-`` 規則の並び。最後に一致した規則が勝つ。"""

    def __init__(self, rules: "list[tuple[bool, str]]") -> None:
        self._rules = [(include, _glob_to_regex(pattern), pattern) for include, pattern in rules]
        # `X/**` 形式の規則は「ディレクトリ X 以下すべて」を意味する。走査時に X へ
        # 降りる前に判定できるよう、ディレクトリ用の規則として別に持つ。
        self._dir_rules = [
            (include, _glob_to_regex(pattern[:-3]), pattern)
            for include, pattern in rules if pattern.endswith("/**")
        ]

    @classmethod
    def from_ledger(cls, repo_root: Path, extra_include=(), extra_exclude=()) -> "Scope":
        rules: "list[tuple[bool, str]]" = []
        ledger = repo_root / SCOPE_LEDGER
        if ledger.is_file():
            for raw in ledger.read_text(encoding="utf-8").splitlines():
                line = raw.strip()
                if not line or line.startswith("#"):
                    continue
                if line[0] in "+-" and len(line) > 1:
                    rules.append((line[0] == "+", line[1:].strip()))
        rules.extend((True, p) for p in extra_include)
        rules.extend((False, p) for p in extra_exclude)
        return cls(rules)

    def allows(self, relative_path: str) -> bool:
        verdict = False
        for include, regex, _ in self._rules:
            if regex.match(relative_path):
                verdict = include
        return verdict

    def blocks_directory(self, relative_dir: str) -> bool:
        """このディレクトリ以下へ**降りない**と決められるか。

        走査後に除外するのでは遅い。``node_modules`` には自己参照 symlink が実在し
        （ISSUE-280）、辿ると際限なく深くなってメモリを食い潰す。降りる前に切る。
        """
        verdict = False
        for include, regex, _ in self._dir_rules:
            if regex.match(relative_dir):
                verdict = not include
        return verdict

    @property
    def rules(self) -> "list[str]":
        return [("+ " if include else "- ") + pattern for include, _, pattern in self._rules]


def iter_files(repo_root: Path, scope: Scope, registry: AnalyzerRegistry,
               roots: "list[str]" = ()) -> "list[str]":
    """走査対象のリポジトリ相対パスを返す（安定順）。

    除外ディレクトリへは**降りない**（走査後に捨てない）。また、ディレクトリの
    シンボリックリンクは辿らない。``unified_ui/web/node_modules`` には自己参照
    symlink が実在し（ISSUE-280）、辿ると深さが際限なく増えて OOM で落ちる（実測）。
    """
    bases = [repo_root / r for r in roots] if roots else [repo_root]
    found: "set[str]" = set()
    for base in bases:
        if base.is_file():
            found.add(base.relative_to(repo_root).as_posix())
            continue
        for dirpath, dirnames, filenames in os.walk(base, followlinks=False):
            relative_dir = Path(dirpath).relative_to(repo_root).as_posix()
            prefix = "" if relative_dir == "." else f"{relative_dir}/"
            dirnames[:] = [name for name in dirnames
                           if not scope.blocks_directory(f"{prefix}{name}")]
            for name in filenames:
                if registry.for_path(name) is None:
                    continue
                relative = f"{prefix}{name}"
                if scope.allows(relative) and (repo_root / relative).is_file():
                    found.add(relative)
    return sorted(found)


def collect(repo_root: Path, paths: "list[str]", registry: AnalyzerRegistry
            ) -> "tuple[list[ModuleFacts], dict[str, list[str]]]":
    """各ファイルを解析する。読み込み・解析の失敗は握り潰さず ``errors`` に残す。"""
    modules: "list[ModuleFacts]" = []
    sources: "dict[str, list[str]]" = {}
    for relative in paths:
        analyzer = registry.for_path(relative)
        if analyzer is None:
            continue
        full = repo_root / relative
        try:
            text = full.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as exc:
            modules.append(ModuleFacts(path=relative, language=analyzer.language, loc=0,
                                       errors=(f"read: {exc}",)))
            continue
        sources[relative] = text.splitlines()
        modules.append(analyzer.analyze(relative, text))
    return modules, sources
