"""EA 入力名 → `build_interactor` 引数の 2 項束縛（内部設計 §4.4.1・D-02）。

1. 層名/責務:
    main 層（Composition Root）。`[TesterInputs]` の 1 行（`TesterInput`）を
    `build_interactor` のキーワード引数へ移す**束縛表**と、その値変換器を持つ。
    値の型は写像先（`build_interactor` の型注釈）が決め、本モジュールは表と
    変換器の管理だけを行う（型の写像表を手書きしない）。

2. 含む構造:
    SUBJECT_SUFFIX      : テスト対象ファイルの接尾辞（`Expert` / `Indicator` の値）。
                          字形の宣言はここ 1 箇所（Phase 8 裁定 T-6 で公開）。
    EaInputBinding      : 引数名 ＋ 変換器の 2 項束縛（1 入力＝1 エントリ）。
    EA_INPUT_BINDINGS   : ea_name → {`.ini` 入力名: 束縛}。**初期は空**（後述）。
    SCALAR_CONVERTERS   : 注釈型 → 変換器の登録表（型の追加＝1 エントリ追加）。
    scalar_converter_for: `build_interactor` の注釈から既定の変換器を導く。
    bind_ea_inputs      : 入力列 → キーワード引数 dict（未登録名は Fail-Stop）。
    ea_stem             : `Expert` パス → EA 名の語幹（§7.2 のパストラバーサル対策）。

3. 元 MQL 対応:
    `.ini` の `[TesterInputs]` セクション（MT5 が EA の input 変数を保存する領域）。
    `名前=現在値` / `名前=現在値||開始||刻み||終了||{Y|N}` の 2 形式（F-13/F-14）。

4. 依存:
    標準: dataclasses / inspect / re / typing
    外部: なし
    プロジェクト内: simulator.domain.exceptions（ConfigError）/
                    simulator.usecase.tester_settings（TesterInput）/
                    simulator.main（build_interactor＝注釈の単一ソース・**無改変**）

束縛表が空である根拠（内部設計 §4.4.1 の確定事項・推測で埋めない）:
    corpus の入力名（`LotSize` / `MAPeriod` / `MAMethod` / `MaxTradesPerDay` /
    `CheckMarketHours`）のうち、`MAMethod` は数値で保存されるのに対し
    `build_interactor` の `ma_method` は文字列語彙であり、数値と語彙の対応は本
    リポジトリ内で実証できない（TBD-19）。`MaxTradesPerDay` / `CheckMarketHours`
    に対応する引数は存在しない（シグネチャ実測）。したがって表は空で開始し、EA を
    Python 側で実装する工程（CON-01・本設計の対象外）で 1 エントリずつ追加する。
"""
from __future__ import annotations

import inspect
import re
from dataclasses import dataclass
from typing import Any, Callable

from simulator.domain.exceptions import ConfigError
from simulator.usecase.tester_settings import TesterInput

# `Expert` / `Indicator` の値の書式（`.ini` は Windows 表記＝実測 44 / 44 件）。
# `pathlib.Path` を経由しない: POSIX の Path は `\` を区切りとして扱わないため、
# `..\..\etc\passwd.ex5` が 1 つの名前として残り、語幹抽出が破れる（§7.2）。
_WINDOWS_PATH_SEPARATOR: str = "\\"
#: テスト対象ファイルの接尾辞（`Expert` / `Indicator` の値）。**公開**しているのは、
#: 対象の候補（`EA 名 + 接尾辞`）を組み立てる側（sim_ui の schema 供給・Phase 8 裁定 T-6）が
#: 字形を書き写さずに済むようにするためである。字形の宣言はここ 1 箇所に保つ。
#: 正規表現側（`framework/tester_settings/validation.py` の `pattern=r"\.ex5$"`）との
#: 統合は別 ISSUE 申し送り（T-6）。
SUBJECT_SUFFIX: str = ".ex5"

