"""`[Tester]` / `[TesterInputs]` の検証層（pydantic 検証 DTO ＋ 例外翻訳）。

1. 層名/責務:
    framework 層。`.ini` の生文字列（字句層が取り出した ``Mapping[str, str]`` と
    入力行）を検証し、内側 DTO（``TesterSettings``＝dataclass）へ変換する唯一の場所。
    pydantic はこのモジュールにのみ存在する（内部設計 §3.3 I-3）。検証に失敗した
    場合は違反を ``SettingsError`` 系へ**決定論的に翻訳**して送出し、pydantic 型を
    上位（loader / main / usecase）へ漏らさない。I/O は行わない。

2. 含む構造:
    _strict_int / _strict_decimal / _strict_date / _timeframe_label:
        `mode="before"` の厳格書式バリデータ 4 種（pydantic の緩い型強制を使わない）。
        数値・日付の字形は **ASCII 限定**（`[0-9]`）である（是正 3・レビュー指摘 🟡-3）。
    _TesterIniModel      : `[Tester]` の検証付き DTO（``extra="forbid"``＝規則 P）。
    _TesterInputsModel   : `[TesterInputs]` の行の検証付き DTO（規則 Q）。
    _PRESENCE_RULES      : 規則 D・E・G・H（キーの**存在**だけで判定できる規則）。
    _AFTER_RULES         : 規則 B・F・K（型付き値を要する規則）。
    KEY_RULES            : `.ini` キー → (規則 ID, 読取器) の表。
    _translate           : 違反レコード列 → ``SettingsError`` 1 件の翻訳（§4.3.3）。
    build_settings       : 検証 → ``TesterSettings`` 構築の入口。

    キー集合・標準キー順・`||` 分解・値の表記規則は**字句層が唯一の宣言**を持つ
    （``STANDARD_KEY_ORDER`` / ``TESTER_KEYS`` / ``split_input_value`` /
    ``TESTER_KEY_SPECS`` / ``format_date_token``）。本モジュールはそれを import して
    使い、書き直さない（この宣言は
    `TestValueNotationHasASingleDeclaration` が機械的に検査する＝是正 B）。

3. 元 MQL 対応:
    `[Tester]` の各キーは MT5 ストラテジーテスター Settings タブの 1 コントロールに
    対応する（基本設計 §2.2.1）。pydantic のフィールド名は `.ini` のキー名
    （CamelCase）と完全一致させる。理由: ``extra="forbid"`` による未知キー拒否
    （規則 P）を、キー名の写像表を別に持たずに成立させるため。

4. 依存:
    標準: re / dataclasses / datetime / collections.abc / typing
    外部: pydantic 2.13.4（本モジュール限定）
    プロジェクト内: simulator.adapter.tester_settings.ini_codec /
                    simulator.domain.tester_settings_exceptions /
                    simulator.usecase.tester_settings

設計上の要点:
    1. **列挙はモジュール参照（``enums.X``）でのみ使う**。`.ini` のキー名と同名の
       列挙（`ForwardMode` / `OptimizationCriterion`）が存在し、モデルのフィールド名
       が同名シンボルを遮蔽するため、直 import すると注釈解決時に `None | None` に
       なって import 自体が壊れる。モジュール参照にすると、キー名と同名の列挙が
       将来増えても同じ罠に落ちない（構造的な再発防止）。
    2. 規則の追加は登録簿へ関数を 1 つ足すだけで完結し、既存の分岐は書き換えない
       （OCP）。規則 ID は違反レコードを経て必ず ``context["rule_id"]`` に載る
       （規則・例外・テストの 1:1:1 対応）。
    3. 規則 B は E-03（``SettingsActivationError``）。UI の活性依存に由来する制約で
       あり「同時に指定できないキーの衝突」ではない（基本設計 §4.5.5・裁定済み）。
    4. ``context["value"]`` には**生トークン**（`.ini` の文字列）を載せる。解釈後の
       値（`int` / `date`）を載せると原典の表記が失われる（R7）。
"""
from __future__ import annotations

import re
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from typing import Annotated, Any, Literal, NoReturn

from pydantic import (
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    PrivateAttr,
    ValidationError,
    ValidationInfo,
    model_validator,
)
from pydantic_core import PydanticCustomError

from simulator.adapter.tester_settings.ini_codec import (
    INPUT_FIELD_COUNTS,
    INPUT_FIELD_SEPARATOR,
    INPUT_FLAG_TRUE,
    INPUT_FLAGS,
    MAX_INPUT_LINES,
    SECTION_TESTER_INPUTS,
    STANDARD_KEY_ORDER,
    TESTER_KEYS,
    format_date_token,
    split_input_value,
)
from simulator.domain.tester_settings_exceptions import (
    IniFormatError,
    SettingsActivationError,
    SettingsError,
    SettingsKeyConflictError,
    SettingsKeyMissingError,
    SettingsValueError,
    UnknownSettingKeyError,
    UnknownSettingValueError,
)
from simulator.usecase.tester_settings import enums
from simulator.usecase.tester_settings.models import (
    DateRange,
    IniDocument,
    TesterInput,
    TesterSettings,
)

