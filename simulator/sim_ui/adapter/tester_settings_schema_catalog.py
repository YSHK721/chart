"""A-TesterSettingsSchemaCatalog: Tester Settings フォームの schema の単一ソース（adapter・Phase 8）。

責務（SRP）: `SettingsSchemaPort` の 6 面を、**既存の単一ソースからの反復導出だけ**で組み立てる。

導出元は 2 種類に限る:

1. **列挙**（`simulator.usecase.tester_settings.enums`）— 本モジュールが直接 import してよい
   唯一の外部語彙。`Period` のラベル・`Model` の生値・非対象の実証状態（`PROVEN` /
   `PROVISIONAL_EXECUTION_DELAYS`）はここが唯一の宣言であり、**写さずに反復して導く**。
   時間足ラベル（`M1` / `Daily` …）・`Model` の数値・対象接尾辞（`.ex5`）の
   リテラルを本モジュールへ書かないことは構造ガード
   （`tests/unit/test_settings_schema_single_source.py`）が AST で固定する。
2. **注入された外側事実** — 標準キー順（字句層 `ini_codec.STANDARD_KEY_ORDER`）・Expert 専用キー
   （検証層 `validation.EXPERT_ONLY_KEYS`）・必須キー・実行可能 EA 名（`main.known_ea_names`）・
   対象接尾辞・非対象の宣言表（`main/tester_settings/unsupported.RULES`）。いずれも本モジュールから
   見て外側（framework / main）に属するため、直接 import せず Composition Root から受け取る
   （層ゲート: `tests/unit/test_sim_ui_import_direction.py`・CLEAN_ARCH の依存方向）。

非対象の宣言（`unsupported_rules`）は**構造で受ける**（型を import しない）。読むのは
``unsupported_id`` / ``field`` / ``reason`` / ``tbd`` の 4 属性だけであり、宣言側の語彙を
そのまま使う（同じ概念に 2 つの呼び名を作らない）。

ラベルは列挙メンバ名である。MT5 の UI 文言は本リポジトリ内に根拠が無く、発明しない
（基本設計 §18.3）。
"""
from __future__ import annotations

from typing import Any, Callable, Iterable, Mapping, Sequence

from simulator.sim_ui.usecase.settings_schema_ports import (
    SchemaOption,
    SettingsSchemaPort,
    UnsupportedNotice,
)
from simulator.usecase.tester_settings.enums import (
    PROVEN_EXECUTION_DELAYS,
    PROVISIONAL_EXECUTION_DELAYS,
    TIMEFRAME_INI_LABELS,
    DatesPreset,
    ForwardMode,
    OptimizationCriterion,
    OptimizationMode,
    TickModel,
)

#: 実証状態（proven / provisional）を載せるキー。値の宣言は enums が持つ。
_EXECUTION_MODE_KEY = "ExecutionMode"
#: 期間（`.ini` は数値ではなくラベル文字列＝D-03）。
_PERIOD_KEY = "Period"


def _int_enum_options(members: "Iterable[Any]") -> "list[SchemaOption]":
    """`IntEnum` の全メンバを「生値の文字列表記 → メンバ名」の選択肢へ写す。

    トークンが生値表記なのは `.ini` の値がそうだからである（`ini_codec._format_int` と同じ
    規約）。表示順は列挙の宣言順＝値順であり、UI の表示順を発明しない（基本設計 §4.3.2）。
    """
    return [SchemaOption(token=str(int(member)), label=member.name) for member in members]


def _timeframe_options() -> "list[SchemaOption]":
    """`Period` の選択肢を `TIMEFRAME_INI_LABELS` から導く（ラベル表の反復）。"""
    return [
        SchemaOption(token=label, label=timeframe.name)
        for timeframe, label in TIMEFRAME_INI_LABELS.items()
    ]


#: 列挙キー → 選択肢の作り方。キー名は `.ini` のキー（標準キー順に実在することを構築時に検査する）。
_ENUM_OPTION_BUILDERS: "dict[str, Callable[[], list[SchemaOption]]]" = {
    _PERIOD_KEY: _timeframe_options,
    "Model": lambda: _int_enum_options(TickModel),
    "Optimization": lambda: _int_enum_options(OptimizationMode),
    "Dates": lambda: _int_enum_options(DatesPreset),
    "ForwardMode": lambda: _int_enum_options(ForwardMode),
    "OptimizationCriterion": lambda: _int_enum_options(OptimizationCriterion),
}


