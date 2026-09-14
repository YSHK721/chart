"""行単位の台帳（1 行 = 1 レコード）。

重複を「クラスタの一覧」だけで出すと、どの行がどれと重なっているのかを目で追えない。
本モジュールは走査対象の**全行**を 1 レコードずつ出す。

想定運用: 出力 CSV を表計算で開き、``code_key`` 列（または ``code_shape`` 列）で
ソートする。同じ内容の行が隣接するので、上から順に 1 件ずつ重複を確認できる。

    code      … 原文（インデントを含む。そのままソートしても揃わない）
    code_key  … 原文を trim ＋ 連続空白畳み込みしたもの。**完全一致の重複**が隣接する
    code_shape… 識別子を ID・リテラルを STR/NUM へ畳んだもの。**名前だけ違う複製**が隣接する
    line_dup  … 同じ code_key を持つ行の総数（1 なら重複なし）
    shape_dup … 同じ code_shape を持つ行の総数
    tok       … その行のトークン数（``}`` や ``return`` のような定型行を弾く閾値に使う）

宣言単位・ブロック単位のクローン ID（``dup_id``）も同じ行に付く。行ソートで見つけた
1 行の重複が、実は 40 行の塊の一部なのかどうかを、その場で判別するため。
"""
from __future__ import annotations

from collections import Counter
from pathlib import Path

from . import kinds as kind_names
from .model import STRUCTURAL_SHAPES, Clone, ModuleFacts

#: CSV / テキスト出力の列順（唯一源。report 側はこれを書き写さない）。
COLUMNS = (
    "no", "dir", "file", "line", "code", "code_key", "code_shape",
    "line_group", "line_dup", "shape_group", "shape_dup", "tok",
    "lang", "kind", "symbol",
    "dup_id", "dup_type", "dup_unit", "dup_count", "dup_partners", "imports",
)

#: 行にシンボルが無いときの種別。
KIND_MODULE = "module"
KIND_IMPORT = "import"
KIND_BLANK = "blank"
KIND_COMMENT = "comment"

_COMMENT_PREFIXES = ("#", "//", "/*", "*", "*/")


def normalize_code(code: str) -> str:
    """ソート用の正規化。行頭行末の空白を落とし、連続空白を 1 個へ畳む。"""
    return " ".join(code.split())


def _innermost(symbols, line: int):
    """``line`` を含む最も内側のシンボルを返す（無ければ ``None``）。"""
    best = None
    for symbol in symbols:
        if symbol.line <= line <= symbol.end_line:
            if best is None or (symbol.end_line - symbol.line) < (best.end_line - best.line):
                best = symbol
    return best


def _shapes_by_line(module: ModuleFacts) -> "dict[int, list[str]]":
    """行ごとの正規化トークン列。構造マーカー（インデント・論理行末）は外す。

    外さないと、同じ 1 行がネストの深さ違いで別グループになり、``code_shape`` 列で
    ソートしても隣接しない。ブロック単位の判定では構造を見るため、外すのはここだけ。
    """
    out: "dict[int, list[str]]" = {}
    for token in module.tokens:
        if token.shape in STRUCTURAL_SHAPES:
            continue
        out.setdefault(token.line, []).append(token.shape)
    return out


def _partner_text(clone: Clone, path: str, line: int, limit: int = 3) -> str:
    others = [f"{o.path}:{o.start_line}-{o.end_line}" for o in clone.occurrences
              if not (o.path == path and o.start_line <= line <= o.end_line)]
    if len(others) > limit:
        return " | ".join(others[:limit]) + f" | +{len(others) - limit}"
    return " | ".join(others)


def _line_marks(clones: "list[Clone]", prefix: str) -> "dict[tuple[str, int], list[tuple[str, Clone]]]":
    marks: "dict[tuple[str, int], list[tuple[str, Clone]]]" = {}
    for index, clone in enumerate(clones, start=1):
        for occurrence in clone.occurrences:
            for line in range(occurrence.start_line, occurrence.end_line + 1):
                marks.setdefault((occurrence.path, line), []).append((f"{prefix}{index}", clone))
    return marks