# ---------------------------------------------------------------------------
# 書式（R7・R10・R11）。モジュール読込時に 1 度だけコンパイルする（§7.1）。
# ---------------------------------------------------------------------------
# 数字クラスは ``\d`` ではなく ``[0-9]`` を明示する（是正 3・レビュー指摘 🟡-3）。
# ``\d`` は Unicode 数字（全角 `１`・アラビア・インド数字 `١` 等）に一致し、``int`` /
# ``float`` / ``date`` もそれらを受理するため、`Model="１"` が `1` として通り、値が
# 無言で書き換わる（`to_mapping` 出力が入力トークンと一致しなくなる）。MT5 が生成し
# 得ない字形は Fail-Stop する。小数点・日付の区切りは ASCII の `.` のみである。
_INT_PATTERN = re.compile(r"[+-]?[0-9]+")
_DECIMAL_PATTERN = re.compile(r"[+-]?[0-9]+(\.[0-9]+)?")
_DATE_PATTERN = re.compile(r"([0-9]{4})\.([0-9]{2})\.([0-9]{2})")

#: `[TesterInputs]` の入力名の長さ上限（基本設計 §4.2.2）。
MAX_INPUT_NAME_CHARS: int = 63

#: Expert テストにのみ存在するキー（基本設計 F-12。規則 G / H の対象）。
#: 「Expert 専用キー」の知識はこの 1 箇所だけが持つ（員数リテラルを持たない）。
EXPERT_ONLY_KEYS: tuple[str, ...] = (
    "Optimization",
    "ForwardMode",
    "Deposit",
    "Currency",
    "ProfitInPips",
    "Leverage",
    "ExecutionMode",
    "OptimizationCriterion",
)

#: 対象種別を決めるキー（規則 D）。
SUBJECT_KEYS: dict[str, enums.SubjectKind] = {
    "Expert": enums.SubjectKind.EXPERT,
    "Indicator": enums.SubjectKind.INDICATOR,
}

#: 期間指定のキー（規則 E）。
PRESET_DATE_KEY: str = "Dates"
CUSTOM_DATE_KEYS: tuple[str, ...] = ("FromDate", "ToDate")


def _as_text(value: Any) -> Any:
    """``context`` へ載せる値を JSON 直列化可能な形へ落とす（§4.5.2 規約 1）。"""
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    return str(value)


def _error_record(code: str, rule_id: str, **context: Any) -> dict[str, Any]:
    """違反 1 件を pydantic の ``errors()`` と同じ形の辞書で表す。

    pydantic 由来の違反（フィールド制約）と本モジュール由来の違反（規則）を同じ形に
    そろえることで、優先順位表による選択を 1 つの実装で行えるようにする。
    """
    ctx: dict[str, Any] = {"rule_id": rule_id}
    ctx.update(
        {name: _as_text(value) if name == "value" else value for name, value in context.items()}
    )
    return {"type": code, "loc": (), "msg": str(context.get("reason", code)), "ctx": ctx}


def _raise_rule(code: str, rule_id: str, **context: Any) -> NoReturn:
    """規則違反を ``PydanticCustomError`` として送出する（翻訳表が例外型を決める）。"""
    record = _error_record(code, rule_id, **context)
    raise PydanticCustomError(code, "設定の規則に違反しています", record["ctx"])


def _reject(value: Any, info: ValidationInfo, expected: str) -> NoReturn:
    """書式違反を E-04 へ翻訳される ``PydanticCustomError`` として送出する。"""
    key = info.field_name or ""
    raise PydanticCustomError(
        "settings_value",
        "設定値の書式が不正です",
        {"key": key, "value": _as_text(value), "expected": expected, "rule_id": rule_id_for(key)},
    )


# ---------------------------------------------------------------------------
# 厳格書式バリデータ（§4.3.1）
# ---------------------------------------------------------------------------
def _strict_int(value: Any, info: ValidationInfo) -> Any:
    """`^[+-]?[0-9]+$` に一致する文字列のみ ``int`` へ変換する。

    `1.0`・空白・`0x1` は不正。**ASCII 数字のみ**を受理し、全角 `１`・アラビア・インド
    数字 `١` 等の Unicode 数字は拒否する（是正 3。受理すると ``int`` が黙って `1` へ
    変換し、原典の表記が失われる）。
    """
    if value is None:
        return None
    if not isinstance(value, str) or not _INT_PATTERN.fullmatch(value):
        _reject(value, info, "10 進整数の文字列（例: '0' / '-1' / '50'）")
    return int(value)


