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
``unsupported_id`` / ``field`` / ``reason`` / ``tbd`` と UI 束縛 ``ui``（``keys`` /
``mode`` / ``tokens``）だけであり、宣言側の語彙をそのまま使う（同じ概念に 2 つの呼び名を
作らない）。UI 束縛は**宣言が所有する**——ここでキー名から導出すると、宣言と食い違っても
静かに 0 件になる告知が生まれる（R-9）。

ラベルは enums の MT5 実測写像（`*_UI_LABELS`・実測 `.doc/mt5_options/` 2026-09-06）が
あればそれ・無ければ列挙メンバ名である。根拠の無い MT5 文言は発明しない（基本設計 §18.3。
評価軸・時間足はスクショ未取得のためメンバ名のまま）。
"""
from __future__ import annotations

from typing import Any, Callable, Iterable, Mapping, Sequence

from simulator.sim_ui.usecase.settings_schema_ports import (
    SchemaOption,
    SettingsSchemaPort,
    UnsupportedNotice,
)
from simulator.usecase.tester_settings.enums import (
    DATES_PRESET_RANGE_KINDS,
    DATES_PRESET_UI_LABELS,
    EXECUTION_DELAY_UI_LABELS,
    FORWARD_MODE_UI_LABELS,
    OPTIMIZATION_MODE_UI_LABELS,
    PROVEN_EXECUTION_DELAYS,
    PROVISIONAL_EXECUTION_DELAYS,
    TICK_MODEL_UI_LABELS,
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


def _activation_rules() -> "dict[str, dict]":
    """キーの活性条件（UI が欄を有効/無効にするための宣言・MT5 設定タブと同形）。

    発火語彙は非対象告知（``UI_TRIGGER_*``）と同じ ``on_tokens`` / ``except_tokens``。
    ``effect`` は不活性時の扱い:

    - ``"omit"``    = 投入本文からキーごと外す（検証規則がキーの不在を要求する）。
    - ``"display"`` = 表示だけ隠し、値は**投入本文に載せ続ける**（キーは常に必須）。

    根拠は検証層の値依存規則と MT5 実画面（`.doc/ss20260906195130.jpg`）:

    - ``ForwardDate``: 規則 F（`ForwardMode == CUSTOM_DATE` ⇔ 存在）→ omit。
    - ``Visual``: 規則 B（`Optimization != DISABLED` のとき存在してはならない）→ omit。
    - ``OptimizationCriterion``: MT5 実画面で最適化が無効のとき**表示されない**が、
      規則 H（Expert 専用キーはすべて必須）によりキー自体は常に要る → display。
      omit にすると投入が E-08（H）で必ず失敗する（統合検定で実測済み 2026-09-06）。

    トークンは enums からの反復導出（数値リテラルを書かない）。front はこの宣言を
    評価するだけで、規則の第 2 実装を持たない。
    """
    return {
        "ForwardDate": {
            "key": "ForwardMode", "mode": "on_tokens",
            "tokens": [str(int(ForwardMode.CUSTOM_DATE))], "effect": "omit",
        },
        "OptimizationCriterion": {
            "key": "Optimization", "mode": "except_tokens",
            "tokens": [str(int(OptimizationMode.DISABLED))], "effect": "display",
        },
        "Visual": {
            "key": "Optimization", "mode": "on_tokens",
            "tokens": [str(int(OptimizationMode.DISABLED))], "effect": "omit",
        },
    }


def _int_enum_options(
    members: "Iterable[Any]", ui_labels: "Mapping[Any, str] | None" = None
) -> "list[SchemaOption]":
    """`IntEnum` の全メンバを「生値の文字列表記 → 表示ラベル」の選択肢へ写す。

    トークンが生値表記なのは `.ini` の値がそうだからである（`ini_codec._format_int` と同じ
    規約）。表示ラベルは enums の MT5 実測写像（`*_UI_LABELS`）があればそれ・無ければ
    メンバ名（発明しない）。表示順は列挙の宣言順＝値順であり、UI の表示順を発明しない
    （基本設計 §4.3.2）。
    """
    labels = ui_labels or {}
    return [
        SchemaOption(token=str(int(member)), label=labels.get(member, member.name))
        for member in members
    ]


def _timeframe_options() -> "list[SchemaOption]":
    """`Period` の選択肢を `TIMEFRAME_INI_LABELS` から導く（ラベル表の反復）。"""
    return [
        SchemaOption(token=label, label=timeframe.name)
        for timeframe, label in TIMEFRAME_INI_LABELS.items()
    ]


#: 列挙キー → 選択肢の作り方。キー名は `.ini` のキー（標準キー順に実在することを構築時に検査する）。
_ENUM_OPTION_BUILDERS: "dict[str, Callable[[], list[SchemaOption]]]" = {
    _PERIOD_KEY: _timeframe_options,
    "Model": lambda: _int_enum_options(TickModel, TICK_MODEL_UI_LABELS),
    "Optimization": lambda: _int_enum_options(OptimizationMode, OPTIMIZATION_MODE_UI_LABELS),
    # Dates は表示期間の種別（range_kind）も併載する（宣言は enums・表示専用）
    "Dates": lambda: [
        SchemaOption(
            token=str(int(member)),
            label=DATES_PRESET_UI_LABELS.get(member, member.name),
            range_kind=DATES_PRESET_RANGE_KINDS.get(member),
        )
        for member in DatesPreset
    ],
    "ForwardMode": lambda: _int_enum_options(ForwardMode, FORWARD_MODE_UI_LABELS),
    # 評価軸はドロップダウンの実測スクショ未取得＝ラベル写像なし（メンバ名で出す）
    "OptimizationCriterion": lambda: _int_enum_options(OptimizationCriterion),
}


class TesterSettingsSchemaCatalog(SettingsSchemaPort):
    """Tester Settings フォームの schema を列挙と注入から組み立てる `SettingsSchemaPort` 実装。

    ``key_order``: `[Tester]` の標準キー順（字句層が権威）。
    ``required_keys``: 他の選択に依らず常に必要なキー（検証層のモデルが権威）。
    ``expert_only_keys``: Expert テスト専用キー（検証層の規則 G/H が権威）。
    ``date_keys``: 値が日付であるキー（検証層 `DATE_VALUE_KEYS` が権威）。
    ``flag_keys``: 値が 0/1 の旗であるキー（検証層 `FLAG_VALUE_KEYS` が権威）。
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
        date_keys: "Sequence[str]",
        flag_keys: "Sequence[str]",
        known_ea_names: "Callable[[], Sequence[str]]",
        subject_suffix: str,
        unsupported_rules: "Mapping[str, Any]",
    ) -> None:
        self._key_order = tuple(key_order)
        self._required_keys = tuple(required_keys)
        self._expert_only_keys = frozenset(expert_only_keys)
        self._date_keys = frozenset(date_keys)
        self._flag_keys = frozenset(flag_keys)
        self._known_ea_names = known_ea_names
        self._subject_suffix = subject_suffix
        self._unsupported_rules = unsupported_rules
        self._assert_keys_exist()
        self._assert_date_keys_are_scalars()
        self._assert_flag_keys_are_scalars()
        self._assert_activation_binds_known_keys()

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

    def _assert_date_keys_are_scalars(self) -> None:
        """注入された日付キーが「標準キー順に実在する非列挙キー」であることを構築時に検査する。

        Fail-Stop にする理由: 検証層のキー名が変わったとき、`value_type` の印字が静かに
        消えて「カレンダーの出ない自由入力欄」へ縮退する（沈黙の縮退を作らない）。
        """
        missing = sorted(self._date_keys - set(self._key_order))
        if missing:
            raise ValueError(
                f"日付キーが標準キー順に存在しません: {missing}"
                f"（key_order={list(self._key_order)}）"
            )
        enum_clash = sorted(self._date_keys & set(_ENUM_OPTION_BUILDERS))
        if enum_clash:
            raise ValueError(f"日付キーが列挙キーと衝突しています: {enum_clash}")

    def _assert_flag_keys_are_scalars(self) -> None:
        """注入された旗キーが「標準キー順に実在する非列挙キー」であることを構築時に検査する。"""
        missing = sorted(self._flag_keys - set(self._key_order))
        if missing:
            raise ValueError(
                f"旗キーが標準キー順に存在しません: {missing}"
                f"（key_order={list(self._key_order)}）"
            )
        enum_clash = sorted(self._flag_keys & set(_ENUM_OPTION_BUILDERS))
        if enum_clash:
            raise ValueError(f"旗キーが列挙キーと衝突しています: {enum_clash}")

    def _assert_activation_binds_known_keys(self) -> None:
        """活性宣言が標準キー順に実在するキーだけを名指ししていることを構築時に検査する。"""
        for target, rule in _activation_rules().items():
            unknown = sorted({target, rule["key"]} - set(self._key_order))
            if unknown:
                raise ValueError(f"活性宣言が未知のキーへ束縛されています: {unknown}")

    def key_order(self) -> "tuple[str, ...]":
        return self._key_order

    def required_keys(self) -> "tuple[str, ...]":
        return self._required_keys

    def enum_options(self) -> "dict[str, list[SchemaOption]]":
        return {key: build() for key, build in _ENUM_OPTION_BUILDERS.items()}

    def scalar_specs(self) -> "dict[str, dict]":
        """列挙でないキーの仕様（標準キー順の並びを保つ）。

        ``expert_only``: 注入された Expert 専用キー集合（検証層の規則 G/H の宣言）に
        属するかの写し。**front に消費者は現状 0 件**である（実測 2026-09-08・
        ISSUE-420 残項目 2。schema から落とすか表示へ使うかは要裁定＝ここでは決めない。
        本 docstring は実態の記述であり、UI が使うという約束ではない）。
        ``value_type``（日付キーのみ ``"date"``）: 値の型。UI が入力部品（カレンダー）を
        出し分けるための宣言。どのキーが日付かは注入（検証層 `DATE_VALUE_KEYS`）が権威。
        ``proven`` / ``provisional``（`ExecutionMode` のみ）: 遅延値の**実証状態**。
        宣言は enums 1 箇所であり、ここは反復して写すだけである。
        """
        enum_keys = set(_ENUM_OPTION_BUILDERS)
        specs: "dict[str, dict]" = {}
        for key in self._key_order:
            if key in enum_keys:
                continue
            spec: "dict[str, Any]" = {"expert_only": key in self._expert_only_keys}
            if key in self._date_keys:
                spec["value_type"] = "date"
            if key in self._flag_keys:
                spec["value_type"] = "flag"
            if key == _EXECUTION_MODE_KEY:
                spec["proven"] = sorted(PROVEN_EXECUTION_DELAYS)
                spec["provisional"] = {
                    str(delay): tbd
                    for delay, tbd in sorted(PROVISIONAL_EXECUTION_DELAYS.items())
                }
                # 表示ラベル（MT5「延滞」の実測写像・enums が唯一の宣言）。無い値は
                # front が生値表記で出す（発明しない）。
                spec["labels"] = {
                    str(delay): label
                    for delay, label in sorted(EXECUTION_DELAY_UI_LABELS.items())
                }
            specs[key] = spec
        return specs

    def activation(self) -> "dict[str, dict]":
        """キーの活性条件（宣言表 `_activation_rules` の写し・導出はしない）。"""
        return _activation_rules()

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
        """非対象の告知（宣言表の全件・宣言順）。理由文言も UI 束縛も宣言から引く。

        UI 束縛（``keys`` / ``trigger`` / ``tokens``）は宣言（`UnsupportedRule.ui`）の
        写しであり、ここで導出しない。宣言を欠いた rule は **Fail-Stop** する——
        黙って配ると「どのキーにも当たらない告知」が生まれ、非対象を選んでも UI が
        何も言わないまま実行段の失敗に至る（沈黙の縮退）。
        """
        return [self._notice_of(rule) for rule in self._unsupported_rules.values()]

    def _notice_of(self, rule: "Any") -> UnsupportedNotice:
        ui = getattr(rule, "ui", None)
        keys = tuple(getattr(ui, "keys", ()) or ())
        if ui is None or not keys:
            raise ValueError(
                f"非対象の宣言 {rule.unsupported_id} に UI 束縛（効く `.ini` キー）が"
                "ありません。束縛が空の告知は投入前に一度も出ないため配りません"
            )
        unknown = sorted(set(keys) - set(self._key_order))
        if unknown:
            raise ValueError(
                f"非対象の宣言 {rule.unsupported_id} が標準キー順に無いキーへ"
                f"束縛されています: {unknown}"
            )
        return UnsupportedNotice(
            unsupported_id=rule.unsupported_id,
            field=rule.field,
            reason=rule.reason,
            tbd=rule.tbd,
            keys=keys,
            trigger=ui.mode,
            tokens=tuple(ui.tokens),
        )