def build_rows(modules: "list[ModuleFacts]", sources: "dict[str, list[str]]",
               function_clones: "list[Clone]", block_clones: "list[Clone]",
               repo_root: Path) -> "list[dict]":
    """全行の台帳を作る。``dup_id`` は ``F<n>``（宣言単位）／``B<n>``（ブロック単位）。"""
    marks = _line_marks(function_clones, "F")
    for key, value in _line_marks(block_clones, "B").items():
        marks.setdefault(key, []).extend(value)

    rows: "list[dict]" = []
    for module in sorted(modules, key=lambda m: m.path):
        lines = sources.get(module.path, [])
        import_lines = {edge.line: edge for edge in module.imports}
        shapes = _shapes_by_line(module)
        directory = f"{(repo_root / module.path).parent}/"
        filename = Path(module.path).name
        for line_number, code in enumerate(lines, start=1):
            stripped = code.strip()
            symbol = _innermost(module.symbols, line_number)
            if line_number in import_lines:
                kind, symbol_name = KIND_IMPORT, import_lines[line_number].spec
            elif symbol is not None:
                kind, symbol_name = symbol.kind, symbol.name
            elif not stripped:
                kind, symbol_name = KIND_BLANK, ""
            elif stripped.startswith(_COMMENT_PREFIXES):
                kind, symbol_name = KIND_COMMENT, ""
            else:
                kind, symbol_name = KIND_MODULE, ""

            line_shapes = shapes.get(line_number, [])
            marked = marks.get((module.path, line_number), [])
            rows.append({
                "no": 0,  # 連番は最終的な並び順が決まってから振る（assign_numbers）
                "dir": directory,
                "file": filename,
                "line": line_number,
                "code": code.rstrip("\n"),
                "code_key": normalize_code(code),
                "code_shape": " ".join(line_shapes),
                "line_group": "",
                "line_dup": 0,
                "shape_group": "",
                "shape_dup": 0,
                "tok": len(line_shapes),
                "lang": module.language,
                "kind": kind,
                "symbol": symbol_name,
                "dup_id": ",".join(i for i, _ in marked),
                "dup_type": ",".join(dict.fromkeys(c.clone_type for _, c in marked)),
                "dup_unit": ",".join(dict.fromkeys(c.unit for _, c in marked)),
                "dup_count": ",".join(str(len(c.occurrences)) for _, c in marked),
                "dup_partners": " ;; ".join(
                    _partner_text(c, module.path, line_number) for _, c in marked),
                "imports": import_lines[line_number].spec if line_number in import_lines else "",
            })

    annotate_duplicates(rows)
    return rows


def annotate_duplicates(rows: "list[dict]") -> None:
    """``line_dup`` / ``shape_dup`` と、そのグループ ID を全行走査で付ける。

    グループ ID は「2 回以上出現するキー」にだけ振る（``L1``＝最多のもの）。
    ソート後に ID が連続していれば、そこが 1 つの重複塊である。
    """
    key_counts = Counter(r["code_key"] for r in rows if r["code_key"])
    shape_counts = Counter(r["code_shape"] for r in rows if r["code_shape"])

    def group_ids(counts: "Counter[str]", prefix: str) -> "dict[str, str]":
        duplicated = [(key, n) for key, n in counts.items() if n >= 2]
        duplicated.sort(key=lambda kv: (-kv[1], kv[0]))
        return {key: f"{prefix}{index}" for index, (key, _) in enumerate(duplicated, start=1)}

    line_groups = group_ids(key_counts, "L")
    shape_groups = group_ids(shape_counts, "S")
    for row in rows:
        key, shape = row["code_key"], row["code_shape"]
        row["line_dup"] = key_counts.get(key, 0) if key else 0
        row["shape_dup"] = shape_counts.get(shape, 0) if shape else 0
        row["line_group"] = line_groups.get(key, "") if key else ""
        row["shape_group"] = shape_groups.get(shape, "") if shape else ""