def _strict_decimal(value: Any, info: ValidationInfo) -> Any:
    """`^[+-]?[0-9]+(\\.[0-9]+)?$` のみ許容する（指数表記・``inf``・``nan`` は不正）。

    小数点は ASCII の `.` のみ、数字は **ASCII 数字のみ**（是正 3）。
    """
    if value is None:
        return None
    if not isinstance(value, str) or not _DECIMAL_PATTERN.fullmatch(value):
        _reject(value, info, "10 進数の文字列（例: '139500' / '0.01'）")
    return float(value)


def _strict_date(value: Any, info: ValidationInfo) -> Any:
    """`YYYY.MM.DD`（ゼロ埋め 2 桁）かつ実在する日付のみ許容する（R10）。

    数字は **ASCII 数字のみ**、区切りは ASCII の `.` のみ（是正 3）。
    """
    if value is None:
        return None
    matched = _DATE_PATTERN.fullmatch(value) if isinstance(value, str) else None
    if matched is None:
        _reject(value, info, "YYYY.MM.DD 形式の文字列")
    try:
        return date(int(matched.group(1)), int(matched.group(2)), int(matched.group(3)))
    except ValueError:
        _reject(value, info, "実在する日付（YYYY.MM.DD）")


def _timeframe_label(value: Any, info: ValidationInfo) -> Any:
    """`Period` のラベルを ``Timeframe`` へ写像する（未知ラベルは E-05・規則 O）。"""
    if value is None or isinstance(value, enums.Timeframe):
        return value
    timeframe = enums.INI_LABEL_TO_TIMEFRAME.get(value) if isinstance(value, str) else None
    if timeframe is None:
        raise PydanticCustomError(
            "unknown_setting_value",
            "未知の時間足ラベルです",
            {
                "key": info.field_name or "Period",
                "value": _as_text(value),
                "allowed": sorted(enums.TIMEFRAME_INI_LABELS.values()),
                "rule_id": "O",
            },
        )
    return timeframe


_StrictInt = BeforeValidator(_strict_int)
_StrictDecimal = BeforeValidator(_strict_decimal)
_StrictDate = BeforeValidator(_strict_date)
_TimeframeLabel = BeforeValidator(_timeframe_label)


# ---------------------------------------------------------------------------
# 規則の登録簿（OCP: 規則の追加は関数 1 つの追加で完結する）
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class _Rule:
    """登録された規則 1 件。"""

    rule_id: str
    check: Callable[..., Any]


#: キーの**存在**だけで判定できる規則（D・E・G・H）。値の検証結果に依存しないため
#: pydantic の外で評価し、値の違反（E-04 / E-05）と同時に収集できるようにする。
#: これがないと「値が 1 つ不正なだけで構造の矛盾が報告されない」ことになり、
#: 優先順位表（§4.3.3）が意図どおり働かない。
_PRESENCE_RULES: list[_Rule] = []
#: 型付き値を要する規則（B・F・K）。`model_validator(mode="after")` から適用する。
_AFTER_RULES: list[_Rule] = []


def _presence_rule(rule_id: str) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    """キー存在規則を登録するデコレータ。"""

    def decorate(func: Callable[..., Any]) -> Callable[..., Any]:
        _PRESENCE_RULES.append(_Rule(rule_id=rule_id, check=func))
        return func

    return decorate


def _after_rule(rule_id: str) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    """値依存規則を登録するデコレータ。"""

    def decorate(func: Callable[..., Any]) -> Callable[..., Any]:
        _AFTER_RULES.append(_Rule(rule_id=rule_id, check=func))
        return func

    return decorate


# --- 規則 D・E・G・H（キーの存在のみで判定） --------------------------------
def _subject_kind_of(keys: frozenset[str]) -> enums.SubjectKind | None:
    """存在するキーから対象種別を決める（両方・両欠落は規則 D が拒否する）。"""
    present = [kind for key, kind in SUBJECT_KEYS.items() if key in keys]
    return present[0] if len(present) == 1 else None


@_presence_rule("D")
def _rule_d_subject_exclusive(keys: frozenset[str]) -> dict[str, Any] | None:
    """`Expert` と `Indicator` は排他かつ、いずれか 1 つが必須（規則 D）。"""
    present = [key for key in SUBJECT_KEYS if key in keys]
    if len(present) > 1:
        return _error_record("settings_key_conflict", "D", keys=present)
    if not present:
        return _error_record("settings_key_missing", "D", keys=list(SUBJECT_KEYS))
    return None


@_presence_rule("E")
def _rule_e_daterange_exclusive(keys: frozenset[str]) -> dict[str, Any] | None:
    """`Dates` と `FromDate`/`ToDate` は排他かつ、いずれかの形式が必須（規則 E）。"""
    has_preset = PRESET_DATE_KEY in keys
    custom = [key for key in CUSTOM_DATE_KEYS if key in keys]
    if has_preset and custom:
        return _error_record("settings_key_conflict", "E", keys=[PRESET_DATE_KEY, *custom])
    if not has_preset and not custom:
        return _error_record(
            "settings_key_missing", "E", keys=[PRESET_DATE_KEY, *CUSTOM_DATE_KEYS]
        )
    if not has_preset and len(custom) < len(CUSTOM_DATE_KEYS):
        missing = [key for key in CUSTOM_DATE_KEYS if key not in keys]
        return _error_record("settings_key_missing", "E", keys=missing)
    return None


