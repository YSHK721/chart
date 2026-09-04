"""JS ソースから `new X({ ... })` の**トップレベル実引数キー**を抽出する（ISSUE-255 追補）。

用途: 合成根（本番配線）が渡す形とテストが渡す形を突き合わせ、
「テストが自分で与えた前提でしか検証していない」状態を機械的に検出する。

限界（明示する）: 構文解析器ではなく括弧対応の走査である。位置引数・spread（``...opts``）・
動的キー（``[k]: v``）は対象外。対象外のものを「無い」と誤判定しないよう、キーが取れなかった
呼び出しは単に対象から外れる（過検出より見落としを選ぶ＝Red の信頼性を優先）。
"""
from __future__ import annotations

import re
from pathlib import Path

#: キー名として採らない予約語（アロー関数本体などを誤って拾わないため）。
_KEYWORDS = {
    "return", "await", "new", "if", "for", "const", "let", "var",
    "function", "this", "typeof", "else", "while", "switch",
}


def strip_comments(src: str) -> str:
    """行コメント・ブロックコメントを除去する（キー直前の注釈で取りこぼさないため）。"""
    src = re.sub(r"/\*.*?\*/", "", src, flags=re.S)
    return re.sub(r"//[^\n]*", "", src)


def _split_top_level(body: str) -> "list[str]":
    depth = 0
    cur: "list[str]" = []
    parts: "list[str]" = []
    for ch in body:
        if ch in "([{":
            depth += 1
        elif ch in ")]}":
            depth -= 1
        if ch == "," and depth == 0:
            parts.append("".join(cur))
            cur = []
        else:
            cur.append(ch)
    parts.append("".join(cur))
    return parts


def new_calls(src: str) -> "list[tuple[str, set[str]]]":
    """``new X({...})`` を抽出し ``(クラス名, トップレベルキー集合)`` の列を返す。

    オプションオブジェクトを持たない呼び出し（位置引数のみ）は返さない。
    """
    src = strip_comments(src)
    out: "list[tuple[str, set[str]]]" = []
    for m in re.finditer(r"new\s+([A-Z][A-Za-z0-9_]*)\s*\(", src):
        cls = m.group(1)
        i = m.end()
        depth = 1
        start = i
        while i < len(src) and depth > 0:
            c = src[i]
            if c in "([{":
                depth += 1
            elif c in ")]}":
                depth -= 1
            i += 1
        args = src[start:i - 1]
        obj = re.search(r"\{(.*)\}", args, re.S)
        if not obj:
            continue
        keys: "set[str]" = set()
        for part in _split_top_level(obj.group(1)):
            part = part.strip()
            if not part or part.startswith("..."):
                continue
            km = re.match(r"^([A-Za-z_$][A-Za-z0-9_$]*)\s*[:,]?", part)
            if km and km.group(1) not in _KEYWORDS:
                keys.add(km.group(1))
        out.append((cls, keys))
    return out


def collect(paths: "list[Path]") -> "dict[str, list[set[str]]]":
    """複数ファイルから ``クラス名 -> [キー集合, ...]`` を集める。"""
    acc: "dict[str, list[set[str]]]" = {}
    for p in paths:
        for cls, keys in new_calls(p.read_text(encoding="utf-8")):
            acc.setdefault(cls, []).append(keys)
    return acc
