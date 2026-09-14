"""Python ソースの解析器（``ast`` + ``tokenize``＝標準ライブラリのみ）。

推測に頼らないため、シンボル種別・依存関係は必ず ``ast`` から採る（正規表現で
`def` を数えない）。トークン列は ``tokenize`` から採り、``INDENT``/``DEDENT``/``NEWLINE`` を
専用マーカー（``<INDENT>`` 等）として残すことで、インデントが担うブロック構造を重複判定へ
反映する。マーカーは辞書リテラルの ``{`` などの実トークンと綴りが衝突しない。
"""
from __future__ import annotations

import ast
import io
import keyword
import tokenize
from bisect import bisect_left, bisect_right

from . import kinds
from .model import (SHAPE_DEDENT, SHAPE_EOL, SHAPE_IDENT, SHAPE_INDENT, SHAPE_NUMBER,
                    SHAPE_STRING, Fragment, ImportEdge, ModuleFacts, Symbol, Token)

#: ``dataclass`` とみなすデコレータ名。
_DATACLASS_DECORATORS = frozenset({"dataclass", "attrs", "define", "frozen"})
#: 列挙型とみなす基底名。
_ENUM_BASES = frozenset({"Enum", "IntEnum", "StrEnum", "IntFlag", "Flag", "ReprEnum"})
#: 抽象基底とみなす基底名。
_ABSTRACT_BASES = frozenset({"ABC", "ABCMeta"})

_SKIP_TOKENS = frozenset({
    tokenize.COMMENT, tokenize.NL, tokenize.ENCODING, tokenize.ENDMARKER,
})


def _shape(tok: tokenize.TokenInfo) -> "str | None":
    """トークンを正規化形へ畳む。``None`` は重複判定に使わない印。"""
    ttype = tok.type
    if ttype in _SKIP_TOKENS:
        return None
    if ttype == tokenize.NAME:
        # 予約語は構造の一部なので原文のまま残す。識別子だけを畳む。
        return tok.string if keyword.iskeyword(tok.string) else SHAPE_IDENT
    if ttype == tokenize.NUMBER:
        return SHAPE_NUMBER
    if ttype == tokenize.STRING:
        return SHAPE_STRING
    if ttype == tokenize.NEWLINE:
        return SHAPE_EOL
    if ttype == tokenize.INDENT:
        return SHAPE_INDENT
    if ttype == tokenize.DEDENT:
        return SHAPE_DEDENT
    if getattr(tokenize, "FSTRING_START", None) is not None and ttype in (
        tokenize.FSTRING_START, tokenize.FSTRING_MIDDLE, tokenize.FSTRING_END,
    ):
        # 3.12 以降は f 文字列が分割されるが、扱いは通常の文字列と同じにする。
        return SHAPE_STRING
    if ttype == tokenize.OP:
        return tok.string
    return None


def _tokenize(source: str) -> "tuple[tuple[Token, ...], tuple[str, ...]]":
    out: "list[Token]" = []
    errors: "list[str]" = []
    try:
        for tok in tokenize.generate_tokens(io.StringIO(source).readline):
            shape = _shape(tok)
            if shape is None:
                continue
            # 構造トークンは原文が空なので、正規化形をそのまま原文としても使う。
            text = shape if shape in (SHAPE_INDENT, SHAPE_DEDENT, SHAPE_EOL) else tok.string
            out.append(Token(text=text, shape=shape, line=tok.start[0]))
    except (tokenize.TokenError, IndentationError, SyntaxError) as exc:
        errors.append(f"tokenize: {exc}")
    return tuple(out), tuple(errors)


def _name_of(node: ast.AST) -> str:
    """式から「最後の名前」を取り出す（``a.b.Protocol`` → ``Protocol``）。"""
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    if isinstance(node, ast.Call):
        return _name_of(node.func)
    if isinstance(node, ast.Subscript):
        return _name_of(node.value)
    return ""


def _decorator_names(node: ast.AST) -> "tuple[str, ...]":
    decorators = getattr(node, "decorator_list", []) or []
    return tuple(_name_of(d) for d in decorators if _name_of(d))


