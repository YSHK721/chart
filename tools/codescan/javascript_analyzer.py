"""JavaScript / TypeScript ソースの解析器（標準ライブラリのみ）。

構文解析器ではなくトークナイザ＋構造走査である。Python 側と違い ``ast`` が無いため、
**取れないものは黙って落とす**方針を採る（過検出より見落とし）。落とすものは
``LIMITATIONS`` に明記し、レポートへそのまま載せる。推測で「無い」と断定しない。
"""
from __future__ import annotations

from . import kinds
from .model import SHAPE_IDENT, SHAPE_NUMBER, SHAPE_STRING, Fragment, ImportEdge, ModuleFacts, Symbol, Token

#: 本解析器が構造として拾わないもの（レポートへ出す）。
LIMITATIONS = (
    "オブジェクトリテラル内のメソッド定義はシンボルとして数えない",
    "式の途中に現れる無名コールバック（`arr.map(x => ...)` 等）はシンボルとして数えない"
    "（名前付き束縛 `const f = () => {}` は入れ子でも数える）",
    "動的 import / require の引数が文字列リテラルでない場合は依存として数えない",
    "TypeScript は型注釈をトークンとして素通しする（型のみの重複は関数重複として現れる）",
)

_KEYWORDS = frozenset("""
await break case catch class const continue debugger default delete do else enum export extends
false finally for function if implements import in instanceof interface let new null of package
private protected public return static super switch this throw true try typeof var void while
with yield async get set from as declare namespace type readonly abstract keyof infer satisfies
""".split())

#: 直前がこれらなら次の ``/`` は正規表現リテラルの開始（除算ではない）。
_REGEX_ALLOWED_KEYWORDS = frozenset({
    "return", "typeof", "instanceof", "in", "of", "new", "delete", "void",
    "throw", "case", "do", "else", "yield", "await",
})
_REGEX_BLOCKED_PUNCT = frozenset({")", "]", "++", "--"})

_PUNCTUATORS = tuple(sorted({
    ">>>=", "...", "===", "!==", "**=", "<<=", ">>=", ">>>", "&&=", "||=", "??=",
    "=>", "==", "!=", "<=", ">=", "&&", "||", "??", "?.", "++", "--",
    "+=", "-=", "*=", "/=", "%=", "&=", "|=", "^=", "**", "<<", ">>",
    "{", "}", "(", ")", "[", "]", ";", ",", "<", ">", "+", "-", "*", "/", "%",
    "&", "|", "^", "!", "~", "?", ":", "=", ".", "@", "#",
}, key=len, reverse=True))

#: テンプレートリテラル式の開き／閉じ。ブロックの ``{`` ``}`` と混ざるとブレース対応が
#: 壊れるため、専用のトークン文字列を使う。
TEMPLATE_EXPR_OPEN = "${"
TEMPLATE_EXPR_CLOSE = "$}"

_IDENT_START = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ_$")
_IDENT_BODY = _IDENT_START | set("0123456789")