@_presence_rule("G")
def _rule_g_indicator_keys(keys: frozenset[str]) -> dict[str, Any] | None:
    """Indicator テストは Expert 専用キーを持たない（規則 G）。"""
    if _subject_kind_of(keys) is not enums.SubjectKind.INDICATOR:
        return None
    present = [key for key in EXPERT_ONLY_KEYS if key in keys]
    if not present:
        return None
    return _error_record(
        "settings_key_conflict",
        "G",
        keys=["Indicator", *present],
        subject_kind=enums.SubjectKind.INDICATOR.value,
    )


@_presence_rule("H")
def _rule_h_expert_keys(keys: frozenset[str]) -> dict[str, Any] | None:
    """Expert テストは Expert 専用キーがすべて必須（規則 H）。"""
    if _subject_kind_of(keys) is not enums.SubjectKind.EXPERT:
        return None
    missing = [key for key in EXPERT_ONLY_KEYS if key not in keys]
    if not missing:
        return None
    return _error_record(
        "settings_key_missing", "H", keys=missing, subject_kind=enums.SubjectKind.EXPERT.value
    )


def _presence_violations(keys: frozenset[str]) -> list[dict[str, Any]]:
    """キー存在規則を登録順に評価し、違反レコードを全件返す。"""
    records = [rule.check(keys) for rule in _PRESENCE_RULES]
    return [record for record in records if record is not None]


