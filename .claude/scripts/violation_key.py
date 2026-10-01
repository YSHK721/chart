"""違反 ident の内容アドレス化 — 行番号にもインタープリタ版にも依存しない安定キーの唯一の定義。

背景 1（2026-08-30 裁定）: 旧キーは `L{lineno}` を埋め込んでいたため、凍結済み違反の
上流に無関係な行を挿入しただけで ident が変わり、Stop フックが「新規違反」として
exit 2 → asyncRewake ループを起こした（隔離環境で再現・実測済み）。

背景 2（ISSUE-543・2026-09-27 実測）: その是正で採った `sha1(ast.dump(node))` は
**Python の版に依存した**。`ast.dump` は 3.13 から空リスト欄（`keywords=[]` 等）を
省略するため、同一ソースでも 3.11 と 3.14 でダイジェストが変わり、baseline（3.14 で凍結）
と実行系（3.11 のコンテナ）の版差だけで既存違反 163 件が「新規」として毎ターン再報告された。

キーは**違反ノードのソース断片**（`ast.get_source_segment`）の空白正規化テキストの
ダイジェストで与える。ソーステキストはどの版のインタープリタで読んでも同一なので、
キーは内容だけに依存する。空白正規化（連続空白を 1 個へ）により、行移動・折返し・
インデント変更では不変。違反ノード自体が書き換われば変わる（＝別の違反として検出される。
これは正しい挙動）。

同一ファイル内に同一内容の違反が複数あるときは `disambiguate` が出現順（行順）に
`#2`, `#3`… を付す。件数の増を新規・減を解消として検出可能に保つためで、
無関係な行挿入では出現順が変わらないため付番も安定する。
"""

from __future__ import annotations

import ast
import hashlib
from dataclasses import replace


def node_digest(node: ast.AST, source: str | None = None) -> str:
    """違反ノードの内容ダイジェスト（位置・インタープリタ版に依存しない・12 hex）。

    ``source`` はノードを切り出した元ファイルのソーステキスト。呼出側（各検定）が
    必ず渡す——これがダイジェストの観測境界である。渡らない・切り出せない場合だけ
    `ast.dump` へ縮退する（実ファイル由来のノードは常に位置情報を持つので通常は通らない。
    縮退経路は版依存に戻るため、恒常的に使ってはならない）。
    """
    segment = ast.get_source_segment(source, node) if source is not None else None
    if segment is not None:
        normalized = " ".join(segment.split())
        return hashlib.sha1(normalized.encode("utf-8")).hexdigest()[:12]
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
