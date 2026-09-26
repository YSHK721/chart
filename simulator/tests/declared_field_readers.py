"""宣言した欄の読み手を構文木で数える計測器（テストヘルパ・**テストではない**）。

なぜ在るか（ISSUE-535・実測 2026-09-26）:
    「`EngineBinding.known_ea_names`」 は**設定されるだけで読み手が 1 つも無い**まま残っていた。
    出力は 1 ビットも変わらないため状態検証では原理的に落ちず、docstring だけが
    「N-01 の事前検証に使う」と嘘を言い続けた。同型の取り残しを次の DTO でも捕まえるため、
    「宣言した欄に読み手が在るか」を**構文木で**数える。

なぜ名前だけでは足りないか（本件がまさにその形）:
    「`known_ea_names`」 という綴りは合流点の別 DTO（「`RunScopeInputs`」）で読まれている。
    属性名だけを数えると、その読み手を 「`EngineBinding`」 の読み手と取り違えて緑になる。
    したがって**属性の土台がどのクラスを指すか**を注釈から解いてから数える。

測り方の限界（言えないことは言わない）:
    本器の型解決は注釈・コンストラクタ呼出・反射（「`fields`」 等）に限る。注釈の無い戻り値を
    経由した読み手は解けない。解けない読み手は「読み手 0」と出る側に倒れる——すなわち
    **偽陽性**になりうるので、宣言側の範囲は偽陽性 0 を実測した範囲だけに限る
    （`simulator/tests/unit/test_declared_fields_have_readers.py` の docstring に根拠）。
"""
from __future__ import annotations

import ast
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path

#: 走査から外す経路（生成物・第三者ソースのみ。**判断を要する除外はここに書かない**）。
EXCLUDED_PATH_PARTS = ("/__pycache__/", "/.git/", "/node_modules/", "/lightweight-charts-python-main/")

#: 要素 1 つを並べる入れ物の注釈（要素の型は第 1 引数）。
SEQUENCE_ANNOTATIONS = frozenset({
    "tuple", "list", "set", "frozenset", "Sequence", "Iterable", "Iterator", "Collection", "AbstractSet",
})
#: 鍵と値を組む入れ物の注釈（値の型は最後の引数）。
MAPPING_ANNOTATIONS = frozenset({"dict", "Mapping", "MutableMapping", "defaultdict", "OrderedDict"})

#: 欄名を書かずに全欄を配る口（反射）。第 1 引数の DTO は**全欄が読まれた**とみなす。
REFLECTIVE_READERS = frozenset({"getattr", "setattr", "hasattr", "vars", "asdict", "astuple", "fields", "replace"})

#: 対応表から値を 1 つ取り出す口。
MAPPING_LOOKUPS = frozenset({"get", "pop", "setdefault"})

OBJECT, SEQUENCE, MAPPING, PAIRS = "object", "sequence", "mapping", "pairs"

#: 宣言側の範囲（**特定の DTO を名指さない**）。Composition Root で宣言される DTO は
#: 「他の層が値を詰める注入束」であり、供給側と読み手が別パッケージに分かれる。だから
#: 読み手が消えても構築側は無傷で残り、取り残しに気づけない（ISSUE-525 の実例がここ）。
DECLARATION_SCOPE = ("simulator/main",)

#: 読み手側の範囲（リポジトリ全体）。読み手が本番か検定かは問わない。
READER_SCOPE = (".",)

#: 読み手が無くてよい欄の宣言（鍵＝(DTO, 欄)・値＝**理由**）。
#:
#: 空であることに意味がある——除外は既定ではなく、理由つきの宣言でしか成立しない。
#: 理由の無い除外・もう当たらない除外は `verify_exemptions` が落とす。
EXEMPT_UNREAD_FIELDS: "dict[tuple[str, str], str]" = {}


@dataclass(frozen=True)
class TypeRef:
    """式が指す型。実体なら ``dto``、入れ物なら ``element`` に中身を持つ（入れ子可）。"""

    kind: str
    dto: "str | None" = None
    element: "TypeRef | None" = None


def an_object(name: str) -> TypeRef:
    """実体（クラス ``name``）を指す型。"""
    return TypeRef(OBJECT, dto=name)


