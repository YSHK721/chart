"""ISSUE-305: ``market_profile_api`` パッケージ内に import の循環が無いことの回帰ガード。

なぜ検定するか:
    循環は「関数内 import（遅延 import）」で **module ロード時の失敗だけ**を回避できてしまうため、
    テストが緑のまま残り続ける。実際、本パッケージには次の 3 循環が長期間存在した（codescan で検出）。

        cache_layout                → controller.tf_period_profile_controller → cache_layout
        compute.market_profile_dwell → compute.market_profile_dwell_warmer     → compute.market_profile_dwell
        compute.market_profile_zp    → compute.market_profile_zp_warmer        → compute.market_profile_zp

    いずれも「内側のモジュールに、外側（合成・運用バッチ）の都合を置いた」ことが原因である。
    したがって本検定は **module-level と関数内の両方**の import を等しく数える（遅延 import は
    循環の回避策ではなく、循環を隠す手段でしかない）。

検定の範囲:
    ``market_profile_api`` パッケージ内の辺のみ（外部・他パッケージへの依存は対象外）。
    循環の探索は標準ライブラリ :mod:`graphlib` に委ねる（探索アルゴリズムを手書きしない）。
"""
from __future__ import annotations

import ast
from graphlib import CycleError, TopologicalSorter
from pathlib import Path

_PKG = Path(__file__).resolve().parents[1] / "market_profile_api"
_ROOT_NAME = _PKG.name


def _module_name(path: Path) -> str:
    """ファイルパスを ``market_profile_api.<...>`` のモジュール名へ変換する。"""
    parts = path.relative_to(_PKG).with_suffix("").parts
    if parts and parts[-1] == "__init__":
        parts = parts[:-1]
    return ".".join((_ROOT_NAME, *parts))


def _known_modules() -> "dict[str, Path]":
    return {_module_name(p): p for p in sorted(_PKG.rglob("*.py"))}


def _resolve(target: str, known: "dict[str, Path]") -> "str | None":
    """import 指定子を、実在するモジュール名へ落とす（存在しない末尾要素は属性とみなす）。"""
    if target in known:
        return target
    head = target.rsplit(".", 1)[0]  # ``pkg.mod`` の ``mod`` が属性だった場合。
    return head if head in known else None


def _edges_of(path: Path, module: str, known: "dict[str, Path]") -> "set[str]":
    """このモジュールが依存する同一パッケージ内モジュールを返す（関数内 import も含む）。"""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    package = module.rsplit(".", 1)[0] if "." in module else module
    out: "set[str]" = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.split(".")[0] == _ROOT_NAME:
                    out.add(alias.name)
        elif isinstance(node, ast.ImportFrom):
            if node.level:  # 相対 import: 現在のパッケージから level-1 段上がる。
                base = package.split(".")
                base = base[: len(base) - (node.level - 1)] if node.level > 1 else base
                prefix = ".".join([*base, node.module] if node.module else base)
            else:
                prefix = node.module or ""
            if prefix.split(".")[0] != _ROOT_NAME:
                continue
            out.update(f"{prefix}.{alias.name}" for alias in node.names)
            out.add(prefix)
    resolved = {r for r in (_resolve(t, known) for t in out) if r is not None}
    return resolved - {module}


def _import_graph() -> "dict[str, set[str]]":
    known = _known_modules()
    return {name: _edges_of(path, name, known) for name, path in known.items()}


def _cycles(graph: "dict[str, set[str]]") -> "list[tuple[str, ...]]":
    """循環をすべて列挙する（1 件見つけるたびにその辺を 1 本落として再探索する）。"""
    remaining = {node: set(deps) for node, deps in graph.items()}
    found: "list[tuple[str, ...]]" = []
    # 1 件検出するごとに辺を 1 本落とすため、反復は**辺数**で有界（モジュール数ではない。
    # モジュール数で打ち切ると循環が多い場合に列挙が途中で切れる）。
    for _ in range(sum(len(deps) for deps in remaining.values()) + 1):
        try:
            TopologicalSorter(remaining).prepare()
        except CycleError as exc:
            cycle = tuple(exc.args[1])
            found.append(cycle)
            remaining[cycle[0]].discard(cycle[1])  # この循環を 1 本切って次を探す。
            continue
        return found
    return found


def test_market_profile_api_has_no_import_cycles():
    cycles = _cycles(_import_graph())
    assert cycles == [], "import の循環が残っている:\n" + "\n".join(
        " → ".join(c) for c in cycles
    )


def test_the_guard_detects_a_cycle_when_one_exists():
    """ガード自体が循環を検出できることを固定する（常に緑の空検定にしない）。"""
    graph = {"a": {"b"}, "b": {"a"}, "c": set()}

    assert _cycles(graph) != []


def test_cache_layout_descriptor_owns_the_boundary_types():
    """記述子の型の所有者は境界モジュールであり、合成側 ``cache_layout`` ではない（ISSUE-305）。

    所有者（Store / controller）が合成側から型を取ると、合成側が所有者を列挙する辺と合わさって
    循環が再発する。型の import 元を構造として固定する。
    """
    owners = [
        _PKG / "gateway" / "zp_store.py",
        _PKG / "gateway" / "dwell_rollup_store.py",
        _PKG / "controller" / "tf_period_profile_controller.py",
    ]
    for owner in owners:
        text = owner.read_text(encoding="utf-8")
        assert "from market_profile_api.cache_layout_descriptor import" in text, owner.name
        assert "from market_profile_api.cache_layout import" not in text, owner.name