def tokenize_js(source: str) -> "tuple[Token, ...]":
    """JS/TS ソースをトークン列へ分解する。

    テンプレートリテラルは ``${}`` の中身を通常のコードとして分解する
    （HTML 生成テンプレート内のロジック重複を見落とさないため）。
    """
    out: "list[Token]" = []
    i, n, line = 0, len(source), 1
    # ctx: テンプレート／テンプレート内式のネスト状態。要素は ["template"] または ["expr", 深さ]。
    ctx: "list[list]" = []

    def push(text: str, shape: str) -> None:
        out.append(Token(text=text, shape=shape, line=line))

    def prev_allows_regex() -> bool:
        if not out:
            return True
        prev = out[-1]
        if prev.shape in (SHAPE_IDENT, SHAPE_NUMBER, SHAPE_STRING):
            return False
        if prev.text in _KEYWORDS:
            return prev.text in _REGEX_ALLOWED_KEYWORDS
        return prev.text not in _REGEX_BLOCKED_PUNCT

    while i < n:
        if ctx and ctx[-1][0] == "template":
            start_line = line
            chunk: "list[str]" = []
            while i < n:
                ch = source[i]
                if ch == "\\" and i + 1 < n:
                    chunk.append(source[i:i + 2])
                    line += source.count("\n", i, i + 2)
                    i += 2
                    continue
                if ch == "`":
                    break
                if ch == "$" and i + 1 < n and source[i + 1] == "{":
                    break
                if ch == "\n":
                    line += 1
                chunk.append(ch)
                i += 1
            if chunk:
                out.append(Token(text="".join(chunk), shape=SHAPE_STRING, line=start_line))
            if i < n and source[i] == "`":
                ctx.pop()
                push("`", "`")
                i += 1
            elif i < n:
                push(TEMPLATE_EXPR_OPEN, TEMPLATE_EXPR_OPEN)
                ctx.append(["expr", 0])
                i += 2
            continue

        ch = source[i]
        if ch == "\n":
            line += 1
            i += 1
            continue
        if ch in " \t\r\f\v":
            i += 1
            continue
        if source.startswith("//", i):
            i = source.find("\n", i)
            if i < 0:
                break
            continue
        if source.startswith("/*", i):
            end = source.find("*/", i + 2)
            end = n if end < 0 else end + 2
            line += source.count("\n", i, end)
            i = end
            continue
        if ch in "'\"":
            start_line, j = line, i + 1
            while j < n:
                if source[j] == "\\":
                    j += 2
                    continue
                if source[j] == ch:
                    j += 1
                    break
                if source[j] == "\n":
                    line += 1
                j += 1
            out.append(Token(text=source[i:j], shape=SHAPE_STRING, line=start_line))
            i = j
            continue
        if ch == "`":
            ctx.append(["template"])
            push("`", "`")
            i += 1
            continue
        if ch == "/" and prev_allows_regex():
            j, in_class = i + 1, False
            while j < n:
                c = source[j]
                if c == "\\":
                    j += 2
                    continue
                if c == "[":
                    in_class = True
                elif c == "]":
                    in_class = False
                elif c == "/" and not in_class:
                    j += 1
                    break
                elif c == "\n":
                    break
                j += 1
            while j < n and source[j] in "gimsuyvd":
                j += 1
            push(source[i:j], SHAPE_STRING)
            i = j
            continue
        if ch.isdigit() or (ch == "." and i + 1 < n and source[i + 1].isdigit()):
            j = i
            while j < n and (source[j].isalnum() or source[j] in "._"):
                j += 1
            push(source[i:j], SHAPE_NUMBER)
            i = j
            continue
        if ch in _IDENT_START:
            j = i
            while j < n and source[j] in _IDENT_BODY:
                j += 1
            word = source[i:j]
            push(word, word if word in _KEYWORDS else SHAPE_IDENT)
            i = j
            continue
        for punct in _PUNCTUATORS:
            if source.startswith(punct, i):
                if punct == "{" and ctx and ctx[-1][0] == "expr":
                    ctx[-1][1] += 1
                elif punct == "}" and ctx and ctx[-1][0] == "expr":
                    if ctx[-1][1] == 0:
                        # ブロックの `}` と混ぜるとブレース対応が壊れ、以降の宣言を
                        # まるごと取りこぼす。テンプレート式の閉じは別トークンにする。
                        ctx.pop()
                        push(TEMPLATE_EXPR_CLOSE, TEMPLATE_EXPR_CLOSE)
                        i += 1
                        break
                    ctx[-1][1] -= 1
                push(punct, punct)
                i += len(punct)
                break
        else:
            i += 1  # 未知の文字（Unicode 識別子など）は構造に影響しないため読み飛ばす
    return tuple(out)


def _match_brace(tokens: "tuple[Token, ...]", open_index: int, limit: int) -> int:
    """``tokens[open_index]`` が ``{`` のとき、対応する ``}`` の位置を返す。無ければ ``limit-1``。"""
    depth = 0
    for i in range(open_index, limit):
        text = tokens[i].text
        if text == "{":
            depth += 1
        elif text == "}":
            depth -= 1
            if depth == 0:
                return i
    return limit - 1


def _match_paren(tokens: "tuple[Token, ...]", open_index: int, limit: int) -> int:
    depth = 0
    for i in range(open_index, limit):
        text = tokens[i].text
        if text == "(":
            depth += 1
        elif text == ")":
            depth -= 1
            if depth == 0:
                return i
    return limit - 1


def _string_value(text: str) -> str:
    if len(text) >= 2 and text[0] in "'\"" and text[-1] == text[0]:
        return text[1:-1]
    return text