def a_container(kind: str, element: "TypeRef | None") -> "TypeRef | None":
    """中身が解けているときだけ入れ物の型を作る（中身不明の入れ物は解かない）。"""
    return TypeRef(kind, element=element) if element is not None else None


@dataclass(frozen=True)
class UnreadField:
    """読み手が 1 つも無い欄。"""

    dto: str
    field: str
    module: str

    def __str__(self) -> str:
        return f"{self.dto}.{self.field} ({self.module})"


class ExemptionError(AssertionError):
    """除外の宣言が規律を満たさない（理由が無い／もう当たらない）。"""


def python_files(root: Path, targets) -> "tuple[Path, ...]":
    """``targets``（root 相対のディレクトリまたはファイル）配下の `*.py` を決定的順で返す。"""
    found: "set[Path]" = set()
    for target in targets:
        path = Path(root) / target
        if path.is_file():
            found.add(path)
            continue
        for candidate in path.rglob("*.py"):
            if any(part in f"/{candidate}" for part in EXCLUDED_PATH_PARTS):
                continue
            found.add(candidate)
    return tuple(sorted(found))


def _is_dataclass(node: ast.ClassDef) -> bool:
    for decorator in node.decorator_list:
        target = decorator.func if isinstance(decorator, ast.Call) else decorator
        name = target.attr if isinstance(target, ast.Attribute) else getattr(target, "id", None)
        if name == "dataclass":
            return True
    return False


def _unstring(annotation):
    """文字列注釈を式へ戻す（`from __future__ import annotations` 下の書き方）。"""
    if isinstance(annotation, ast.Constant) and isinstance(annotation.value, str):
        try:
            return ast.parse(annotation.value, mode="eval").body
        except SyntaxError:
            return None
    return annotation


def _annotation_type(annotation) -> "TypeRef | None":
    """注釈が指す型を解く（解けなければ ``None``）。"""
    annotation = _unstring(annotation) if annotation is not None else None
    if annotation is None:
        return None
    if isinstance(annotation, ast.Name):
        return an_object(annotation.id)
    if isinstance(annotation, ast.Attribute):
        return an_object(annotation.attr)
    if isinstance(annotation, ast.BinOp) and isinstance(annotation.op, ast.BitOr):
        for side in (annotation.left, annotation.right):
            resolved = _annotation_type(side)
            if resolved is not None and resolved.dto != "None":
                return resolved
        return None
    if isinstance(annotation, ast.Subscript):
        return _subscript_type(annotation)
    return None


def _subscript_type(annotation: ast.Subscript) -> "TypeRef | None":
    head = _annotation_type(annotation.value)
    if head is None or head.kind != OBJECT:
        return None
    arguments = annotation.slice.elts if isinstance(annotation.slice, ast.Tuple) else [annotation.slice]
    arguments = [a for a in arguments if not (isinstance(a, ast.Constant) and a.value is Ellipsis)]
    if not arguments:
        return None
    if head.dto in {"Optional", "Final", "Annotated"}:
        return _annotation_type(arguments[0])
    if head.dto in SEQUENCE_ANNOTATIONS:
        return a_container(SEQUENCE, _annotation_type(arguments[0]))
    if head.dto in MAPPING_ANNOTATIONS and len(arguments) >= 2:
        return a_container(MAPPING, _annotation_type(arguments[-1]))
    return None


def _tuple_member_types(annotation) -> "tuple | None":
    """``tuple[A, B, C]`` の各位置の型（可変長・非 tuple なら ``None``）。

    位置ごとに型が違う組は 1 つの「並び」では表せない。展開して受ける側が
    どの位置に何が来るかを知るための宣言をここで解く。
    """
    annotation = _unstring(annotation) if annotation is not None else None
    if not isinstance(annotation, ast.Subscript):
        return None
    head = _annotation_type(annotation.value)
    if head is None or head.dto != "tuple" or not isinstance(annotation.slice, ast.Tuple):
        return None
    members = annotation.slice.elts
    if any(isinstance(member, ast.Constant) and member.value is Ellipsis for member in members):
        return None
    return tuple(_annotation_type(member) for member in members)