def _has_abstract_method(node: ast.ClassDef) -> bool:
    for child in node.body:
        if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if any(d.startswith("abstract") for d in _decorator_names(child)):
                return True
    return False


def _class_kind(node: ast.ClassDef, bases: "tuple[str, ...]", decorators: "tuple[str, ...]") -> str:
    base_set = set(bases)
    if "Protocol" in base_set:
        return kinds.PROTOCOL
    if "TypedDict" in base_set:
        return kinds.TYPEDDICT
    if "NamedTuple" in base_set:
        return kinds.NAMEDTUPLE
    if base_set & _ENUM_BASES:
        return kinds.ENUM
    if base_set & _ABSTRACT_BASES or _has_abstract_method(node):
        return kinds.ABSTRACT_CLASS
    for keyword_arg in node.keywords:
        if keyword_arg.arg == "metaclass" and _name_of(keyword_arg.value) in _ABSTRACT_BASES:
            return kinds.ABSTRACT_CLASS
    if set(decorators) & _DATACLASS_DECORATORS:
        return kinds.DATACLASS
    if any(b in {"Exception", "BaseException"} or b.endswith(("Error", "Exception")) for b in bases):
        return kinds.EXCEPTION
    return kinds.CLASS


def _function_kind(node: "ast.FunctionDef | ast.AsyncFunctionDef", in_class: bool,
                   decorators: "tuple[str, ...]") -> str:
    is_async = isinstance(node, ast.AsyncFunctionDef)
    if in_class:
        if any(d.startswith("abstract") for d in decorators):
            return kinds.ABSTRACT_METHOD
        if "staticmethod" in decorators:
            return kinds.STATIC_METHOD
        if "classmethod" in decorators:
            return kinds.CLASS_METHOD
        if "setter" in decorators:
            return kinds.SETTER
        if "property" in decorators or "cached_property" in decorators:
            return kinds.PROPERTY
        if node.name == "__init__":
            return kinds.CONSTRUCTOR
        return kinds.ASYNC_METHOD if is_async else kinds.METHOD
    return kinds.ASYNC_FUNCTION if is_async else kinds.FUNCTION


def _dunder_all(tree: ast.Module) -> "frozenset[str] | None":
    for node in tree.body:
        targets = node.targets if isinstance(node, ast.Assign) else (
            [node.target] if isinstance(node, ast.AnnAssign) else [])
        for target in targets:
            if isinstance(target, ast.Name) and target.id == "__all__":
                value = node.value
                if isinstance(value, (ast.List, ast.Tuple, ast.Set)):
                    return frozenset(
                        el.value for el in value.elts
                        if isinstance(el, ast.Constant) and isinstance(el.value, str)
                    )
    return None


