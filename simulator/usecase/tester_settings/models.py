"""Settings の DTO 群（生表現 `IniDocument` と型付き表現 `TesterSettings`）。

1. 層名/責務:
    usecase 層。`.ini` の**生表現**（行原文を順序どおり保持し往復バイト一致を成立
    させる）と**型付き表現**（列挙・日付へ解釈済み）の二重表現を保持する
    （基本設計 §3.2）。I/O・検証・エンジン投入は行わない。

2. 含む構造:
    IniLineKind / IniLine / IniDocument : 生表現（往復の正典）。
    DateRange / TesterInput            : 入れ子 DTO。
    SettingsPayload                    : 19 フィールドの唯一の宣言。
    TesterSettings                     : 読み込んだ設定そのもの（往復・検査用）。
    EffectiveSettings                  : 実行時に参照してよい派生ビュー（規則 A・C で
                                         inert フィールドを None 化したもの）。

3. 元 MQL 対応:
    `.ini`（MT5 が Settings タブをシリアライズした形式）の 2 セクション
    `[Tester]` / `[TesterInputs]` に対応する。

4. 依存:
    標準: dataclasses / datetime / typing
    外部: なし
    プロジェクト内: simulator.usecase.tester_settings.enums

設計上の要点（内部設計 §4.2.1 の型確定事項）:
    `TesterSettings` と `EffectiveSettings` はフィールド集合が完全に同一である。
    同じ 19 行を 2 度書くと必ず片方が腐るため、宣言は ``SettingsPayload`` の 1 箇所
    だけに置き、両者はその**兄弟**（どちらも他方の派生ではない）として定義する。
    兄弟にする理由は置換可能性（LSP）: `EffectiveSettings` は値が欠けた派生ビューで
    あり、往復（`dump`）の入力としては成立しない。継承にすると `TesterSettings` を
    要求する往復経路へ派生ビューが流れ込めてしまう。
"""
from __future__ import annotations

from dataclasses import dataclass, fields
from datetime import date
from enum import StrEnum

from simulator.usecase.tester_settings.enums import (
    DateRangeKind,
    DatesPreset,
    ForwardMode,
    InputForm,
    OptimizationCriterion,
    OptimizationMode,
    SubjectKind,
    TickModel,
    Timeframe,
)

#: `Math calculations` のとき参照してはならない（inert な）フィールド名。
#: 規則 A の 10 フィールド ＋ 規則 C の `visual`（基本設計 §4.5.5）。
INERT_FIELDS: tuple[str, ...] = (
    "symbol",
    "timeframe",
    "date_range",
    "forward_mode",
    "forward_date",
    "execution_delay",
    "profit_in_pips",
    "deposit",
    "currency",
    "leverage",
    "visual",
)

#: 生表現（往復の正典）。実行時ビュー（`EffectiveSettings`）はこれを持たない。
RAW_REPRESENTATION_FIELDS: tuple[str, ...] = ("source", "header_comment")


class IniLineKind(StrEnum):
    """`.ini` の行種別（R3〜R5）。"""

    COMMENT = "comment"
    SECTION = "section"
    ENTRY = "entry"
    BLANK = "blank"


@dataclass(frozen=True)
class IniLine:
    """`.ini` の 1 行（原文＋解釈の便宜のための派生値）。

    ``text`` は改行文字を含まない**行原文**であり、復元はこれだけを使う
    （``key`` / ``value`` は解釈用の派生値で復元には用いない＝R7）。
    """

    kind: IniLineKind
    text: str
    lineno: int
    section: str | None = None
    key: str | None = None
    value: str | None = None


@dataclass(frozen=True)
class IniDocument:
    """`.ini` の生表現（往復バイト一致の正典・R6/R9）。"""

    lines: tuple[IniLine, ...]
    encoding: str
    newline: str
    has_bom: bool
    trailing_newline: bool

    def key_order(self, section: str) -> tuple[str, ...]:
        """``section`` 内のキーを出現順で返す（R6）。"""
        return tuple(
            line.key
            for line in self.lines
            if line.section == section and line.kind is IniLineKind.ENTRY and line.key is not None
        )

    def entries(self, section: str) -> tuple[tuple[str, str], ...]:
        """``section`` 内の (key, value) を出現順で返す。"""
        return tuple(
            (line.key, line.value)
            for line in self.lines
            if line.section == section
            and line.kind is IniLineKind.ENTRY
            and line.key is not None
            and line.value is not None
        )

    def entry(self, section: str, key: str) -> str | None:
        """``section`` の ``key`` の値を返す（無ければ None）。"""
        for entry_key, value in self.entries(section):
            if entry_key == key:
                return value
        return None

    def header_comment(self) -> str | None:
        """1 行目がコメント行のときその原文を返す（F-18）。"""
        if self.lines and self.lines[0].kind is IniLineKind.COMMENT:
            return self.lines[0].text
        return None


