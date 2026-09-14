"""重複検出（言語非依存）。

3 つの見方を出す。用途が違うので統合しない。

1. 宣言単位クローン (``unit="function"``)
   関数・メソッド・クラスまるごとの一致。単一ソース化の対象がそのまま宣言なので、
   最も直接に「消せる複製」を指す。
2. ブロック単位クローン (``unit="block"``)
   宣言をまたぐ／宣言の一部だけの一致。手書きコピペは宣言境界に揃わないことが多く、
   1 だけでは取り逃す。
3. 同名別実装 (``diverged_names``)
   同じ名前が複数ファイルにあり中身が違う。複製が**片方だけ直された**状態を指す。
   これは 1・2 では検出できない（一致しないため）が、複製の最も危険な帰結である。

クローン種別:
    ``type-1`` = トークン列が完全一致。
    ``type-2`` = 識別子・リテラルだけが異なる（``Token.shape`` が一致）。
"""
from __future__ import annotations

from collections import defaultdict

from .model import Clone, Fragment, ModuleFacts, Occurrence

_HASH_BASE = 1_000_003
_HASH_MOD = (1 << 61) - 1


def _occurrence(fragment: Fragment) -> Occurrence:
    return Occurrence(path=fragment.path, name=fragment.name, kind=fragment.kind,
                      start_line=fragment.start_line, end_line=fragment.end_line)


def cluster_fragments(fragments, min_tokens: int, min_lines: int) -> "list[Clone]":
    """宣言単位のクローンを求める。"""
    by_shape: "dict[tuple, list[Fragment]]" = defaultdict(list)
    for fragment in fragments:
        if len(fragment.tokens) < min_tokens or fragment.line_count < min_lines:
            continue
        by_shape[tuple(t.shape for t in fragment.tokens)].append(fragment)

    clones: "list[Clone]" = []
    for shape, group in by_shape.items():
        if len(group) < 2:
            continue
        exact: "dict[tuple, list[Fragment]]" = defaultdict(list)
        for fragment in group:
            exact[tuple(t.text for t in fragment.tokens)].append(fragment)
        for members in exact.values():
            if len(members) >= 2:
                clones.append(Clone(clone_type="type-1", unit="function", token_count=len(shape),
                                    occurrences=[_occurrence(f) for f in members]))
        if len(exact) > 1:
            clones.append(Clone(clone_type="type-2", unit="function", token_count=len(shape),
                                occurrences=[_occurrence(f) for f in group]))
    return _drop_contained(clones)


def _contains(outer: Occurrence, inner: Occurrence) -> bool:
    return (outer.path == inner.path
            and outer.start_line <= inner.start_line
            and inner.end_line <= outer.end_line
            and (outer.start_line, outer.end_line) != (inner.start_line, inner.end_line))


def _drop_contained(clones: "list[Clone]") -> "list[Clone]":
    """大きいクローンに完全に含まれる小さいクローンを落とす。

    クラス全体が複製されていれば、その中の各メソッドも当然複製である。両方を出すと
    件数が水増しされ、優先順位が読めなくなる。残すのは外側だけ。
    """
    ordered = sorted(clones, key=lambda c: c.token_count, reverse=True)
    kept: "list[Clone]" = []
    for clone in ordered:
        covered = False
        for bigger in kept:
            if bigger.token_count <= clone.token_count:
                continue
            if all(any(_contains(b, o) for b in bigger.occurrences) for o in clone.occurrences):
                covered = True
                break
        if not covered:
            kept.append(clone)
    return kept


def _window_hashes(shape_ids: "list[int]", window: int) -> "list[int]":
    """長さ ``window`` の各位置の多項式ローリングハッシュ。"""
    if len(shape_ids) < window:
        return []
    power = pow(_HASH_BASE, window - 1, _HASH_MOD)
    out: "list[int]" = []
    h = 0
    for i, value in enumerate(shape_ids):
        if i >= window:
            h = (h - shape_ids[i - window] * power) % _HASH_MOD
        h = (h * _HASH_BASE + value) % _HASH_MOD
        if i >= window - 1:
            out.append(h)
    return out


def find_block_clones(modules: "list[ModuleFacts]", window: int, min_tokens: int,
                      max_occurrences: int) -> "tuple[list[Clone], dict]":
    """ブロック単位クローンを求める。

    Args:
        window: 一致の種となる連続トークン数。小さすぎると定型句を拾う。
        min_tokens: 併合後にこの長さ未満のブロックは捨てる。
        max_occurrences: 同一 window が この回数を超えて現れる場合は種にしない
            （定型句が組合せ爆発を起こすため）。除外件数は戻り値の統計に必ず出す
            （黙って打ち切らない）。

    Returns:
        (クローン列, 統計)。統計には除外した定型句の数と実例を含む。
    """
    shape_ids: "dict[str, int]" = {}
    streams: "list[list[int]]" = []
    for module in modules:
        streams.append([shape_ids.setdefault(t.shape, len(shape_ids) + 1) for t in module.tokens])

    index: "dict[int, list[tuple[int, int]]]" = defaultdict(list)
    for file_index, stream in enumerate(streams):
        for pos, h in enumerate(_window_hashes(stream, window)):
            index[h].append((file_index, pos))

    skipped: "list[tuple[int, int]]" = []
    diagonals: "dict[tuple[int, int, int], list[int]]" = defaultdict(list)
    for h, occurrences in index.items():
        if len(occurrences) < 2:
            continue
        if len(occurrences) > max_occurrences:
            skipped.append((h, len(occurrences)))
            continue
        # 連続する 2 件だけを結ぶ（全対を結ぶと定型句で組合せ爆発する）。
        for (fa, pa), (fb, pb) in zip(occurrences, occurrences[1:]):
            if fa == fb and abs(pa - pb) < window:
                continue  # 自己重なり
            diagonals[(fa, fb, pa - pb)].append(pa)

    keyed: "list[tuple[tuple, Clone]]" = []
    for (fa, fb, delta), positions in diagonals.items():
        positions.sort()
        run_start = previous = positions[0]
        for pos in positions[1:] + [None]:
            if pos is not None and pos == previous + 1:
                previous = pos
                continue
            length = previous - run_start + window
            if length >= min_tokens:
                found = _block_clone(modules, fa, fb, run_start, previous, delta, window, length)
                if found is not None:
                    keyed.append(found)
            if pos is None:
                break
            run_start = previous = pos

    merged = _merge_block_clones(keyed)
    stats = {
        "indexed_windows": sum(max(0, len(s) - window + 1) for s in streams),
        "skipped_boilerplate_windows": len(skipped),
        "skipped_max_occurrences": max((n for _, n in skipped), default=0),
        "max_occurrences_threshold": max_occurrences,
    }
    return merged, stats