#: ``--only-dup`` の選択肢と、その意味（唯一源。CLI のヘルプはここから引く）。
DUP_FILTERS = {
    "none": "全行を出す（既定）",
    "line": "完全一致で 2 回以上出る行だけ抽出（code_key 一致）",
    "shape": "名前・リテラルを無視して 2 回以上出る行だけ抽出（code_shape 一致）",
    "clone": "宣言単位・ブロック単位のクローンに含まれる行だけ抽出（dup_id 有り）",
    "any": "上記いずれかに当たる行を抽出",
}


def filter_rows(rows: "list[dict]", only: str, min_tok: int,
                skip_kinds: "frozenset[str]" = frozenset()) -> "list[dict]":
    """重複行だけを抜き出す。

    Args:
        only: ``DUP_FILTERS`` のいずれか。
        min_tok: この数未満のトークンしかない行を落とす（``}`` 等の定型行対策）。
        skip_kinds: 落とす ``kind``（例: ``import``）。既定は空＝何も落とさない。
            既定で落とすと「重複が無い」ように見えてしまうため、除外は必ず明示指定にする。
    """
    if only == "none" and min_tok <= 0 and not skip_kinds:
        return rows
    predicate = {
        "none": lambda r: True,
        "line": lambda r: r["line_dup"] >= 2,
        "shape": lambda r: r["shape_dup"] >= 2,
        "clone": lambda r: bool(r["dup_id"]),
        "any": lambda r: r["line_dup"] >= 2 or r["shape_dup"] >= 2 or bool(r["dup_id"]),
    }[only]
    return [r for r in rows
            if r["tok"] >= min_tok and r["kind"] not in skip_kinds and predicate(r)]


def sort_rows(rows: "list[dict]", order: str) -> "list[dict]":
    """出力順を決める。

    ``path``  … ファイル順・行順（原文どおり読む用）
    ``code``  … ``code_key`` 順（完全一致の重複が隣接する）
    ``shape`` … ``code_shape`` 順（名前だけ違う複製が隣接する）
    ``dup``   … 重複数の多い順 → ``code_key`` 順（重い重複から 1 件ずつ潰す用）
    """
    if order == "dup":
        return sorted(rows, key=lambda r: (-r["line_dup"], -r["shape_dup"], r["code_key"],
                                           r["dir"], r["file"], r["line"]))
    if order == "code":
        return sorted(rows, key=lambda r: (r["code_key"] == "", r["code_key"], r["dir"], r["file"], r["line"]))
    if order == "shape":
        return sorted(rows, key=lambda r: (r["code_shape"] == "", r["code_shape"], r["dir"], r["file"], r["line"]))
    return sorted(rows, key=lambda r: (r["dir"], r["file"], r["line"]))


def assign_numbers(rows: "list[dict]") -> "list[dict]":
    """最終の並び順で連番を振り直す（``no`` は「上から何件目か」を意味する）。"""
    for index, row in enumerate(rows, start=1):
        row["no"] = index
    return rows


def summarize_kinds(modules: "list[ModuleFacts]") -> "dict[str, int]":
    """種別ごとの定義数（def / protocol / class ... の内訳）。"""
    counts: "dict[str, int]" = {}
    for module in modules:
        for symbol in module.symbols:
            counts[symbol.kind] = counts.get(symbol.kind, 0) + 1
    return dict(sorted(counts.items(), key=lambda kv: (-kv[1], kv[0])))


def type_symbols(modules: "list[ModuleFacts]") -> "list[dict]":
    """型・名前空間を成すシンボル（class / protocol / enum ...）の一覧。"""
    out: "list[dict]" = []
    for module in modules:
        for symbol in module.symbols:
            if symbol.kind in kind_names.TYPE_KINDS:
                out.append({"path": symbol.path, "name": symbol.name, "kind": symbol.kind,
                            "line": symbol.line, "end_line": symbol.end_line,
                            "bases": list(symbol.bases), "exported": symbol.exported})
    out.sort(key=lambda d: (d["path"], d["line"]))
    return out