def called_name(node: ast.Call) -> "str | None":
    """呼ばれたものの名前（関数呼出ならその関数名・メソッド呼出ならそのメソッド名）。"""
    called = node.func
    if isinstance(called, ast.Attribute):
        return called.attr
    return getattr(called, "id", None)


def _declare(table: "dict[str, object]", name: str, resolved) -> None:
    """同名でも解が食い違うなら解かない（別物を同じものとして数えない）。"""
    table[name] = resolved if table.get(name, resolved) == resolved else None


class DataclassIndex:
    """走査した全 `@dataclass` の欄と宣言位置、および名前 → 型の宣言表。"""

    def __init__(self, trees: "dict[Path, ast.Module]") -> None:
        self.fields: "dict[str, dict[str, ast.expr | None]]" = defaultdict(dict)
        self.declared_in: "dict[str, str]" = {}
        self.ambiguous: "set[str]" = set()
        self.module_level: "dict[str, TypeRef | None]" = {}
        self.returns: "dict[str, TypeRef | None]" = {}
        self.return_members: "dict[str, tuple | None]" = {}
        for path, tree in trees.items():
            for node in ast.walk(tree):
                if isinstance(node, ast.ClassDef) and _is_dataclass(node):
                    self._add_dataclass(node, str(path))
        # DTO の宣言が全部そろってから名前の表を組む（後のファイルで宣言された DTO を
        # 使う表を取り逃さないため。1 ファイル 1 周で済ませると宣言順に依存してしまう）。
        for tree in trees.values():
            for node in ast.walk(tree):
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    _declare(self.returns, node.name, _annotation_type(node.returns))
                    _declare(self.return_members, node.name, _tuple_member_types(node.returns))
            for statement in tree.body:
                if isinstance(statement, ast.AnnAssign) and isinstance(statement.target, ast.Name):
                    _declare(self.module_level, statement.target.id, _annotation_type(statement.annotation))
                if isinstance(statement, ast.Assign):
                    resolved = self._literal_type(statement.value)
                    for target in statement.targets:
                        if isinstance(target, ast.Name):
                            _declare(self.module_level, target.id, resolved)

    def _literal_type(self, value) -> "TypeRef | None":
        """入れ物リテラルの型（中身が同じ DTO のコンストラクタ呼出だけで書かれている場合）。

        注釈の無い宣言表（``_FORMS = {...}``）の中身を解くためにある。中身が混ざって
        いれば解かない——解いたふりをすると、別物の欄を読んだと数えてしまう。
        """
        if isinstance(value, ast.Dict):
            return a_container(MAPPING, self._constructed_type(value.values))
        if isinstance(value, (ast.List, ast.Tuple, ast.Set)):
            return a_container(SEQUENCE, self._constructed_type(value.elts))
        return None

    def _constructed_type(self, nodes) -> "TypeRef | None":
        calls = [
            node for node in nodes
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
        ]
        if not calls or len(calls) != len(nodes):
            return None                       # 1 つでも別の書き方が混ざれば解かない
        built = {node.func.id for node in calls}
        if len(built) != 1:
            return None
        name = built.pop()
        return an_object(name) if name in self.fields else None

    def _add_dataclass(self, node: ast.ClassDef, path: str) -> None:
        if self.declared_in.get(node.name, path) != path:
            self.ambiguous.add(node.name)
        self.declared_in[node.name] = path
        for statement in node.body:
            if isinstance(statement, ast.AnnAssign) and isinstance(statement.target, ast.Name):
                self.fields[node.name][statement.target.id] = statement.annotation

    def field_type(self, dto: str, attribute: str) -> "TypeRef | None":
        """``dto.attribute`` が指す型（注釈から解けなければ ``None``）。"""
        return _annotation_type(self.fields.get(dto, {}).get(attribute))