# 変換器が受理する書式（§4.4.1 の表。沈黙変換を禁止するため厳密に固定する）。
_INT_PATTERN = re.compile(r"^[+-]?\d+$")
_FLOAT_PATTERN = re.compile(r"^[+-]?\d+(\.\d+)?$")
#: `bool` の受理語彙（corpus 実測 F-13 の表記。`True` / `1` / `yes` は受理しない）。
BOOL_TOKENS: dict[str, bool] = {"true": True, "false": False}


def _to_int(text: str) -> int:
    if not _INT_PATTERN.match(text):
        raise ValueError("整数（^[+-]?\\d+$）として解釈できません")
    return int(text)


def _to_float(text: str) -> float:
    if not _FLOAT_PATTERN.match(text):
        raise ValueError("実数（^[+-]?\\d+(\\.\\d+)?$）として解釈できません")
    return float(text)


def _to_str(text: str) -> str:
    return text


def _to_bool(text: str) -> bool:
    if text not in BOOL_TOKENS:
        raise ValueError(
            f"真偽値（{' / '.join(sorted(BOOL_TOKENS))}）として解釈できません"
        )
    return BOOL_TOKENS[text]


#: 注釈型 → 変換器の登録表。対応型を増やすときは**本表に 1 エントリ追加**する
#: （`scalar_converter_for` の分岐を書き換えない＝OCP）。`bool` を `int` より先に
#: 登録する必要はない: 参照は注釈オブジェクトの同一性で行い、部分型関係を使わない。
SCALAR_CONVERTERS: dict[Any, Callable[[str], Any]] = {
    int: _to_int,
    float: _to_float,
    str: _to_str,
    bool: _to_bool,
}


@dataclass(frozen=True)
class EaInputBinding:
    """`.ini` の 1 入力を `build_interactor` の 1 引数へ移す束縛。

    ``convert`` は ``TesterInput.current``（文字列）を引数の値へ変換する純関数。
    変換不能は ``ValueError`` を送出する契約とし、呼出側（``bind_ea_inputs``）が
    ``ConfigError`` へ翻訳する（外側へ生の ``ValueError`` を漏らさない）。
    """

    param: str
    convert: Callable[[str], Any]


def _build_interactor_annotations() -> dict[str, Any]:
    """`build_interactor` の引数名 → 型注釈（唯一の型ソース）。

    ``eval_str=True`` が必須である理由（実測）: `simulator/main/__init__.py` は
    ``from __future__ import annotations`` を持つため、既定では注釈が文字列
    （``"int"``）のまま返り、型オブジェクトとして参照できない。
    """
    from simulator.main import build_interactor

    signature = inspect.signature(build_interactor, eval_str=True)
    return {
        name: parameter.annotation for name, parameter in signature.parameters.items()
    }


def scalar_converter_for(param: str) -> Callable[[str], Any]:
    """``param`` の型注釈に対応する既定の変換器を返す（写像表を手書きしない）。

    事前条件: ``param`` は `build_interactor` のキーワード引数名であり、その注釈が
    ``SCALAR_CONVERTERS`` に登録された型であること。
    事後条件: 返る関数は文字列を受けて当該型の値を返す。解釈不能は ``ValueError``。
    例外: 未知の引数名・未登録の注釈型は ``ConfigError``（推測で ``str`` へ落とさない）。
    """
    annotations = _build_interactor_annotations()
    if param not in annotations:
        raise ConfigError(
            f"build_interactor に存在しない引数名です: {param}",
            context={"param": param, "allowed": sorted(annotations)},
        )
    annotation = annotations[param]
    converter = SCALAR_CONVERTERS.get(annotation)
    if converter is None:
        raise ConfigError(
            f"文字列から変換できない型注釈です: {param}: {annotation!r}",
            context={
                "param": param,
                "annotation": repr(annotation),
                "supported": sorted(str(key) for key in SCALAR_CONVERTERS),
            },
        )
    return converter