class PythonAnalyzer:
    """``LanguageAnalyzer`` の Python 実装。"""

    language = "python"
    extensions = frozenset({".py", ".pyi"})

    def analyze(self, path: str, source: str) -> ModuleFacts:
        loc = source.count("\n") + (0 if source.endswith("\n") or not source else 1)
        tokens, errors = _tokenize(source)
        try:
            tree = ast.parse(source)
        except SyntaxError as exc:
            return ModuleFacts(path=path, language=self.language, loc=loc, tokens=tokens,
                               errors=errors + (f"parse: {exc}",))

        exported_names = _dunder_all(tree)
        symbols: "list[Symbol]" = []
        imports: "list[ImportEdge]" = []
        fragments: "list[Fragment]" = []
        starts = [t.line for t in tokens]

        def slice_tokens(start: int, end: int) -> "tuple[Token, ...]":
            lo = bisect_left(starts, start)
            hi = bisect_right(starts, end)
            return tokens[lo:hi]

        def is_exported(name: str, top_level: bool) -> bool:
            if top_level and exported_names is not None:
                return name in exported_names
            return not name.startswith("_")

        def record(node: ast.AST, name: str, kind: str, qualname: str,
                   decorators: "tuple[str, ...]", bases: "tuple[str, ...]", top_level: bool) -> None:
            end_line = getattr(node, "end_lineno", node.lineno) or node.lineno
            symbols.append(Symbol(
                path=path, name=qualname, kind=kind, line=node.lineno, end_line=end_line,
                exported=is_exported(name, top_level), decorators=decorators, bases=bases,
            ))
            if kind in kinds.FRAGMENT_KINDS:
                fragments.append(Fragment(
                    path=path, name=qualname, kind=kind, start_line=node.lineno,
                    end_line=end_line, tokens=slice_tokens(node.lineno, end_line),
                ))

        def walk(body: "list[ast.stmt]", prefix: str, in_class: bool, top_level: bool) -> None:
            for node in body:
                if isinstance(node, ast.ClassDef):
                    decorators = _decorator_names(node)
                    bases = tuple(b for b in (_name_of(b) for b in node.bases) if b)
                    qualname = f"{prefix}{node.name}"
                    record(node, node.name, _class_kind(node, bases, decorators), qualname,
                           decorators, bases, top_level)
                    walk(node.body, f"{qualname}.", in_class=True, top_level=False)
                elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    decorators = _decorator_names(node)
                    qualname = f"{prefix}{node.name}"
                    record(node, node.name, _function_kind(node, in_class, decorators), qualname,
                           decorators, (), top_level)
                    walk(node.body, f"{qualname}.", in_class=False, top_level=False)
                elif isinstance(node, ast.Import):
                    for alias in node.names:
                        imports.append(ImportEdge(path=path, spec=alias.name, level=0,
                                                  line=node.lineno, names=(alias.asname or alias.name,)))
                elif isinstance(node, ast.ImportFrom):
                    imports.append(ImportEdge(
                        path=path, spec=node.module or "", level=node.level or 0, line=node.lineno,
                        names=tuple(a.name for a in node.names), is_from=True,
                    ))
                elif top_level and isinstance(node, (ast.Assign, ast.AnnAssign)):
                    self._record_binding(node, path, symbols, is_exported)
                elif hasattr(ast, "TypeAlias") and isinstance(node, ast.TypeAlias):
                    name = _name_of(node.name)
                    symbols.append(Symbol(path=path, name=f"{prefix}{name}", kind=kinds.TYPE_ALIAS,
                                          line=node.lineno, end_line=node.end_lineno or node.lineno,
                                          exported=is_exported(name, top_level)))
                elif isinstance(node, (ast.If, ast.Try, ast.With, ast.For, ast.While)):
                    # 条件付き定義・try/except import は実在する。素通りさせない。
                    for attr in ("body", "orelse", "finalbody"):
                        walk(getattr(node, attr, []) or [], prefix, in_class, top_level)
                    for handler in getattr(node, "handlers", []) or []:
                        walk(handler.body, prefix, in_class, top_level)

        walk(tree.body, "", in_class=False, top_level=True)
        return ModuleFacts(
            path=path, language=self.language, loc=loc, symbols=tuple(symbols),
            imports=tuple(imports), fragments=tuple(fragments), tokens=tokens, errors=errors,
        )

    @staticmethod
    def _record_binding(node: "ast.Assign | ast.AnnAssign", path: str,
                        symbols: "list[Symbol]", is_exported) -> None:
        """モジュール直下の束縛を定数・変数・型エイリアスとして記録する。"""
        targets = node.targets if isinstance(node, ast.Assign) else [node.target]
        annotation = _name_of(node.annotation) if isinstance(node, ast.AnnAssign) and node.annotation else ""
        for target in targets:
            if not isinstance(target, ast.Name):
                continue
            if annotation == "TypeAlias":
                kind = kinds.TYPE_ALIAS
            elif target.id.isupper():
                kind = kinds.CONSTANT
            else:
                kind = kinds.VARIABLE
            symbols.append(Symbol(
                path=path, name=target.id, kind=kind, line=node.lineno,
                end_line=getattr(node, "end_lineno", node.lineno) or node.lineno,
                exported=is_exported(target.id, True),
            ))