class ReadTally(ast.NodeVisitor):
    """1 ファイルぶんの構文木から「どの DTO のどの欄が読まれたか」を数える。"""

    def __init__(
        self,
        index: DataclassIndex,
        reads: "dict[tuple[str, str], int]",
        reflected: "set[str]",
    ) -> None:
        self._index = index
        self._reads = reads
        self._reflected = reflected
        self._scopes: "list[dict[str, TypeRef]]" = [{}]

    # --- 名前 → 型の束縛 -------------------------------------------------------
    def _enter(self) -> None:
        self._scopes.append(dict(self._scopes[-1]))

    def _leave(self) -> None:
        self._scopes.pop()

    def _bind(self, name: str, resolved: "TypeRef | None") -> None:
        if resolved is not None:
            self._scopes[-1][name] = resolved

    def _lookup(self, name: str) -> "TypeRef | None":
        return self._scopes[-1].get(name) or self._index.module_level.get(name)

    def _type_of(self, node) -> "TypeRef | None":
        """式が指す型（解けなければ ``None``）。"""
        if isinstance(node, ast.Name):
            return self._lookup(node.id)
        if isinstance(node, ast.Attribute):
            base = self._type_of(node.value)
            if base is not None and base.kind == OBJECT and base.dto:
                return self._index.field_type(base.dto, node.attr)
            return None
        if isinstance(node, ast.Call):
            return self._call_type(node)
        if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
            # 並びの連結（`明示表 + 導出表`）。どちらか一方が解ければ中身は同じ型である。
            for side in (node.left, node.right):
                resolved = self._type_of(side)
                if resolved is not None and resolved.kind == SEQUENCE:
                    return resolved
            return None
        return None

    def _call_type(self, node: ast.Call) -> "TypeRef | None":
        """呼出の戻りが指す型。対応表の 「`values`」 / 「`items`」 / 引き当ても解く。"""
        called = node.func
        if isinstance(called, ast.Attribute):
            base = self._type_of(called.value)
            if base is not None and base.kind == MAPPING:
                if called.attr == "values":
                    return a_container(SEQUENCE, base.element)
                if called.attr == "items":
                    return a_container(PAIRS, base.element)
                if called.attr in MAPPING_LOOKUPS:
                    return base.element
        name = called_name(node)
        return self._index.returns.get(name) if name else None

    def _element_of(self, node) -> "TypeRef | None":
        """入れ物を回したときに 1 件ずつ出てくるものの型。"""
        resolved = self._type_of(node)
        return resolved.element if resolved is not None and resolved.kind == SEQUENCE else None

    def _bind_iteration_target(self, target, iterated) -> None:
        """回した先の名前を束ねる。2 つ組（「`items`」）は**値の側**だけを束ねる。"""
        if isinstance(target, ast.Name):
            self._bind(target.id, self._element_of(iterated))
            return
        resolved = self._type_of(iterated)
        if resolved is None or resolved.kind != PAIRS:
            return
        if isinstance(target, ast.Tuple) and len(target.elts) == 2:
            value_name = target.elts[1]
            if isinstance(value_name, ast.Name):
                self._bind(value_name.id, resolved.element)

    # --- 訪問 -----------------------------------------------------------------
    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        self._enter()
        if node.name in self._index.fields:
            self._bind("self", an_object(node.name))
        self.generic_visit(node)
        self._leave()

    def _visit_function(self, node) -> None:
        self._enter()
        arguments = node.args
        for argument in [*arguments.posonlyargs, *arguments.args, *arguments.kwonlyargs]:
            self._bind(argument.arg, _annotation_type(argument.annotation))
        self.generic_visit(node)
        self._leave()

    visit_FunctionDef = _visit_function
    visit_AsyncFunctionDef = _visit_function

    def visit_AnnAssign(self, node: ast.AnnAssign) -> None:
        if isinstance(node.target, ast.Name):
            self._bind(node.target.id, _annotation_type(node.annotation))
        self.generic_visit(node)

    def visit_Assign(self, node: ast.Assign) -> None:
        for target in node.targets:
            self._bind_assignment(target, node.value)
        self.generic_visit(node)

    def _bind_assignment(self, target, value) -> None:
        """代入の左辺を束ねる。組の展開は**位置ごとの宣言**から束ねる。"""
        if isinstance(target, ast.Name):
            self._bind(target.id, self._type_of(value))
            return
        if not isinstance(target, (ast.Tuple, ast.List)):
            return
        members = self._returned_members(value)
        if members is None or len(members) != len(target.elts):
            return
        for element, resolved in zip(target.elts, members):
            if isinstance(element, ast.Name):
                self._bind(element.id, resolved)

    def _returned_members(self, value) -> "tuple | None":
        """呼出の戻りが位置ごとに型の違う組なら、その各位置の型。"""
        if not isinstance(value, ast.Call):
            return None
        name = called_name(value)
        return self._index.return_members.get(name) if name else None

    def visit_For(self, node: ast.For) -> None:
        self._bind_iteration_target(node.target, node.iter)
        self.generic_visit(node)

    def _visit_comprehension(self, node) -> None:
        self._enter()
        for generator in node.generators:
            self._bind_iteration_target(generator.target, generator.iter)
        self.generic_visit(node)
        self._leave()

    visit_ListComp = _visit_comprehension
    visit_SetComp = _visit_comprehension
    visit_DictComp = _visit_comprehension
    visit_GeneratorExp = _visit_comprehension

    def visit_Call(self, node: ast.Call) -> None:
        if called_name(node) in REFLECTIVE_READERS and node.args:
            subject = self._type_of(node.args[0])
            if subject is None and isinstance(node.args[0], ast.Name):
                subject = an_object(node.args[0].id)     # クラスそのものを渡す形
            if subject is not None and subject.kind == OBJECT and subject.dto in self._index.fields:
                self._reflected.add(subject.dto)
        self.generic_visit(node)

    def visit_Attribute(self, node: ast.Attribute) -> None:
        base = self._type_of(node.value)
        if base is not None and base.kind == OBJECT and base.dto:
            if node.attr in self._index.fields.get(base.dto, {}):
                self._reads[(base.dto, node.attr)] += 1
        self.generic_visit(node)