def bind_ea_inputs(ea_name: str, inputs: "tuple[TesterInput, ...]") -> "dict[str, Any]":
    """`[TesterInputs]` の入力列を `build_interactor` のキーワード引数へ束縛する。

    事前条件: なし（``inputs`` は空でもよい）。
    事後条件: 返る dict のキーはすべて `build_interactor` の引数名である。
    例外: 束縛表に無い入力名、および変換不能な値は ``ConfigError``
        （基本設計 §6.2「写像に無い入力名は ConfigError・沈黙破棄しない」）。

    ``EA_INPUT_BINDINGS`` が空である現状では、入力を 1 つでも持つ設定は必ず
    ``ConfigError`` になる。これは仕様どおりの Fail-Stop であり（EA の売買ロジック
    は移植対象外＝CON-01 / N-01）、値を捨てて実行を続けることはしない。
    """
    table = EA_INPUT_BINDINGS.get(ea_name, {})
    bound: dict[str, Any] = {}
    for tester_input in inputs:
        binding = table.get(tester_input.name)
        if binding is None:
            raise ConfigError(
                f"EA 入力に対応する引数が未登録です: {ea_name}.{tester_input.name}",
                context={
                    "ea_name": ea_name,
                    "input_name": tester_input.name,
                    "allowed": sorted(table),
                },
            )
        try:
            bound[binding.param] = binding.convert(tester_input.current)
        except ValueError as exc:
            raise ConfigError(
                f"EA 入力の値を変換できません: {tester_input.name}="
                f"{tester_input.current!r}（{exc}）",
                context={
                    "ea_name": ea_name,
                    "input_name": tester_input.name,
                    "param": binding.param,
                    "value": tester_input.current,
                },
            ) from exc
    return bound


#: ea_name → {`.ini` 入力名: 束縛}。**初期は空**（内部設計 §4.4.1 の確定事項）。
#: 登録は EA を Python 側で実装する工程で 1 エントリずつ行う。既存の分岐・関数は
#: 1 行も変えずに追加できる（本 dict への代入だけで束縛が有効になる＝OCP）。
EA_INPUT_BINDINGS: "dict[str, dict[str, EaInputBinding]]" = {}


def ea_stem(subject_path: str) -> str:
    """`Expert` の値から EA 名の語幹を取り出す（§7.2・K-18）。

    事前条件: ``subject_path`` は `.ini` の `Expert` / `Indicator` の値（Windows 表記）。
    事後条件: 最後の ``\\`` 以降を取り、その**末尾の** ``.ex5`` を 1 回だけ除去した
        文字列を返す。
    例外: 送出しない。任意の ``str`` に対して値を返す（`rsplit` / `removesuffix` は
        一致が無くても入力をそのまま返すため、分岐も失敗経路も持たない）。

    保証しないこと（反例つき。以前の docstring は「``\\`` と ``.ex5`` は含まれない」と
    書いていたが、いずれも成立しない）:
        - ``.ex5`` が残らないこと: ``ea_stem("a.ex5.ex5") == "a.ex5"``（除去は 1 回）。
        - POSIX 区切り ``/`` が残らないこと:
          ``ea_stem("dir/sub/EA.ex5") == "dir/sub/EA"``（``/`` は区切りとして扱わない）。

    これで安全性が損なわれない理由: 得られた語幹は `known_ea_names` との**集合所属
    判定にのみ**使い、パスとして解決しない（N-01）。所属しない語幹は実行に到達せず
    `ConfigError` で止まるため、区切りや拡張子が残ること自体は実行対象の選択に影響
    しない。ファイルシステムへは一切触れない。

    ``pathlib.Path`` を経由しないのは、POSIX の ``Path`` が ``\\`` を区切りとして
    扱わず ``..\\..\\etc\\passwd.ex5`` を 1 要素とみなすためである
    （`..\\..\\etc\\passwd.ex5` → ``passwd``）。
    """
    last = subject_path.rsplit(_WINDOWS_PATH_SEPARATOR, 1)[-1]
    return last.removesuffix(SUBJECT_SUFFIX)