# ---------------------------------------------------------------------------
# `[Tester]` の検証付き DTO
# ---------------------------------------------------------------------------
class _TesterIniModel(BaseModel):
    """`[Tester]` セクションの検証付き DTO（framework 層限定）。

    フィールド名は `.ini` のキー名（CamelCase）と完全一致させ、宣言順は字句層の
    ``STANDARD_KEY_ORDER`` と一致させる（一致はモジュール読込時に機械検査する）。
    列挙は ``enums.X`` で参照する（フィールド名による遮蔽を構造的に避ける）。
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    Expert: Annotated[str | None, Field(min_length=1, max_length=255, pattern=r"\.ex5$")] = None
    Indicator: Annotated[str | None, Field(min_length=1, max_length=255, pattern=r"\.ex5$")] = None
    Symbol: Annotated[str, Field(min_length=1, max_length=31)]
    Period: Annotated[enums.Timeframe, _TimeframeLabel]
    Optimization: Annotated[enums.OptimizationMode | None, _StrictInt] = None
    Model: Annotated[enums.TickModel, _StrictInt]
    Dates: Annotated[enums.DatesPreset | None, _StrictInt] = None
    FromDate: Annotated[date | None, _StrictDate] = None
    ToDate: Annotated[date | None, _StrictDate] = None
    ForwardMode: Annotated[enums.ForwardMode | None, _StrictInt] = None
    ForwardDate: Annotated[date | None, _StrictDate] = None
    Deposit: Annotated[float | None, _StrictDecimal, Field(gt=0, le=1e12)] = None
    Currency: Annotated[str | None, Field(pattern=r"^[A-Z]{3}$")] = None
    ProfitInPips: Annotated[Literal[0, 1] | None, _StrictInt] = None
    Leverage: Annotated[int | None, _StrictInt, Field(ge=1, le=1000)] = None
    ExecutionMode: Annotated[int | None, _StrictInt, Field(ge=-(2**31), le=2**31 - 1)] = None
    OptimizationCriterion: Annotated[enums.OptimizationCriterion | None, _StrictInt] = None
    Visual: Annotated[Literal[0, 1] | None, _StrictInt] = None

    @model_validator(mode="after")
    def _apply_after_rules(self) -> "_TesterIniModel":
        """登録済みの値依存規則を登録順に適用する（規則の追加で本関数は変わらない）。"""
        for rule in _AFTER_RULES:
            rule.check(self)
        return self


@_after_rule("B")
def _rule_b_visual_exclusive(model: _TesterIniModel) -> None:
    """`Optimization != DISABLED` のとき `Visual` は存在してはならない（規則 B）。

    送出は E-03（``SettingsActivationError``）。UI の活性依存に由来する制約であり、
    キーの衝突（E-02）ではない（基本設計 §4.5.5・``SettingsActivationError`` の
    docstring「規則 B・S」）。
    """
    if model.Optimization is None or model.Optimization is enums.OptimizationMode.DISABLED:
        return
    if model.Visual is not None:
        # ``value`` は載せない: 生トークンは翻訳器が ``key`` から引く（R7）。
        _raise_rule(
            "settings_activation",
            "B",
            field="visual",
            key="Visual",
            expected="Optimization != 0 のとき Visual キーは存在しない",
        )


@_after_rule("F")
def _rule_f_forward_date(model: _TesterIniModel) -> None:
    """`ForwardMode == CUSTOM_DATE` ⇔ `ForwardDate` の存在（規則 F）。"""
    is_custom = model.ForwardMode is enums.ForwardMode.CUSTOM_DATE
    if is_custom and model.ForwardDate is None:
        _raise_rule("settings_key_missing", "F", keys=["ForwardDate"])
    if not is_custom and model.ForwardDate is not None:
        _raise_rule("settings_key_conflict", "F", keys=["ForwardDate", "ForwardMode"])


@_after_rule("K")
def _rule_k_date_order(model: _TesterIniModel) -> None:
    """`FromDate <= ToDate`（規則 K）。"""
    if model.FromDate is None or model.ToDate is None:
        return
    if model.FromDate > model.ToDate:
        # ``value`` は載せない: 生トークンは翻訳器が ``key`` から引く（R7）。
        _raise_rule(
            "settings_value",
            "K",
            key="FromDate",
            expected=f"ToDate（{format_date_token(model.ToDate)}）以下",
        )


# ---------------------------------------------------------------------------
# `[TesterInputs]` の検証付き DTO（規則 Q）
# ---------------------------------------------------------------------------
class _TesterInputsModel(BaseModel):
    """`[TesterInputs]` の行の検証付き DTO（規則 Q）。

    行原文（``"名前=値"``）を受け取り、``||`` 分解・フラグ・名前の一意性・名前長・
    行数上限を検査して ``TesterInput`` を構築する。字句層も読込経路で R8 を検査する
    が、``tester_settings_from_mapping``（字句層を経由しない経路）でも同じ拒否が
    成立する必要があるため、``TesterInput`` の構築はこの 1 箇所が行う（§4.4.2）。
    分解そのものは字句層の ``split_input_value`` を使う（`||` の知識を持たない）。
    """

    model_config = ConfigDict(extra="forbid")

    lines: tuple[str, ...] = ()

    _inputs: tuple[TesterInput, ...] = PrivateAttr(default=())

    @property
    def inputs(self) -> tuple[TesterInput, ...]:
        """検証済みの ``TesterInput`` 列。"""
        return self._inputs

    @model_validator(mode="after")
    def _rule_q_inputs(self) -> "_TesterInputsModel":
        """規則 Q: 2 形式・フラグ・名前の一意性・名前長・行数上限。"""
        if len(self.lines) > MAX_INPUT_LINES:
            _raise_rule(
                "ini_format",
                "R8",
                reason=(
                    f"{SECTION_TESTER_INPUTS} の行数が上限 {MAX_INPUT_LINES} を超えています: "
                    f"{len(self.lines)}"
                ),
                section=SECTION_TESTER_INPUTS,
            )
        seen: set[str] = set()
        parsed: list[TesterInput] = []
        for line in self.lines:
            entry = _parse_input_line(line)
            if entry.name in seen:
                _raise_rule(
                    "ini_format",
                    "R8",
                    reason=f"{SECTION_TESTER_INPUTS} の入力名が重複しています: {entry.name}",
                    section=SECTION_TESTER_INPUTS,
                    key=entry.name,
                    line=line,
                )
            seen.add(entry.name)
            parsed.append(entry)
        self._inputs = tuple(parsed)
        return self


def _parse_input_line(line: str) -> TesterInput:
    """`[TesterInputs]` の 1 行を ``TesterInput`` へ分解する（R8・§4.4.2）。"""
    name, separator, value = line.partition("=")
    if not separator:
        _raise_rule(
            "ini_format",
            "R8",
            reason=f"{SECTION_TESTER_INPUTS} の行が '名前=値' 形式ではありません",
            section=SECTION_TESTER_INPUTS,
            line=line,
        )
    if not 1 <= len(name) <= MAX_INPUT_NAME_CHARS:
        _raise_rule(
            "ini_format",
            "R8",
            reason=(
                f"{SECTION_TESTER_INPUTS} の入力名の長さが 1〜{MAX_INPUT_NAME_CHARS} "
                "文字ではありません"
            ),
            section=SECTION_TESTER_INPUTS,
            line=line,
        )
    fields = split_input_value(value)
    form = INPUT_FIELD_COUNTS.get(len(fields))
    if form is None:
        _raise_rule(
            "ini_format",
            "R8",
            reason=(
                f"{SECTION_TESTER_INPUTS} の '{INPUT_FIELD_SEPARATOR}' 分割数が "
                f"{sorted(INPUT_FIELD_COUNTS)} のいずれでもありません: {len(fields)}"
            ),
            section=SECTION_TESTER_INPUTS,
            line=line,
        )
    if form is enums.InputForm.SCALAR:
        return TesterInput(name=name, form=form, current=fields[0], raw=line)
    flag = fields[-1]
    if flag not in INPUT_FLAGS:
        _raise_rule(
            "ini_format",
            "R8",
            reason=(
                f"{SECTION_TESTER_INPUTS} の最適化フラグが {'/'.join(INPUT_FLAGS)} "
                f"ではありません: {flag!r}"
            ),
            section=SECTION_TESTER_INPUTS,
            line=line,
        )
    return TesterInput(
        name=name,
        form=form,
        current=fields[0],
        raw=line,
        start=fields[1],
        step=fields[2],
        stop=fields[3],
        optimize=flag == INPUT_FLAG_TRUE,
    )


# ---------------------------------------------------------------------------
# `.ini` キー → TesterSettings フィールドの読取方向の表
#
# 書出し方向（``TesterSettings`` → `.ini` の値文字列）と標準キー順・許容キー集合は
# 字句層の ``TESTER_KEY_SPECS`` / ``STANDARD_KEY_ORDER`` / ``TESTER_KEYS`` が唯一の
# 宣言であり、本モジュールはそれを import して使う（書き直さない）。
# ---------------------------------------------------------------------------
def _scalar_reader(
    ini_key: str,
    field: str,
    *,
    parse: Callable[[Any], Any] | None = None,
) -> Callable[[Any], dict[str, Any]]:
    """1 対 1 対応キー（複合でないキー）の読取器を作る。"""

    def to_settings(model: Any) -> dict[str, Any]:
        value = getattr(model, ini_key)
        if value is None:
            return {}
        return {field: parse(value) if parse is not None else value}

    return to_settings


def _subject_reader(ini_key: str, kind: enums.SubjectKind) -> Callable[[Any], dict[str, Any]]:
    """`Expert` / `Indicator`（``subject_kind`` と ``subject_path`` の複合キー）。"""

    def to_settings(model: Any) -> dict[str, Any]:
        value = getattr(model, ini_key)
        if value is None:
            return {}
        return {"subject_kind": kind, "subject_path": value}

    return to_settings


def _dates_reader(model: Any) -> dict[str, Any]:
    """`Dates` → ``DateRange(kind=PRESET)``。"""
    if model.Dates is None:
        return {}
    return {"date_range": DateRange(kind=enums.DateRangeKind.PRESET, preset=model.Dates)}


def _from_date_reader(model: Any) -> dict[str, Any]:
    """`FromDate`（＋`ToDate`）→ ``DateRange(kind=CUSTOM)``。"""
    if model.FromDate is None:
        return {}
    return {
        "date_range": DateRange(
            kind=enums.DateRangeKind.CUSTOM, from_date=model.FromDate, to_date=model.ToDate
        )
    }


def _paired_key_reader(_model: Any) -> dict[str, Any]:
    """他のキーの読取器が併せて取るキー（`ToDate` は `FromDate` の読取器が取る）。"""
    return {}


@dataclass(frozen=True)
class TesterKeyRule:
    """`[Tester]` の 1 キーの検証層側の仕様（規則 ID と読取器）。"""

    rule_id: str
    to_settings: Callable[[Any], dict[str, Any]]


#: キー → (規則 ID, 読取器)。キー集合・順序は保持しない（字句層の順序表から導出）。
KEY_RULES: dict[str, TesterKeyRule] = {
    "Expert": TesterKeyRule("N", _subject_reader("Expert", enums.SubjectKind.EXPERT)),
    "Indicator": TesterKeyRule("N", _subject_reader("Indicator", enums.SubjectKind.INDICATOR)),
    "Symbol": TesterKeyRule("M", _scalar_reader("Symbol", "symbol")),
    "Period": TesterKeyRule("O", _scalar_reader("Period", "timeframe")),
    "Optimization": TesterKeyRule("O", _scalar_reader("Optimization", "optimization")),
    "Model": TesterKeyRule("O", _scalar_reader("Model", "tick_model")),
    "Dates": TesterKeyRule("O", _dates_reader),
    "FromDate": TesterKeyRule("R10", _from_date_reader),
    "ToDate": TesterKeyRule("R10", _paired_key_reader),
    "ForwardMode": TesterKeyRule("O", _scalar_reader("ForwardMode", "forward_mode")),
    "ForwardDate": TesterKeyRule("R10", _scalar_reader("ForwardDate", "forward_date")),
    "Deposit": TesterKeyRule("I", _scalar_reader("Deposit", "deposit")),
    "Currency": TesterKeyRule("L", _scalar_reader("Currency", "currency")),
    "ProfitInPips": TesterKeyRule(
        "R11", _scalar_reader("ProfitInPips", "profit_in_pips", parse=bool)
    ),
    "Leverage": TesterKeyRule("J", _scalar_reader("Leverage", "leverage")),
    "ExecutionMode": TesterKeyRule("R7", _scalar_reader("ExecutionMode", "execution_delay")),
    "OptimizationCriterion": TesterKeyRule(
        "O", _scalar_reader("OptimizationCriterion", "optimization_criterion")
    ),
    "Visual": TesterKeyRule("R11", _scalar_reader("Visual", "visual", parse=bool)),
}

# 宣言（pydantic の型）・読取器（KEY_RULES）・字句層の順序表が食い違ったまま動く
# ことを機械的に禁じる（制約は機械的検査で担保する）。
if tuple(_TesterIniModel.model_fields) != STANDARD_KEY_ORDER:
    raise RuntimeError(
        "_TesterIniModel のフィールド順が字句層の STANDARD_KEY_ORDER と一致しません: "
        f"{tuple(_TesterIniModel.model_fields)} != {STANDARD_KEY_ORDER}"
    )
if frozenset(KEY_RULES) != TESTER_KEYS:
    raise RuntimeError(
        "KEY_RULES のキー集合が字句層の TESTER_KEYS と一致しません: "
        f"{sorted(set(KEY_RULES) ^ set(TESTER_KEYS))}"
    )
if not frozenset(EXPERT_ONLY_KEYS) < TESTER_KEYS:
    raise RuntimeError("EXPERT_ONLY_KEYS が字句層の TESTER_KEYS の真部分集合ではありません")


def required_tester_keys() -> "tuple[str, ...]":
    """`[Tester]` の**無条件必須**キーを標準キー順で返す（追加のみ・挙動 0 変化）。

    「無条件」とは、他キーの選択に依らず常に必要という意味である。条件付きの必須
    （規則 D の `Expert` / `Indicator`・規則 E の `Dates` / `FromDate`＋`ToDate`）は
    本関数に含まれない——それらは排他条件を伴い、単独のキー集合では表せない。

    導出元は宣言（`_TesterIniModel` の required フィールド）ただ 1 つである。必須キーの
    表を別に手書きすると、宣言に既定値を足したときに片方だけが腐る。フィールド名が
    `.ini` のキー名と一致することはモジュール読込時に機械検査済み（上の RuntimeError）。

    用途: 設定フォームの schema 供給（Phase 8）が「常に埋めねばならないキー」を
    UI へ配るための単一ソース。検証そのものの経路は本関数を通らない。
    """
    return tuple(
        key for key in STANDARD_KEY_ORDER if _TesterIniModel.model_fields[key].is_required()
    )


def rule_id_for(key: str | None) -> str:
    """`.ini` キーに対応する規則 ID（未知キーは規則 P）。"""
    rule = KEY_RULES.get(key or "")
    return rule.rule_id if rule is not None else "P"


# ---------------------------------------------------------------------------
# 違反レコード → SettingsError の翻訳（決定論・内部設計 §4.3.3）
# ---------------------------------------------------------------------------
#: pydantic のエラー型（自作 ``PydanticCustomError`` のコードを含む）→ 送出する例外。
_ERROR_TYPE_TO_EXCEPTION: dict[str, type[SettingsError]] = {
    "extra_forbidden": UnknownSettingKeyError,          # E-06（規則 P）
    "missing": SettingsKeyMissingError,                 # E-08
    "unknown_setting_value": UnknownSettingValueError,  # E-05（自作・規則 O）
    "enum": UnknownSettingValueError,                   # E-05（規則 O）
    "literal_error": SettingsValueError,                # E-04（R11 の 0/1）
    "settings_key_conflict": SettingsKeyConflictError,  # E-02（自作・規則 D/E/F/G）
    "settings_key_missing": SettingsKeyMissingError,    # E-08（自作・規則 D/E/F/H）
    "settings_activation": SettingsActivationError,     # E-03（自作・規則 B）
    "settings_value": SettingsValueError,               # E-04（自作・書式／規則 K）
    "ini_format": IniFormatError,                       # E-01（自作・規則 Q）
}
#: 型・範囲・書式の残り全部（pydantic 組込の制約違反）。
_DEFAULT_EXCEPTION: type[SettingsError] = SettingsValueError

#: 複数違反が同時に出たときに送出する 1 件を選ぶ順序（§4.3.3）。
#: マッピングの挿入順・フィールド定義順に依存しない。
_PRIORITY: tuple[str, ...] = (
    "extra_forbidden",        # 1. 未知キー（そのファイル自体が対象外の可能性）
    "unknown_setting_value",  # 2. 未知値（MT5 バージョン差の検出シグナル）
    "enum",
    "settings_key_conflict",  # 3. 構造の矛盾
    "settings_activation",
    "ini_format",
    "missing",                # 4. 欠落
    "settings_key_missing",
)                             # 5. 残りは E-04

#: 違反レコードの ``ctx`` からそのまま ``context`` へ渡す語（§4.5.2 の語彙）。
_CONTEXT_PASSTHROUGH: frozenset[str] = frozenset(
    {
        "key",
        "keys",
        "value",
        "expected",
        "allowed",
        "rule_id",
        "reason",
        "field",
        "subject_kind",
        "section",
        "line",
        "tbd",
    }
)


def _select_error(records: Sequence[Mapping[str, Any]]) -> Mapping[str, Any]:
    """優先順位表で 1 件を選ぶ（同型が複数なら登録順・宣言順の先頭）。"""
    for error_type in _PRIORITY:
        for record in records:
            if record["type"] == error_type:
                return record
    return records[0]


def _context_for(
    record: Mapping[str, Any],
    *,
    records: Sequence[Mapping[str, Any]],
    raw: Mapping[str, str],
    path: str | None,
) -> dict[str, Any]:
    """選ばれた 1 件から ``context`` を組む（全件は ``validation_errors`` に残す）。"""
    ctx = dict(record.get("ctx") or {})
    location = record.get("loc") or ()
    error_type = str(record["type"])
    key = ctx.get("key") or (str(location[0]) if location else None)

    context: dict[str, Any] = {"path": path, "validation_errors": list(records)}
    context.update({name: value for name, value in ctx.items() if name in _CONTEXT_PASSTHROUGH})
    context.setdefault("key", key)
    context.setdefault("rule_id", "P" if error_type == "extra_forbidden" else rule_id_for(key))
    context.setdefault("expected", str(record.get("msg", "")))

    if error_type == "missing":
        context.setdefault("keys", [key] if key else [])
    elif error_type == "extra_forbidden":
        context.setdefault("allowed", list(STANDARD_KEY_ORDER))
    if "value" not in context and key is not None and key in raw:
        # 生トークンを載せる（解釈後の値を載せると原典の表記が失われる＝R7）。
        context["value"] = raw[key]
    if "reason" not in context:
        context["reason"] = f"{key}: {record.get('msg', '')}" if key else str(record.get("msg", ""))
    return context


def _translate(
    records: Sequence[Mapping[str, Any]],
    *,
    raw: Mapping[str, str],
    path: str | None,
) -> SettingsError:
    """違反レコード列を ``SettingsError`` 系 1 件へ翻訳する（§4.3.3）。

    全件は ``context["validation_errors"]`` に残す。原 ``ValidationError`` は呼出側が
    ``raise ... from exc`` で保持する（pydantic 型を上位へ漏らさない）。
    """
    selected = _select_error(records)
    exception_type = _ERROR_TYPE_TO_EXCEPTION.get(str(selected["type"]), _DEFAULT_EXCEPTION)
    allowed = exception_type.allowed_context()
    context = {
        name: value
        for name, value in _context_for(selected, records=records, raw=raw, path=path).items()
        if name in allowed and value is not None
    }
    if exception_type.REQUIRED_CONTEXT - set(context):  # pragma: no cover - 退避経路
        return exception_type(f"設定検証に失敗しました: {len(records)} 件のエラー", context=context)
    return exception_type(**context)


# ---------------------------------------------------------------------------
# 入口
# ---------------------------------------------------------------------------
def build_settings(
    tester: Mapping[str, str],
    inputs: Sequence[str] = (),
    *,
    source: IniDocument | None = None,
    header_comment: str | None = None,
    path: str | None = None,
) -> TesterSettings:
    """`[Tester]` / `[TesterInputs]` の生値を検証し ``TesterSettings`` を返す。

    事前条件: ``tester`` のキーは `.ini` と同じ CamelCase、値は生トークン文字列。
        ``inputs`` は `[TesterInputs]` の行原文（``"名前=値"``）。
    事後条件: 規則 B〜Q を適用済みの ``TesterSettings``（frozen）を返す。
    送出例外: ``SettingsError`` 系（E-01 / E-02 / E-03 / E-04 / E-05 / E-06 / E-08）。

    評価順: (1) キー存在規則（D・E・G・H）→ (2) field 制約と書式バリデータ →
    (3) 値依存規則（B・F・K）と規則 Q。(1) は (2) の成否に依存しないため常に評価し、
    全違反を 1 つの優先順位表で選別する。
    """
    raw = dict(tester)
    records: list[dict[str, Any]] = _presence_violations(frozenset(raw))
    try:
        model = _TesterIniModel(**raw)
        inputs_model = _TesterInputsModel(lines=tuple(inputs))
    except ValidationError as exc:
        records.extend(exc.errors(include_url=False))
        raise _translate(records, raw=raw, path=path) from exc
    if records:
        raise _translate(records, raw=raw, path=path)

    values: dict[str, Any] = {}
    for key in STANDARD_KEY_ORDER:
        values.update(KEY_RULES[key].to_settings(model))
    return TesterSettings(
        **values,
        inputs=inputs_model.inputs,
        header_comment=header_comment,
        source=source,
    )