class JavaScriptAnalyzer:
    """``LanguageAnalyzer`` の JavaScript / TypeScript 実装。"""

    language = "javascript"
    extensions = frozenset({".js", ".mjs", ".cjs", ".ts", ".mts", ".tsx", ".jsx"})

    def analyze(self, path: str, source: str) -> ModuleFacts:
        loc = source.count("\n") + (0 if source.endswith("\n") or not source else 1)
        tokens = tokenize_js(source)
        symbols: "list[Symbol]" = []
        fragments: "list[Fragment]" = []
        self._scan(tokens, 0, len(tokens), path, "", in_class=False, top_level=True,
                   symbols=symbols, fragments=fragments)
        imports = tuple(self._imports(tokens, path))
        return ModuleFacts(path=path, language=self.language, loc=loc, symbols=tuple(symbols),
                           imports=imports, fragments=tuple(fragments), tokens=tokens)

    # -- 依存関係 -------------------------------------------------------------
    @staticmethod
    def _imports(tokens: "tuple[Token, ...]", path: str) -> "list[ImportEdge]":
        out: "list[ImportEdge]" = []
        n = len(tokens)
        for i, tok in enumerate(tokens):
            if tok.text in ("import", "export"):
                # `import "x"` / `import(...)` / `... from "x"` を同じ規則で拾う。
                if tok.text == "import" and i + 1 < n and tokens[i + 1].shape == SHAPE_STRING:
                    out.append(ImportEdge(path=path, spec=_string_value(tokens[i + 1].text),
                                          line=tok.line))
                    continue
                if tok.text == "import" and i + 2 < n and tokens[i + 1].text == "(" \
                        and tokens[i + 2].shape == SHAPE_STRING:
                    out.append(ImportEdge(path=path, spec=_string_value(tokens[i + 2].text),
                                          line=tok.line))
                    continue
                for j in range(i + 1, min(i + 80, n)):
                    if tokens[j].text == ";" or tokens[j].text in ("import", "export"):
                        break
                    if tokens[j].text == "from" and j + 1 < n and tokens[j + 1].shape == SHAPE_STRING:
                        out.append(ImportEdge(path=path, spec=_string_value(tokens[j + 1].text),
                                              line=tok.line))
                        break
            elif tok.text == "require" and i + 2 < n and tokens[i + 1].text == "(" \
                    and tokens[i + 2].shape == SHAPE_STRING:
                out.append(ImportEdge(path=path, spec=_string_value(tokens[i + 2].text), line=tok.line))
        return out

    # -- シンボル -------------------------------------------------------------
    def _scan(self, tokens, start: int, end: int, path: str, prefix: str, in_class: bool,
              top_level: bool, symbols: "list[Symbol]", fragments: "list[Fragment]") -> None:
        """``[start, end)`` の**直下**（ネスト外）だけを走査する。

        ``top_level`` が偽（＝関数本体の内側）のとき、値の束縛は記録しない。
        Python 側がモジュール直下の束縛だけを数えるのに合わせる（局所変数は種別集計の
        対象外）。ただし関数束縛は入れ子でも記録する（複製の単位になるため）。
        """
        i = start
        depth = 0
        exported = False
        modifiers: "list[str]" = []
        while i < end:
            text = tokens[i].text
            if text in "([{":
                depth += 1
                i += 1
                continue
            if text in ")]}":
                depth -= 1
                i += 1
                continue
            if depth != 0:
                i += 1
                continue

            if text == "export":
                exported = True
                i += 1
                if i < end and tokens[i].text == "default":
                    i += 1
                continue
            if in_class and text in ("static", "async", "get", "set", "#", "*",
                                     "public", "private", "protected", "readonly", "abstract"):
                # 修飾子の直後が識別子・`(`ならメンバ宣言。そうでなければ通常の識別子。
                if i + 1 < end and (tokens[i + 1].shape == SHAPE_IDENT or tokens[i + 1].text in ("*", "[", "#")):
                    modifiers.append(text)
                    i += 1
                    continue

            consumed = self._try_declaration(tokens, i, end, path, prefix, in_class, top_level,
                                             exported, tuple(modifiers), symbols, fragments)
            if consumed > 0:
                i += consumed
                exported = False
                modifiers = []
                continue
            if text == ";":
                exported = False
                modifiers = []
            i += 1

    def _try_declaration(self, tokens, i: int, end: int, path: str, prefix: str, in_class: bool,
                         top_level: bool, exported: bool, modifiers: "tuple[str, ...]",
                         symbols: "list[Symbol]", fragments: "list[Fragment]") -> int:
        """``i`` から宣言を 1 件読み取る。消費したトークン数を返す（0 = 宣言でない）。"""
        text = tokens[i].text

        if text in ("class", "interface") and i + 1 < end and tokens[i + 1].shape == SHAPE_IDENT:
            name = tokens[i + 1].text
            bases: "list[str]" = []
            j = i + 2
            while j < end and tokens[j].text != "{":
                if tokens[j].text in ("extends", "implements") and j + 1 < end:
                    bases.append(tokens[j + 1].text)
                j += 1
            if j >= end:
                return 0
            close = _match_brace(tokens, j, end)
            kind = kinds.INTERFACE if text == "interface" else self._class_kind(bases)
            self._emit(symbols, fragments, tokens, path, f"{prefix}{name}", kind, i, close,
                       exported, modifiers, tuple(bases))
            if text == "class":
                self._scan(tokens, j + 1, close, path, f"{prefix}{name}.", True, False,
                           symbols, fragments)
            return close - i + 1

        if text == "enum" and i + 1 < end and tokens[i + 1].shape == SHAPE_IDENT:
            j = i + 2
            while j < end and tokens[j].text != "{":
                j += 1
            close = _match_brace(tokens, j, end) if j < end else end - 1
            self._emit(symbols, fragments, tokens, path, f"{prefix}{tokens[i + 1].text}",
                       kinds.ENUM, i, close, exported, modifiers, ())
            return close - i + 1

        if text == "type" and i + 2 < end and tokens[i + 1].shape == SHAPE_IDENT and tokens[i + 2].text == "=":
            symbols.append(Symbol(path=path, name=f"{prefix}{tokens[i + 1].text}", kind=kinds.TYPE_ALIAS,
                                  line=tokens[i].line, end_line=tokens[i].line, exported=exported))
            return 3

        if text in ("function", "async") or (text == "*" and not in_class):
            j = i
            is_async = tokens[j].text == "async"
            if is_async:
                j += 1
            if j < end and tokens[j].text == "function":
                j += 1
                is_generator = j < end and tokens[j].text == "*"
                if is_generator:
                    j += 1
                if j < end and tokens[j].shape == SHAPE_IDENT:
                    name = tokens[j].text
                    brace = self._body_brace(tokens, j + 1, end)
                    if brace < 0:
                        return 0
                    close = _match_brace(tokens, brace, end)
                    kind = (kinds.GENERATOR if is_generator else
                            kinds.ASYNC_FUNCTION if is_async else kinds.FUNCTION)
                    self._emit(symbols, fragments, tokens, path, f"{prefix}{name}", kind, i, close,
                               exported, modifiers, ())
                    self._scan(tokens, brace + 1, close, path, f"{prefix}{name}.", False, False,
                               symbols, fragments)
                    return close - i + 1
            return 0

        if not in_class and text in ("const", "let", "var") and i + 1 < end and tokens[i + 1].shape == SHAPE_IDENT:
            name = tokens[i + 1].text
            if i + 2 < end and tokens[i + 2].text == "=":
                kind, close, body = self._binding_kind(tokens, i + 3, end, name)
                if top_level or kind in (kinds.ARROW_FUNCTION, kinds.FUNCTION):
                    self._emit(symbols, fragments, tokens, path, f"{prefix}{name}", kind, i, close,
                               exported, modifiers, ())
                if body >= 0:
                    self._scan(tokens, body + 1, close, path, f"{prefix}{name}.", False, False,
                               symbols, fragments)
                return close - i + 1
            return 0

        if in_class and (tokens[i].shape == SHAPE_IDENT or text in ("constructor", "[")):
            name = tokens[i].text
            j = i + 1
            if text == "[":  # 計算キー: `[Symbol.iterator]()` の類
                j = self._match_bracket(tokens, i, end) + 1
                name = "[computed]"
            if j < end and tokens[j].text == "(":
                close_paren = _match_paren(tokens, j, end)
                if close_paren + 1 < end and tokens[close_paren + 1].text == "{":
                    close = _match_brace(tokens, close_paren + 1, end)
                    self._emit(symbols, fragments, tokens, path, f"{prefix}{name}",
                               self._member_kind(name, modifiers), i, close, exported, modifiers, ())
                    self._scan(tokens, close_paren + 2, close, path, f"{prefix}{name}.", False, False,
                               symbols, fragments)
                    return close - i + 1
            if j < end and tokens[j].text == "=":
                kind, close, body = self._binding_kind(tokens, j + 1, end, name)
                if kind == kinds.ARROW_FUNCTION:
                    self._emit(symbols, fragments, tokens, path, f"{prefix}{name}", kind, i, close,
                               exported, modifiers, ())
                    if body >= 0:
                        self._scan(tokens, body + 1, close, path, f"{prefix}{name}.", False, False,
                                   symbols, fragments)
                    return close - i + 1
        return 0

    @staticmethod
    def _match_bracket(tokens, open_index: int, limit: int) -> int:
        depth = 0
        for i in range(open_index, limit):
            if tokens[i].text == "[":
                depth += 1
            elif tokens[i].text == "]":
                depth -= 1
                if depth == 0:
                    return i
        return limit - 1

    @staticmethod
    def _class_kind(bases: "list[str]") -> str:
        if any(b.endswith("Error") for b in bases):
            return kinds.EXCEPTION
        return kinds.CLASS

    @staticmethod
    def _member_kind(name: str, modifiers: "tuple[str, ...]") -> str:
        if name == "constructor":
            return kinds.CONSTRUCTOR
        if "get" in modifiers:
            return kinds.PROPERTY
        if "set" in modifiers:
            return kinds.SETTER
        if "*" in modifiers:
            return kinds.GENERATOR
        if "static" in modifiers:
            return kinds.STATIC_METHOD
        if "async" in modifiers:
            return kinds.ASYNC_METHOD
        return kinds.METHOD

    @staticmethod
    def _body_brace(tokens, i: int, end: int) -> int:
        """引数リストを読み飛ばして本体 ``{`` の位置を返す。見つからなければ -1。"""
        while i < end and tokens[i].text != "(":
            if tokens[i].text in ("{", ";"):
                return -1
            i += 1
        if i >= end:
            return -1
        close = _match_paren(tokens, i, end)
        j = close + 1
        while j < end and tokens[j].text != "{":
            if tokens[j].text == ";":
                return -1
            j += 1
        return j if j < end else -1

    def _binding_kind(self, tokens, i: int, end: int, name: str) -> "tuple[str, int, int]":
        """``= 右辺`` を見て (種別, 終端インデックス, 本体 ``{`` の位置) を決める。

        本体位置は入れ子の宣言を走査するために返す。本体が式のみのアロー関数
        （``() => expr``）は ``-1``（走査すべき宣言が入らない）。
        """
        j = i
        if j < end and tokens[j].text == "async":
            j += 1
        if j < end and tokens[j].text == "function":
            brace = self._body_brace(tokens, j + 1, end)
            if brace >= 0:
                return kinds.FUNCTION, _match_brace(tokens, brace, end), brace
        if j < end and tokens[j].text == "(":
            close = _match_paren(tokens, j, end)
            if close + 1 < end and tokens[close + 1].text == "=>":
                if close + 2 < end and tokens[close + 2].text == "{":
                    return kinds.ARROW_FUNCTION, _match_brace(tokens, close + 2, end), close + 2
                return kinds.ARROW_FUNCTION, self._statement_end(tokens, close + 2, end), -1
        if j + 1 < end and tokens[j].shape == SHAPE_IDENT and tokens[j + 1].text == "=>":
            if j + 2 < end and tokens[j + 2].text == "{":
                return kinds.ARROW_FUNCTION, _match_brace(tokens, j + 2, end), j + 2
            return kinds.ARROW_FUNCTION, self._statement_end(tokens, j + 2, end), -1
        kind = kinds.CONSTANT if name.isupper() else kinds.VARIABLE
        return kind, self._statement_end(tokens, i, end), -1

    @staticmethod
    def _statement_end(tokens, i: int, end: int) -> int:
        depth = 0
        for j in range(i, end):
            text = tokens[j].text
            if text in "([{":
                depth += 1
            elif text in ")]}":
                if depth == 0:
                    return max(i, j - 1)
                depth -= 1
            elif text == ";" and depth == 0:
                return j
        return end - 1

    @staticmethod
    def _emit(symbols, fragments, tokens, path: str, name: str, kind: str,
              start: int, close: int, exported: bool, modifiers: "tuple[str, ...]",
              bases: "tuple[str, ...]") -> None:
        start_line = tokens[start].line
        end_line = tokens[close].line
        symbols.append(Symbol(path=path, name=name, kind=kind, line=start_line, end_line=end_line,
                              exported=exported, decorators=modifiers, bases=bases))
        if kind in kinds.FRAGMENT_KINDS:
            fragments.append(Fragment(path=path, name=name, kind=kind, start_line=start_line,
                                      end_line=end_line, tokens=tokens[start:close + 1]))
