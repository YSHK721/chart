"""シンボル種別の語彙（唯一源）。

言語ごとに別の語を作らない。同じ概念には同じ語を割り当て、言語差は
``ModuleFacts.language`` で区別する（例: Python の ``def`` も JS の
``function`` 宣言も ``function``）。レポートの集計軸はこの語彙だけで決まる。
"""
from __future__ import annotations

# --- 型・名前空間 -----------------------------------------------------------
CLASS = "class"                 # 通常のクラス
PROTOCOL = "protocol"           # typing.Protocol を継承（構造的部分型の境界）
ABSTRACT_CLASS = "abstract_class"   # ABC / ABCMeta / abstractmethod を持つ
DATACLASS = "dataclass"         # @dataclass 系
ENUM = "enum"                   # Enum / IntEnum / StrEnum / Flag
TYPEDDICT = "typeddict"         # TypedDict
NAMEDTUPLE = "namedtuple"       # NamedTuple
EXCEPTION = "exception"         # 例外クラス
INTERFACE = "interface"         # TS の interface
TYPE_ALIAS = "type_alias"       # TypeAlias / type 文 / TS type

# --- 手続き -----------------------------------------------------------------
FUNCTION = "function"           # モジュール直下の関数
ASYNC_FUNCTION = "async_function"
GENERATOR = "generator"         # function* （JS）
METHOD = "method"               # クラス内の関数
ASYNC_METHOD = "async_method"
ABSTRACT_METHOD = "abstract_method"
PROPERTY = "property"           # @property / get アクセサ
SETTER = "setter"               # @x.setter / set アクセサ
STATIC_METHOD = "static_method"
CLASS_METHOD = "class_method"
CONSTRUCTOR = "constructor"     # __init__ / constructor
ARROW_FUNCTION = "arrow_function"   # JS のアロー関数束縛

# --- 値 ---------------------------------------------------------------------
CONSTANT = "constant"           # 大文字束縛（モジュール定数）
VARIABLE = "variable"           # それ以外のモジュール直下束縛

#: 「宣言単位の重複」を測る対象。値の束縛は本体を持たないため含めない。
FRAGMENT_KINDS = frozenset({
    CLASS, PROTOCOL, ABSTRACT_CLASS, DATACLASS, ENUM, TYPEDDICT, NAMEDTUPLE,
    EXCEPTION, FUNCTION, ASYNC_FUNCTION, GENERATOR, METHOD, ASYNC_METHOD,
    ABSTRACT_METHOD, PROPERTY, SETTER, STATIC_METHOD, CLASS_METHOD,
    CONSTRUCTOR, ARROW_FUNCTION,
})

#: 型・名前空間を成す種別（依存の向きを読むときの主対象）。
TYPE_KINDS = frozenset({
    CLASS, PROTOCOL, ABSTRACT_CLASS, DATACLASS, ENUM, TYPEDDICT, NAMEDTUPLE,
    EXCEPTION, INTERFACE, TYPE_ALIAS,
})