def _block_clone(modules, fa: int, fb: int, run_start: int, run_end: int, delta: int,
                 window: int, length: int) -> "tuple[tuple, Clone] | None":
    """一致区間を実体で確認し、(内容キー, クローン) を返す。ハッシュ衝突なら ``None``。"""
    a_tokens = modules[fa].tokens[run_start:run_start + length]
    b_start = run_start - delta
    b_tokens = modules[fb].tokens[b_start:b_start + length]
    if len(a_tokens) != len(b_tokens) or not a_tokens:
        return None
    a_shape = tuple(t.shape for t in a_tokens)
    if a_shape != tuple(t.shape for t in b_tokens):
        return None  # ハッシュ衝突。実体で確認してから採る。
    same_text = [t.text for t in a_tokens] == [t.text for t in b_tokens]
    clone = Clone(
        clone_type="type-1" if same_text else "type-2", unit="block", token_count=length,
        occurrences=[
            Occurrence(path=modules[fa].path, name="", kind="block",
                       start_line=a_tokens[0].line, end_line=a_tokens[-1].line),
            Occurrence(path=modules[fb].path, name="", kind="block",
                       start_line=b_tokens[0].line, end_line=b_tokens[-1].line),
        ],
    )
    return a_shape, clone


def _merge_block_clones(keyed: "list[tuple[tuple, Clone]]") -> "list[Clone]":
    """内容が同じブロック対を 1 クラスタ（N 箇所）へまとめ、内包される小片を落とす。"""
    by_key: "dict[tuple, Clone]" = {}
    for key, clone in keyed:
        merged = by_key.get(key)
        if merged is None:
            by_key[key] = clone
            continue
        known = {(o.path, o.start_line) for o in merged.occurrences}
        for occurrence in clone.occurrences:
            if (occurrence.path, occurrence.start_line) not in known:
                merged.occurrences.append(occurrence)
                known.add((occurrence.path, occurrence.start_line))
        if clone.clone_type == "type-2":
            merged.clone_type = "type-2"
    for clone in by_key.values():
        clone.occurrences.sort(key=lambda o: (o.path, o.start_line))
    return _drop_contained(list(by_key.values()))


def drop_blocks_inside(block_clones: "list[Clone]", function_clones: "list[Clone]") -> "list[Clone]":
    """宣言単位クローンの内側に完全に収まるブロックを落とす（同じ複製の二重計上）。"""
    covered = [o for clone in function_clones for o in clone.occurrences]
    out: "list[Clone]" = []
    for clone in block_clones:
        if all(any(_contains(c, o) or (c.path == o.path and c.start_line <= o.start_line
                                       and o.end_line <= c.end_line)
                   for c in covered) for o in clone.occurrences):
            continue
        out.append(clone)
    return out


def diverged_names(modules: "list[ModuleFacts]", min_tokens: int) -> "list[dict]":
    """同名なのに中身が違う宣言を集める（複製が片方だけ直された状態）。

    比較は**原文トークン**で行う。正規化形で比べると、``* 2`` が片方だけ ``* 3`` に
    直された状態（数値は ``NUM`` へ畳まれる）を同一とみなして取り逃す。これは
    「複製の片方だけが修正された」という最も危険な形そのものなので、必ず拾う。

    ``shape_variants`` が 1 なら差は名前・リテラルだけ（型 2 クローンとしても出る）、
    2 以上なら構造そのものが食い違っている。後者を優先して並べる。
    """
    by_name: "dict[str, list[Fragment]]" = defaultdict(list)
    for module in modules:
        for fragment in module.fragments:
            if len(fragment.tokens) >= min_tokens:
                by_name[fragment.name].append(fragment)

    out: "list[dict]" = []
    for name, group in by_name.items():
        if len({f.path for f in group}) < 2:
            continue
        variants = {tuple(t.text for t in f.tokens) for f in group}
        if len(variants) < 2:
            continue  # 完全一致ならクローン検出側で出る
        shapes = {tuple(t.shape for t in f.tokens) for f in group}
        out.append({
            "name": name,
            "variants": len(variants),
            "shape_variants": len(shapes),
            "occurrences": [
                {"path": f.path, "line": f.start_line, "end_line": f.end_line,
                 "kind": f.kind, "tokens": len(f.tokens)}
                for f in sorted(group, key=lambda f: (f.path, f.start_line))
            ],
        })
    out.sort(key=lambda d: (-d["shape_variants"], -len(d["occurrences"]), d["name"]))
    return out