def field_reads(
    index: DataclassIndex, trees: "dict[Path, ast.Module]"
) -> "tuple[dict[tuple[str, str], int], set[str]]":
    """全ファイルを 1 度ずつ走り、(DTO, 欄) ごとの読み手の数と、反射で読まれた DTO を返す。"""
    reads: "dict[tuple[str, str], int]" = defaultdict(int)
    reflected: "set[str]" = set()
    for tree in trees.values():
        ReadTally(index, reads, reflected).visit(tree)
    return reads, reflected


def verify_exemptions(exemptions, unread: "tuple[UnreadField, ...]") -> None:
    """除外の宣言が規律を満たすことを要求する（逃げ道にしない）。

    - 理由の無い除外は認めない（空白だけも同じ）。「なぜ読み手が無くてよいか」が書かれて
      いない除外は、次に読む人が是非を判断できない。
    - もう当たらない除外も認めない（読み手ができた・欄が消えた）。残った除外は
      「既定で拾わない範囲」を静かに広げる。

    「`sim_offered`」 の先例と同じ規律である: 宣言の欠落を既定へ倒さず、宣言の側で止める。
    """
    without_reason = sorted(key for key, reason in exemptions.items() if not str(reason).strip())
    if without_reason:
        raise ExemptionError(
            "除外の宣言に理由がありません: "
            + ", ".join(f"{dto}.{field}" for dto, field in without_reason)
        )
    still_unread = {(field.dto, field.field) for field in unread}
    stale = sorted(key for key in exemptions if key not in still_unread)
    if stale:
        raise ExemptionError(
            "もう当たらない除外の宣言が残っています（読み手ができた／欄が消えた）: "
            + ", ".join(f"{dto}.{field}" for dto, field in stale)
        )


def unread_declared_fields(*, root, declared_under, read_under, exemptions) -> "tuple[UnreadField, ...]":
    """読み手が 1 つも無い欄のうち、**理由つきで除外を宣言していないもの**を返す。"""
    root = Path(root)
    trees = {path: ast.parse(path.read_text(encoding="utf-8")) for path in python_files(root, read_under)}
    index = DataclassIndex(trees)
    reads, reflected = field_reads(index, trees)
    declared_paths = {str(path) for path in python_files(root, declared_under)}
    unread = tuple(
        UnreadField(dto=dto, field=field, module=str(Path(path).relative_to(root)))
        for dto, path in sorted(index.declared_in.items())
        if path in declared_paths
        for field in index.fields[dto]
        if reads[(dto, field)] == 0 and dto not in reflected
    )
    verify_exemptions(exemptions, unread)
    return tuple(field for field in unread if (field.dto, field.field) not in exemptions)