class TesterSettingsSchemaCatalog(SettingsSchemaPort):
    """Tester Settings フォームの schema を列挙と注入から組み立てる `SettingsSchemaPort` 実装。

    ``key_order``: `[Tester]` の標準キー順（字句層が権威）。
    ``required_keys``: 他の選択に依らず常に必要なキー（検証層のモデルが権威）。
    ``expert_only_keys``: Expert テスト専用キー（検証層の規則 G/H が権威）。
    ``known_ea_names``: 実行可能な EA 名を返す呼び出し可能（エンジンの公開アクセサへの束縛）。
    ``subject_suffix``: 対象ファイルの接尾辞（`main/tester_settings` が権威）。
    ``unsupported_rules``: 非対象の宣言表（ID → 宣言）。
    """

    def __init__(
        self,
        *,
        key_order: "Sequence[str]",
        required_keys: "Sequence[str]",
        expert_only_keys: "Sequence[str]",
        known_ea_names: "Callable[[], Sequence[str]]",
        subject_suffix: str,
        unsupported_rules: "Mapping[str, Any]",
    ) -> None:
        self._key_order = tuple(key_order)
        self._required_keys = tuple(required_keys)
        self._expert_only_keys = frozenset(expert_only_keys)
        self._known_ea_names = known_ea_names
        self._subject_suffix = subject_suffix
        self._unsupported_rules = unsupported_rules
        self._assert_keys_exist()

    def _assert_keys_exist(self) -> None:
        """本モジュールが名指しするキーが、注入されたキー順に実在することを構築時に検査する。

        Fail-Stop にする理由: 字句層のキー名が変わったとき、名指しが静かに死んで
        「選択肢が 1 つも出ないフォーム」になる。沈黙の縮退を作らない。
        """
        named = {*_ENUM_OPTION_BUILDERS, _EXECUTION_MODE_KEY}
        missing = sorted(named - set(self._key_order))
        if missing:
            raise ValueError(
                "schema が名指しするキーが標準キー順に存在しません: "
                f"{missing}（key_order={list(self._key_order)}）"
            )

    def key_order(self) -> "tuple[str, ...]":
        return self._key_order

    def required_keys(self) -> "tuple[str, ...]":
        return self._required_keys

    def enum_options(self) -> "dict[str, list[SchemaOption]]":
        return {key: build() for key, build in _ENUM_OPTION_BUILDERS.items()}

    def scalar_specs(self) -> "dict[str, dict]":
        """列挙でないキーの仕様（標準キー順の並びを保つ）。

        ``expert_only``: 注入された Expert 専用キー集合に属するか（Indicator テストでは
        持てないキーであることを UI が投入前に示せるようにする＝規則 G）。
        ``proven`` / ``provisional``（`ExecutionMode` のみ）: 遅延値の**実証状態**。
        宣言は enums 1 箇所であり、ここは反復して写すだけである。
        """
        enum_keys = set(_ENUM_OPTION_BUILDERS)
        specs: "dict[str, dict]" = {}
        for key in self._key_order:
            if key in enum_keys:
                continue
            spec: "dict[str, Any]" = {"expert_only": key in self._expert_only_keys}
            if key == _EXECUTION_MODE_KEY:
                spec["proven"] = sorted(PROVEN_EXECUTION_DELAYS)
                spec["provisional"] = {
                    str(delay): tbd
                    for delay, tbd in sorted(PROVISIONAL_EXECUTION_DELAYS.items())
                }
            specs[key] = spec
        return specs

    def expert_options(self) -> "list[SchemaOption]":
        """`Expert` の候補（実行可能 EA 名 ＋ 注入された対象接尾辞）。

        接尾辞を連結するのはここだけである（front も候補を組み立てない）。未登録名は
        実行時に N-01 で Fail-Stop するため、候補そのものを権威集合から作る。
        """
        return [
            SchemaOption(token=f"{name}{self._subject_suffix}", label=name)
            for name in self._known_ea_names()
        ]

    def unsupported(self) -> "list[UnsupportedNotice]":
        """非対象の告知（宣言表の全件・宣言順）。理由文言は書き写さず宣言から引く。"""
        return [
            UnsupportedNotice(
                unsupported_id=rule.unsupported_id,
                field=rule.field,
                reason=rule.reason,
                tbd=rule.tbd,
            )
            for rule in self._unsupported_rules.values()
        ]
