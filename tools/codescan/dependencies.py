"""依存関係の解決とグラフ解析。

import 指定子を**実ファイル**へ解決する。解決できたものだけを内部依存とし、
残りは外部依存として名前のまま数える（存在しない依存を推測で作らない）。

Python の解決根は ``tools/dev_paths.txt``（唯一源）から読む。ここに値を書き写すと
実行時とテスト時で解決根がずれるため、必ず台帳から読む（ISSUE-279 と同じ理由）。
"""
from __future__ import annotations

from pathlib import Path

from .model import ModuleFacts

#: JS の拡張子補完順（Node / ブラウザ双方の慣行）。
_JS_SUFFIXES = (".js", ".mjs", ".cjs", ".ts", ".tsx", ".jsx", ".json")


def python_roots(repo_root: Path) -> "list[str]":
    """``tools/dev_paths.txt`` から Python の import 解決根を読む。"""
    ledger = repo_root / "tools" / "dev_paths.txt"
    roots: "list[str]" = []
    if ledger.is_file():
        for raw in ledger.read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            if line and not line.startswith("#"):
                roots.append("" if line == "." else line.rstrip("/"))
    if "" not in roots:
        roots.insert(0, "")
    return roots


class Resolver:
    """import 指定子 → リポジトリ相対パスの解決器。"""

    def __init__(self, repo_root: Path, known_files: "set[str]", roots: "list[str]") -> None:
        self._repo_root = repo_root
        self._files = known_files
        self._roots = roots

    def resolve(self, module: ModuleFacts, edge) -> "list[str]":
        """1 件の import を実ファイル群へ解決する（解決できなければ空）。"""
        if module.language == "python":
            return self._resolve_python(module.path, edge)
        target = self._resolve_js(module.path, edge.spec)
        return [target] if target else []

    def _candidate(self, base: str) -> "str | None":
        for candidate in (f"{base}.py", f"{base}/__init__.py", f"{base}.pyi"):
            if candidate in self._files:
                return candidate
        return None

    def _bases(self, path: str, spec: str, level: int) -> "list[str]":
        parts = spec.split(".") if spec else []
        if level:
            base_dir = Path(path).parent
            for _ in range(level - 1):
                base_dir = base_dir.parent
            return ["/".join([str(base_dir).strip("."), *parts]).strip("/")]
        return ["/".join([p for p in [root, *parts] if p]) for root in self._roots]

    def _resolve_python(self, path: str, edge) -> "list[str]":
        for base in self._bases(path, edge.spec, edge.level):
            if edge.is_from:
                # `from pkg import mod` の `mod` はサブモジュールでありうる。先に実体を探す。
                submodules = [found for found in
                              (self._candidate(f"{base}/{name}") for name in edge.names) if found]
                if submodules:
                    return submodules
            found = self._candidate(base)
            if found:
                return [found]
        return []

    def _resolve_js(self, path: str, spec: str) -> "str | None":
        if not spec.startswith("."):
            return None
        target = (Path(path).parent / spec).as_posix()
        target = Path(target).resolve().relative_to(Path.cwd()).as_posix() \
            if target.startswith("/") else _normalize(target)
        if target in self._files:
            return target
        for suffix in _JS_SUFFIXES:
            if f"{target}{suffix}" in self._files:
                return f"{target}{suffix}"
            if f"{target}/index{suffix}" in self._files:
                return f"{target}/index{suffix}"
        return None


def _normalize(path: str) -> str:
    parts: "list[str]" = []
    for part in path.split("/"):
        if part in ("", "."):
            continue
        if part == ".." and parts and parts[-1] != "..":
            parts.pop()
            continue
        parts.append(part)
    return "/".join(parts)


def build_graph(modules: "list[ModuleFacts]", resolver: Resolver) -> dict:
    """依存グラフを組む。

    Returns:
        ``edges`` (内部依存 from→to)、``external`` (未解決指定子の出現数)、
        ``fan_in`` / ``fan_out``、``cycles`` (強連結成分＝循環)、``unresolved`` を含む dict。
    """
    by_path = {m.path: m for m in modules}
    edges: "set[tuple[str, str]]" = set()
    external: "dict[str, int]" = {}
    unresolved: "list[dict]" = []

    for module in modules:
        for edge in module.imports:
            targets = [t for t in resolver.resolve(module, edge) if t != module.path]
            if targets:
                edges.update((module.path, target) for target in targets)
            else:
                name = edge.spec.split(".")[0] if edge.level == 0 else f"{'.' * edge.level}{edge.spec}"
                external[name] = external.get(name, 0) + 1
                unresolved.append({"path": module.path, "spec": edge.spec,
                                   "level": edge.level, "line": edge.line})

    fan_out: "dict[str, int]" = {}
    fan_in: "dict[str, int]" = {}
    adjacency: "dict[str, list[str]]" = {m.path: [] for m in modules}
    for source, target in edges:
        fan_out[source] = fan_out.get(source, 0) + 1
        fan_in[target] = fan_in.get(target, 0) + 1
        if target in adjacency:
            adjacency[source].append(target)

    return {
        "edges": sorted(edges),
        "external": dict(sorted(external.items(), key=lambda kv: -kv[1])),
        "fan_in": fan_in,
        "fan_out": fan_out,
        "cycles": find_cycles(adjacency),
        "unresolved": unresolved,
    }


def find_cycles(adjacency: "dict[str, list[str]]") -> "list[list[str]]":
    """強連結成分のうち大きさ 2 以上（＝循環）を返す（Tarjan・反復版）。"""
    index_of: "dict[str, int]" = {}
    low: "dict[str, int]" = {}
    on_stack: "set[str]" = set()
    stack: "list[str]" = []
    counter = 0
    cycles: "list[list[str]]" = []

    for root in adjacency:
        if root in index_of:
            continue
        work: "list[tuple[str, int]]" = [(root, 0)]
        while work:
            node, child_index = work[-1]
            if child_index == 0:
                index_of[node] = low[node] = counter
                counter += 1
                stack.append(node)
                on_stack.add(node)
            children = adjacency.get(node, ())
            if child_index < len(children):
                work[-1] = (node, child_index + 1)
                child = children[child_index]
                if child not in index_of:
                    work.append((child, 0))
                elif child in on_stack:
                    low[node] = min(low[node], index_of[child])
                continue
            work.pop()
            if work:
                parent = work[-1][0]
                low[parent] = min(low[parent], low[node])
            if low[node] == index_of[node]:
                component: "list[str]" = []
                while True:
                    member = stack.pop()
                    on_stack.discard(member)
                    component.append(member)
                    if member == node:
                        break
                if len(component) > 1:
                    cycles.append(sorted(component))
    cycles.sort(key=lambda c: (-len(c), c))
    return cycles
