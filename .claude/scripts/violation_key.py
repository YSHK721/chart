"""違反 ident の内容アドレス化 — 行番号に依存しない安定キーの唯一の定義。

背景（2026-08-30 裁定）: 旧キーは `L{lineno}` を埋め込んでいたため、凍結済み違反の
上流に無関係な行を挿入しただけで ident が変わり、Stop フックが「新規違反」として
exit 2 → asyncRewake ループを起こした（隔離環境で再現・実測済み）。

キーは AST ノードの構造ダイジェストで与える。`ast.dump` は既定で位置属性
（lineno / col_offset）を含まないため、行移動・整形では不変。違反ノード自体が
書き換われば変わる（＝別の違反として検出される。これは正しい挙動）。

同一ファイル内に同一内容の違反が複数あるときは `disambiguate` が出現順（行順）に
`#2`, `#3`… を付す。件数の増を新規・減を解消として検出可能に保つためで、
無関係な行挿入では出現順が変わらないため付番も安定する。
"""

from __future__ import annotations

import ast
import hashlib
from dataclasses import replace


def node_digest(node: ast.AST) -> str:
    """AST ノードの構造ダイジェスト（位置属性を含まない・12 hex）。"""
    return hashlib.sha1(ast.dump(node).encode("utf-8")).hexdigest()[:12]


def disambiguate(violations: list) -> list:
    """同一 (check, path, key) の 2 件目以降へ `#k` を付す。入力は行順整列済みを前提。"""
    seen: dict[tuple[str, str, str], int] = {}
    out = []
    for v in violations:
        k = (v.check, v.path, v.key)
        n = seen.get(k, 0) + 1
        seen[k] = n
        out.append(v if n == 1 else replace(v, key=f"{v.key}#{n}"))
    return out
