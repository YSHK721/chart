"""codescan の値オブジェクト定義（言語非依存）。

ここに置くのは「解析器が何を出すか」の契約だけである。言語固有の知識
（Python の ast、JS のトークン規則）は各 analyzer 側に閉じ、本モジュールへ
持ち込まない。重複検出・依存解析・レポートはすべて本モジュールの型だけを
入力に取るため、言語追加は analyzer の追加のみで済む（OCP）。
"""
from __future__ import annotations

from dataclasses import dataclass, field

#: 正規化トークンの記号。Type-2 クローン（識別子・リテラルだけが異なる複製）を
#: 同一視するために、識別子と各リテラルをこの記号へ畳む。
SHAPE_IDENT = "ID"
SHAPE_NUMBER = "NUM"
SHAPE_STRING = "STR"

#: 構造だけを表すトークン（Python のインデント・論理行末）。ブロック単位の重複判定では
#: 構造の一致を見るために必要だが、**1 行単位の比較では外す**。ネストの深さが違うだけの
#: 同じ 1 行が別物に見えてしまい、行ソートで隣接しなくなるため。
#: 実トークン（辞書リテラルの ``{``・JS の ``;``）と衝突しない綴りにする。
SHAPE_INDENT = "<INDENT>"
SHAPE_DEDENT = "<DEDENT>"
SHAPE_EOL = "<EOL>"
STRUCTURAL_SHAPES = frozenset({SHAPE_INDENT, SHAPE_DEDENT, SHAPE_EOL})


@dataclass(frozen=True)
class Token:
    """重複検出の最小単位。

    Attributes:
        text: 原文どおりのトークン（Type-1 判定に使う）。
        shape: 正規化形（Type-2 判定に使う）。識別子は ``ID``、数値は ``NUM``、
            文字列は ``STR`` へ畳む。予約語・演算子・区切りは原文のまま。
        line: 1 始まりの行番号。
    """

    text: str
    shape: str
    line: int


@dataclass(frozen=True)
class Symbol:
    """モジュールが定義する要素 1 件（種別の集計対象）。

    Attributes:
        path: リポジトリ根からの相対パス。
        name: 修飾名（``Outer.inner`` 形式）。
        kind: 種別。``KIND_*`` 参照。言語をまたいで同義の語を使う
            （例: Python の ``def`` も JS の ``function`` も ``function``）。
        line: 定義開始行（デコレータ・修飾子を除いた宣言行）。
        end_line: 定義終了行。
        exported: 外部公開面か。Python は ``__all__``／先頭 ``_`` 規約、
            JS は ``export`` の有無で判定する。
        decorators: デコレータ・修飾子の名前（JS では ``static``/``async`` 等）。
        bases: 基底クラス名（クラス系のみ。それ以外は空）。
    """

    path: str
    name: str
    kind: str
    line: int
    end_line: int
    exported: bool = False
    decorators: "tuple[str, ...]" = ()
    bases: "tuple[str, ...]" = ()


@dataclass(frozen=True)
class ImportEdge:
    """1 件の import 宣言。解決前の生の指定子を保持する。

    Attributes:
        path: import している側のファイル（リポジトリ相対）。
        spec: 指定子そのもの（``a.b.c`` / ``./x.js`` / ``pandas``）。
        level: Python の相対 import 段数（``from .. import x`` なら 2）。JS は 0。
        line: 行番号。
        names: 取り込んだ名前（``import a`` のように無い場合は空）。
        is_from: ``from X import a, b`` 形式か。``a`` がサブモジュールでありうるため、
            解決時に ``X/a.py`` を先に探す必要がある（これを見ないと
            ``from . import kinds`` がパッケージ ``__init__`` への依存に化け、
            存在しない循環を作る）。
    """

    path: str
    spec: str
    level: int = 0
    line: int = 0
    names: "tuple[str, ...]" = ()
    is_from: bool = False


@dataclass(frozen=True)
class Fragment:
    """重複判定の単位となるコード片（関数・メソッド・クラス本体）。

    Attributes:
        path: リポジトリ相対パス。
        name: 修飾名。
        kind: ``Symbol.kind`` と同じ語彙。
        start_line / end_line: 行範囲（両端含む）。
        tokens: 当該範囲のトークン列。
    """

    path: str
    name: str
    kind: str
    start_line: int
    end_line: int
    tokens: "tuple[Token, ...]"

    @property
    def line_count(self) -> int:
        return self.end_line - self.start_line + 1


@dataclass(frozen=True)
class ModuleFacts:
    """1 ファイルから抽出した事実の全体。

    Attributes:
        path: リポジトリ相対パス。
        language: ``python`` / ``javascript`` 等。
        loc: 物理行数。
        symbols / imports / fragments: 上記各型の列。
        tokens: ファイル全体のトークン列（行をまたぐブロック重複の走査に使う）。
        errors: 解析に失敗した理由（構文エラー等）。空なら成功。
    """

    path: str
    language: str
    loc: int
    symbols: "tuple[Symbol, ...]" = ()
    imports: "tuple[ImportEdge, ...]" = ()
    fragments: "tuple[Fragment, ...]" = ()
    tokens: "tuple[Token, ...]" = ()
    errors: "tuple[str, ...]" = ()


@dataclass(frozen=True)
class Occurrence:
    """重複クラスタの構成要素 1 件（どのファイルのどの範囲か）。"""

    path: str
    name: str
    kind: str
    start_line: int
    end_line: int

    @property
    def line_count(self) -> int:
        return self.end_line - self.start_line + 1


@dataclass
class Clone:
    """重複クラスタ 1 件。

    Attributes:
        clone_type: ``type-1``（完全一致）／``type-2``（識別子・リテラルのみ相違）。
        unit: ``function``（宣言単位）／``block``（宣言をまたぐ連続領域）。
        token_count: 1 件あたりのトークン数。
        occurrences: 出現箇所。2 件以上。
        removable_lines: 単一ソース化した場合に消える行数の見積り
            （= 総行数 − 最長の 1 件）。優先順位付けの指標。
    """

    clone_type: str
    unit: str
    token_count: int
    occurrences: "list[Occurrence]" = field(default_factory=list)

    @property
    def removable_lines(self) -> int:
        if not self.occurrences:
            return 0
        total = sum(o.line_count for o in self.occurrences)
        return total - max(o.line_count for o in self.occurrences)

    @property
    def paths(self) -> "list[str]":
        return sorted({o.path for o in self.occurrences})

    @property
    def cross_file(self) -> bool:
        return len(self.paths) > 1