@dataclass(frozen=True)
class DateRange:
    """期間指定（`Dates` か `FromDate`+`ToDate` のいずれか＝F-2）。"""

    kind: DateRangeKind
    preset: DatesPreset | None = None
    from_date: date | None = None
    to_date: date | None = None


@dataclass(frozen=True)
class TesterInput:
    """`[TesterInputs]` の 1 行（型推定を行わず文字列で保持＝基本設計 §4.2.2）。"""

    name: str
    form: InputForm
    current: str
    raw: str
    start: str | None = None
    step: str | None = None
    stop: str | None = None
    optimize: bool | None = None


@dataclass(frozen=True, kw_only=True)
class SettingsPayload:
    """Settings の 19 フィールドの**唯一の宣言**（直接生成しない）。

    `Indicator` テストでは Expert 専用 8 キーが存在しない（F-12）ため、それらは
    ``None`` を取り得る。既定値は「キーが存在しない」ことのみを表し、値を発明しない
    （`deposit` / `currency` / `leverage` / `symbol` 等に推定値を入れない）。
    """

    subject_kind: SubjectKind
    subject_path: str
    tick_model: TickModel
    symbol: str | None = None
    timeframe: Timeframe | None = None
    date_range: DateRange | None = None
    forward_mode: ForwardMode | None = None
    forward_date: date | None = None
    deposit: float | None = None
    currency: str | None = None
    profit_in_pips: bool | None = None
    leverage: int | None = None
    execution_delay: int | None = None
    optimization: OptimizationMode | None = None
    optimization_criterion: OptimizationCriterion | None = None
    visual: bool | None = None
    inputs: tuple[TesterInput, ...] = ()
    header_comment: str | None = None
    source: IniDocument | None = None

    def _field_values(self) -> dict:
        """フィールド名 → 値（浅いコピー。``asdict`` と違い入れ子 DTO を壊さない）。"""
        return {field.name: getattr(self, field.name) for field in fields(self)}


@dataclass(frozen=True, kw_only=True)
class TesterSettings(SettingsPayload):
    """読み込んだ設定そのもの（往復・検査の対象）。"""

    def effective(self) -> "EffectiveSettings":
        """実行時に参照してよい派生ビューを返す（規則 A・C。純関数）。

        ``tick_model == MATH_CALCULATIONS`` のとき inert な 11 フィールドを ``None``
        に置換する。それ以外の型付きフィールドは値をそのまま引き写す。

        生表現（``source`` / ``header_comment``）は**常に落とす**。これを引き写すと
        ``eff.source.entry("Tester", "Deposit")`` のように inert のはずの値へ到達でき、
        規則 A の遮断が型の外側で破れるため（実行時ビューは生表現を持たない）。
        往復（`dump`）は `TesterSettings` 側の責務であり、生表現を必要としない。
        """
        values = self._field_values()
        if self.tick_model is TickModel.MATH_CALCULATIONS:
            values.update({name: None for name in INERT_FIELDS})
        values.update({name: None for name in RAW_REPRESENTATION_FIELDS})
        return EffectiveSettings(**values)


@dataclass(frozen=True, kw_only=True)
class EffectiveSettings(SettingsPayload):
    """実行時ビュー（inert フィールドが ``None`` 化された派生 DTO）。

    変換層（`build_interactor` への写像）は本型のみを参照する。これにより
    「inert な値を口座計算に用いる」経路が型で塞がれる（基本設計 §4.5.5）。
    """

    @property
    def is_math_calculations(self) -> bool:
        return self.tick_model is TickModel.MATH_CALCULATIONS

    @property
    def inert_fields(self) -> tuple[str, ...]:
        """本ビューで inert 化されたフィールド名（非該当時は空）。"""
        return INERT_FIELDS if self.is_math_calculations else ()
