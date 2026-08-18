"""`.ini` の字句層（バイト ⇄ 文字列 ⇄ 行 ⇄ 文書 ⇄ ファイル）。

1. 層名/責務:
    adapter 層。MT5 ストラテジーテスターの `.ini` を**書式としてのみ**扱う。
    規則 R1（エンコーディング）・R2（改行）・R3〜R5（行種別）・R6（キー順）・
    R8（`[TesterInputs]` の分解）・R9（往復バイト一致）を担う。
    値の**意味**（列挙への写像・範囲・活性依存）は解釈しない（検証層の責務）。
    責務は 1 段 1 関数に割り、境界を跨がない:
        バイト ⇄ 文字列 : ``read_bytes`` / ``decode``
        文字列 ⇄ 行     : ``split_lines``
        行     ⇄ 文書   : ``parse`` / ``serialize``
        文書   ⇄ ファイル: ``read_document`` / ``write_document``
        設定   → 文書   : ``build_document``

2. 含む構造:
    MAX_FILE_BYTES / MAX_LINE_CHARS / MAX_INPUT_LINES : 入力上限（内部設計 §7.3）。
    SECTION_TESTER / SECTION_TESTER_INPUTS / SECTION_ORDER : セクション名の単一ソース。
    INPUT_FIELD_SEPARATOR / INPUT_FLAG_TRUE / INPUT_FLAG_FALSE : `||` 分解の単一ソース。
    TESTER_KEY_SPECS / STANDARD_KEY_ORDER / TESTER_KEYS :
        `[Tester]` の標準キー順（基本設計 §4.4）の**唯一の宣言**。許容キー集合は
        この順序表から導出する（順序表と集合を二重に書かない）。
    read_bytes / decode / split_lines / parse / serialize /
    read_document / write_document / build_document : 8 関数。

3. 元 MQL 対応:
    MT5 が Settings タブをシリアライズした `MQL5/Profiles/Tester/*.ini`
    （UTF-16LE + BOM・CRLF・`[Tester]` → `[TesterInputs]` の 2 セクション）。
    corpus 44 件の実測に基づく（BOM 44/44 が ``FF FE``、CRLF 44/44、
    最終行 CRLF 終端 44/44、空行 0、`[Tester]` 重複キー 0）。

4. 依存:
    標準: dataclasses / hashlib / logging / pathlib / typing
    外部: なし（**pydantic を import しない**＝内部設計 §3.3 I-2）
    プロジェクト内: simulator.domain.tester_settings_exceptions（例外）／
                    simulator.usecase.tester_settings（DTO・列挙）
                    ※ simulator.main は import しない（I-4）

セキュリティ（内部設計 §7.2）:
    本モジュールは `Expert` / `Indicator` の値をファイルシステムアクセスに用いない。
    ファイルを開くのは呼出側が渡した ``path`` だけである。
"""
from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from collections.abc import Iterable, Mapping
from typing import Callable

from simulator.domain.tester_settings_exceptions import (
    IniFormatError,
    SettingsValueError,
)
from simulator.usecase.tester_settings import (
    TIMEFRAME_INI_LABELS,
    IniDocument,
    IniLine,
    IniLineKind,
    InputForm,
    SettingsPayload,
    SubjectKind,
    TesterSettings,
    Timeframe,
)

logger = logging.getLogger("simulator.tester_settings")

# ---------------------------------------------------------------------------
# 上限（内部設計 §7.3）
# ---------------------------------------------------------------------------

#: 入力ファイルサイズの上限（1 MiB）。corpus 実測の最大は 1,118 バイト。
MAX_FILE_BYTES: int = 1 << 20
#: 1 行の文字数上限。corpus 実測の最大は 116 文字。
MAX_LINE_CHARS: int = 4096
#: `[TesterInputs]` の行数上限（基本設計 §4.2 #17）。corpus 実測の最大は 8 行。
MAX_INPUT_LINES: int = 256

# ---------------------------------------------------------------------------
# 書式定数（単一ソース）
# ---------------------------------------------------------------------------

#: 読込で受容する BOM → エンコーディング名（R1）。BOM 不在は E-01。
_BOM_ENCODINGS: tuple[tuple[bytes, str], ...] = (
    (b"\xff\xfe", "utf-16-le"),
    (b"\xfe\xff", "utf-16-be"),
)
#: 書出しで受容する符号化（R1）。**``_BOM_ENCODINGS`` から導出**する（是正 D）。
#: 読込側（``decode``）の像と書出し側（``serialize``）の定義域を同一の宣言から作り、
#: 「読めるが書けない / 書けるが読み戻せない」符号化が生じないようにする。
#: 手書きの第 2 の集合を作らない（BOM 表が唯一の宣言）。
WRITE_ENCODINGS: frozenset[str] = frozenset(encoding for _, encoding in _BOM_ENCODINGS)
#: **新規生成**する文書の符号化（R1 で LE 固定）。読込んだ文書の書出しは
#: ``IniDocument.encoding`` に従う（是正 2。R9 を BE 入力でも成立させる）。
ENCODING_WRITE: str = "utf-16-le"
#: 書出し改行（R2 で CRLF 固定）。読込元が無いときの既定値でもある。
NEWLINE_WRITE: str = "\r\n"

#: セクション名は**角括弧を含む行原文**とする（基本設計 §4.4 R4 の文言
#: 「許容するセクション名は `[Tester]` と `[TesterInputs]`」に一致させる）。
#: ``IniLine.section`` / ``IniDocument.key_order`` / ``entries`` / ``entry`` の引数も同じ表記。
SECTION_TESTER: str = "[Tester]"
SECTION_TESTER_INPUTS: str = "[TesterInputs]"
#: 出現順（R4 で固定。各 1 回）。許容セクション名の集合はここから導出する。
SECTION_ORDER: tuple[str, ...] = (SECTION_TESTER, SECTION_TESTER_INPUTS)
ALLOWED_SECTIONS: frozenset[str] = frozenset(SECTION_ORDER)

COMMENT_PREFIX: str = ";"
SECTION_OPEN: str = "["
SECTION_CLOSE: str = "]"
KEY_VALUE_SEPARATOR: str = "="

#: `[TesterInputs]` の値の区切り（R8・F-13）。`||` 分解はこの定数だけを使う。
INPUT_FIELD_SEPARATOR: str = "||"
#: 最適化フラグ（R8 の 5 件目）。
INPUT_FLAG_TRUE: str = "Y"
INPUT_FLAG_FALSE: str = "N"
INPUT_FLAGS: tuple[str, ...] = (INPUT_FLAG_TRUE, INPUT_FLAG_FALSE)
#: 許容するフィールド数 → 行の形（R8）。2・3・4・6 以上は不正。
INPUT_FIELD_COUNTS: dict[int, InputForm] = {1: InputForm.SCALAR, 5: InputForm.RANGE_5}


# ---------------------------------------------------------------------------
# `[Tester]` の標準キー順（唯一の宣言）
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class TesterKeySpec:
    """`[Tester]` の 1 キーの書式仕様（順序・値の取り出し方）。

    ``extract`` は設定 DTO から**書出し用の文字列**を返す純関数。``None`` を返した
    場合そのキーは出力しない（キーを発明しない＝基本設計 §4.2 の既定値方針）。
    """

    key: str
    extract: Callable[[SettingsPayload], str | None]


def _subject_extractor(kind: SubjectKind) -> Callable[[SettingsPayload], str | None]:
    """`Expert` / `Indicator` は排他（F-1）。対象種別が一致するときだけ出力する。"""

    def extract(settings: SettingsPayload) -> str | None:
        return settings.subject_path if settings.subject_kind is kind else None

    return extract


def format_date_token(value: date) -> str:
    """R10: `YYYY.MM.DD`（ゼロ埋め 2 桁）。**日付表記規則の唯一の宣言**。

    公開名である理由（是正 B）: 検証層の診断メッセージ（規則 K の `expected`）も
    同じ表記を要する。表記を各層で書き直すと同じ規則が複数箇所に生じ、片方だけが
    腐る（実際に検証層へ完全一致の複製が生じていた）。表記を要する側は本関数を
    import して使い、字形を書き直さない。
    """
    return f"{value.year:04d}.{value.month:02d}.{value.day:02d}"


def _format_date(value: date | None) -> str | None:
    """書出し用ラッパ（``None`` は「キーを出力しない」を表す）。

    表記そのものは持たない（``format_date_token`` が唯一の宣言）。
    """
    if value is None:
        return None
    return format_date_token(value)


def _format_bool(value: bool | None) -> str | None:
    """R11: `1` / `0`。"""
    if value is None:
        return None
    return "1" if value else "0"


def _format_int(value: int | None) -> str | None:
    """`IntEnum` を含む整数の生値表記。"""
    if value is None:
        return None
    return str(int(value))


def _format_timeframe(value: Timeframe | None) -> str | None:
    """`Period` は数値ではなく `.ini` ラベル（D-03。写像は enums の単一ソース）。"""
    if value is None:
        return None
    return TIMEFRAME_INI_LABELS[value]


def _format_deposit(value: float | None) -> str | None:
    """`Deposit` の新規生成時表記。

    corpus 実測（31 件・2 種類 `10000` / `139500`）はすべて整数表記であり、
    非整数値の表記は**実測が存在しない**。推測で `139500.5` 等の表記を発明せず
    Fail-Stop する（`.claude/CLAUDE.md`「実証的証拠のない仮定で実装しない」）。
    読込元がある往復経路では本関数を通らない（行原文をそのまま復元する＝R7）。
    """
    if value is None:
        return None
    if float(value).is_integer():
        return str(int(value))
    raise SettingsValueError(
        key="Deposit",
        value=float(value),
        expected="整数値（非整数の .ini 表記は corpus 44 件に実測がない）",
        rule_id="R7",
    )


def _format_str(value: str | None) -> str | None:
    return value


#: `[Tester]` のキーを**標準キー順**（基本設計 §4.4・corpus 実測順）で並べた唯一の表。
#: Indicator テストの並び（`Indicator` / `Symbol` / `Period` / `Model` / 期間 / `Visual`）は
#: Expert 専用 8 キーの値が ``None`` になる（F-12）ことで自動的に得られる。別表を持たない。
TESTER_KEY_SPECS: tuple[TesterKeySpec, ...] = (
    TesterKeySpec("Expert", _subject_extractor(SubjectKind.EXPERT)),
    TesterKeySpec("Indicator", _subject_extractor(SubjectKind.INDICATOR)),
    TesterKeySpec("Symbol", lambda s: _format_str(s.symbol)),
    TesterKeySpec("Period", lambda s: _format_timeframe(s.timeframe)),
    TesterKeySpec("Optimization", lambda s: _format_int(s.optimization)),
    TesterKeySpec("Model", lambda s: _format_int(s.tick_model)),
    TesterKeySpec("Dates", lambda s: _format_int(_preset_of(s))),
    TesterKeySpec("FromDate", lambda s: _format_date(_from_date_of(s))),
    TesterKeySpec("ToDate", lambda s: _format_date(_to_date_of(s))),
    TesterKeySpec("ForwardMode", lambda s: _format_int(s.forward_mode)),
    TesterKeySpec("ForwardDate", lambda s: _format_date(s.forward_date)),
    TesterKeySpec("Deposit", lambda s: _format_deposit(s.deposit)),
    TesterKeySpec("Currency", lambda s: _format_str(s.currency)),
    TesterKeySpec("ProfitInPips", lambda s: _format_bool(s.profit_in_pips)),
    TesterKeySpec("Leverage", lambda s: _format_int(s.leverage)),
    TesterKeySpec("ExecutionMode", lambda s: _format_int(s.execution_delay)),
    TesterKeySpec("OptimizationCriterion", lambda s: _format_int(s.optimization_criterion)),
    TesterKeySpec("Visual", lambda s: _format_bool(s.visual)),
)

#: 標準キー順（R6。新規生成時の並び）。
STANDARD_KEY_ORDER: tuple[str, ...] = tuple(spec.key for spec in TESTER_KEY_SPECS)
#: `[Tester]` の許容キー集合（R12 の判定に使う）。**順序表から導出**する。
TESTER_KEYS: frozenset[str] = frozenset(STANDARD_KEY_ORDER)


def _preset_of(settings: SettingsPayload):
    """`Dates`（`DateRange.kind == PRESET` のときのみ）。"""
    date_range = settings.date_range
    if date_range is None:
        return None
    return date_range.preset


def _from_date_of(settings: SettingsPayload) -> date | None:
    date_range = settings.date_range
    return None if date_range is None else date_range.from_date


def _to_date_of(settings: SettingsPayload) -> date | None:
    date_range = settings.date_range
    return None if date_range is None else date_range.to_date


# ---------------------------------------------------------------------------
# 1. バイト ⇄ 文字列
# ---------------------------------------------------------------------------


def read_bytes(path: str | Path) -> bytes:
    """`.ini` をバイト列で読む（サイズ上限検査つき）。

    事前条件: ``path`` が存在すること。
    事後条件: 返り値の長さは ``MAX_FILE_BYTES`` 以下。

    ``FileNotFoundError`` は**翻訳せずそのまま伝播**する（呼出側の引数誤りであり
    `.ini` の書式問題ではない。内部設計 §4.5.3）。
    """
    target = Path(path)
    size = target.stat().st_size  # 不在時は FileNotFoundError がそのまま伝播する
    if size > MAX_FILE_BYTES:
        raise IniFormatError(
            reason=f"ファイルサイズが上限 {MAX_FILE_BYTES} バイトを超えています（{size} バイト）",
            rule_id="R1",
            path=str(target),
        )
    return target.read_bytes()


def decode(data: bytes, *, path: str | None = None) -> tuple[str, str]:
    """R1: BOM で UTF-16LE / BE を判定して復号する。

    事前条件: なし。
    事後条件: ``(BOM を除いた本文, エンコーディング名)`` を返す。
    BOM 不在・復号不能はいずれも `.ini` の書式不正（E-01）とする。
    """
    for bom, encoding in _BOM_ENCODINGS:
        if data.startswith(bom):
            try:
                text = data[len(bom) :].decode(encoding)
            except UnicodeDecodeError as exc:
                raise IniFormatError(
                    reason=f"UTF-16 として復号できません: {exc.reason}",
                    rule_id="R1",
                    path=path,
                ) from exc
            return text, encoding
    raise IniFormatError(
        reason="先頭に UTF-16 の BOM がありません",
        rule_id="R1",
        path=path,
    )


# ---------------------------------------------------------------------------
# 2. 文字列 ⇄ 行
# ---------------------------------------------------------------------------


def split_lines(text: str) -> tuple[tuple[str, ...], str, bool]:
    """R2: 行へ分割する。

    事前条件: ``text`` は BOM を含まない本文。
    事後条件: ``(行内容列, 検出した newline, trailing_newline)``。行内容は改行文字を
    含まない。CRLF / LF の双方を受容し、改行で終端された行の末尾 `CR` を除去する。

    改行が CRLF と LF で混在する入力は E-01 とする。理由: 本関数は文書全体で 1 つの
    newline しか返さないため、混在を受容すると ``serialize`` の往復がバイト一致し
    なくなる（R9 が沈黙して壊れる）。corpus 44 件はすべて CRLF 単一である。
    """
    if text == "":
        return (), NEWLINE_WRITE, False

    trailing_newline = text.endswith("\n")
    parts = text.split("\n")
    if trailing_newline:
        parts = parts[:-1]  # 末尾の空要素（最終改行の後ろ）を落とす

    # 改行で終端された行だけが CR の有無を語れる（最終行が未終端なら判定に使わない）。
    terminated = parts if trailing_newline else parts[:-1]
    crlf_count = sum(1 for part in terminated if part.endswith("\r"))
    if terminated and crlf_count not in (0, len(terminated)):
        raise IniFormatError(
            reason=(
                f"改行が CRLF と LF で混在しています（CRLF {crlf_count} 行 / 全 {len(terminated)} 行）"
            ),
            rule_id="R2",
        )
    newline = NEWLINE_WRITE if terminated and crlf_count == len(terminated) else "\n"
    if not terminated:
        newline = NEWLINE_WRITE  # 1 行のみ・未終端。書出し既定（CRLF）に倒す

    stripped = list(parts)
    for index in range(len(terminated)):
        if stripped[index].endswith("\r"):
            stripped[index] = stripped[index][:-1]
    return tuple(stripped), newline, trailing_newline


# ---------------------------------------------------------------------------
# 3. 行 ⇄ 文書
# ---------------------------------------------------------------------------


def _classify(raw: str) -> IniLineKind:
    """R3〜R5 の行種別判定（**判定はこの 1 関数だけが持つ**）。"""
    if raw.strip() == "":
        return IniLineKind.BLANK
    if raw.startswith(COMMENT_PREFIX):
        return IniLineKind.COMMENT
    if raw.startswith(SECTION_OPEN):
        return IniLineKind.SECTION
    return IniLineKind.ENTRY


def split_input_value(value: str) -> tuple[str, ...]:
    """R8: `[TesterInputs]` の値を `||` で分解する（分解はこの 1 関数だけが持つ）。"""
    return tuple(value.split(INPUT_FIELD_SEPARATOR))


#: 生トークンに含められない文字（1 トークン = 1 行の対応を壊す）。R2 の改行判定と
#: 同じ文字集合だが、こちらは**供給されたトークン**に課す事前条件である。
LINE_BREAK_CHARS: tuple[str, ...] = ("\r", "\n")


def _has_line_break(token: str) -> bool:
    return any(char in token for char in LINE_BREAK_CHARS)


def _require_single_line_tokens(
    pairs: tuple[tuple[str, str], ...],
    input_lines: tuple[str, ...],
) -> None:
    """供給された生トークンが 1 行であることを課す（R5・是正 C）。

    事前条件: なし。
    事後条件: 返れば、どのトークンも改行を含まない（＝組み立てた本文の行数が供給数と
    一致する）。違反は E-01（`rule_id="R5"`）で、**呼出側が供給した対そのもの**を
    ``key`` / ``value``（`[TesterInputs]` は ``line``）に載せて指す。

    この検査を ``parse`` より前に置く理由（レビュー指摘 是正 C）: 呼出側は対と行原文
    だけを供給し「行番号」も「改行」も供給しない。改行を含むトークンをそのまま本文へ
    連結すると、
        1. 診断が行数に基づく R2（CRLF/LF 混在）になり、実際の誤り（どのキーの値に
           改行があるか）を指さない。
        2. 供給側の改行が CRLF のときは**例外すら出ず**、1 行が 2 行へ黙って分割される
           （実測: ``("a=1\\r\\nb=2",)`` が 2 エントリになる）。沈黙で表現が変わる。
    行数に基づく R2 診断は ``split_lines`` を通るファイル読込経路の専用診断に留める。
    """
    for key, value in pairs:
        for token, role in ((key, "キー"), (value, "値")):
            if _has_line_break(token):
                raise IniFormatError(
                    reason=f"{SECTION_TESTER} の{role}に改行を含められません: {token!r}",
                    rule_id="R5",
                    key=key,
                    value=value,
                    line=f"{key}{KEY_VALUE_SEPARATOR}{value}",
                )
    for line in input_lines:
        if _has_line_break(line):
            raise IniFormatError(
                reason=f"{SECTION_TESTER_INPUTS} の行原文に改行を含められません: {line!r}",
                rule_id="R5",
                line=line,
            )


def _fail(
    reason: str,
    *,
    rule_id: str,
    path: str | None,
    lineno: int,
    line: str,
    key: str | None = None,
):
    """E-01 を組み立てる（診断値の載せ方を 1 箇所に集約する）。"""
    context: dict[str, object] = {
        "reason": reason,
        "rule_id": rule_id,
        "path": path,
        "lineno": lineno,
        "line": line,
    }
    if key is not None:
        context["key"] = key
    return IniFormatError(**context)


def parse(text: str, *, encoding: str, path: str | None = None) -> IniDocument:
    """R2〜R5・R8 の構文検証を行い ``IniDocument`` を組み立てる。

    事前条件: ``text`` は BOM を含まない本文。``encoding`` は ``decode`` の戻り値。
    事後条件: 行原文が順序どおり保持され、``serialize`` で往復できる（R9）。

    **意味解釈は行わない**: 列挙への写像・値域・活性依存・未知キー（R12）・未知値
    （R13）は検証層の責務であり、本関数は判定しない。本関数が拒むのは書式のみ:
    行長上限 / セクション名・出現順・重複（R4）/ エントリ書式（R5）/
    `[Tester]` の重複キー（内部設計 §4.1.2 補足規則 2）/ `||` の分割数とフラグ（R8）/
    `[TesterInputs]` の行数上限。

    ``has_bom`` は常に ``True`` になる。``decode`` が BOM 不在を E-01 で拒むため
    本関数に BOM 無しの本文は到達せず、``build_document`` の生成物も R1 により
    BOM 付きで書き出されるためである。
    """
    raw_lines, newline, trailing_newline = split_lines(text)

    lines: list[IniLine] = []
    current_section: str | None = None
    section_sequence: list[str] = []
    tester_keys_seen: set[str] = set()
    input_line_count = 0

    for lineno, raw in enumerate(raw_lines, start=1):
        if len(raw) > MAX_LINE_CHARS:
            raise _fail(
                f"行が上限 {MAX_LINE_CHARS} 文字を超えています（{len(raw)} 文字）",
                rule_id="R2",
                path=path,
                lineno=lineno,
                line=raw,
            )
        kind = _classify(raw)

        if kind is IniLineKind.SECTION:
            if not raw.endswith(SECTION_CLOSE):
                raise _fail(
                    "セクション行が ']' で終わっていません",
                    rule_id="R4",
                    path=path,
                    lineno=lineno,
                    line=raw,
                )
            name = raw
            if name not in ALLOWED_SECTIONS:
                raise _fail(
                    f"許容しないセクション名です: {name}（許容: {', '.join(SECTION_ORDER)}）",
                    rule_id="R4",
                    path=path,
                    lineno=lineno,
                    line=raw,
                )
            if name in section_sequence:
                raise _fail(
                    f"セクションが重複しています: {name}",
                    rule_id="R4",
                    path=path,
                    lineno=lineno,
                    line=raw,
                )
            section_sequence.append(name)
            current_section = name
            lines.append(IniLine(kind=kind, text=raw, lineno=lineno, section=name))
            continue

        if kind is IniLineKind.ENTRY:
            if current_section is None:
                raise _fail(
                    "セクションの外にエントリ行があります",
                    rule_id="R4",
                    path=path,
                    lineno=lineno,
                    line=raw,
                )
            if KEY_VALUE_SEPARATOR not in raw:
                raise _fail(
                    "'=' を含まない行です",
                    rule_id="R5",
                    path=path,
                    lineno=lineno,
                    line=raw,
                )
            key, value = raw.split(KEY_VALUE_SEPARATOR, 1)
            if key != key.strip() or key == "":
                raise _fail(
                    f"キー名が空か前後に空白があります: {key!r}",
                    rule_id="R5",
                    path=path,
                    lineno=lineno,
                    line=raw,
                )
            if current_section == SECTION_TESTER:
                if key in tester_keys_seen:
                    raise _fail(
                        f"{SECTION_TESTER} のキーが重複しています: {key}",
                        rule_id="R5",
                        path=path,
                        lineno=lineno,
                        line=raw,
                        key=key,
                    )
                tester_keys_seen.add(key)
            else:
                input_line_count += 1
                if input_line_count > MAX_INPUT_LINES:
                    raise _fail(
                        f"{SECTION_TESTER_INPUTS} の行数が上限 {MAX_INPUT_LINES} を超えています",
                        rule_id="R8",
                        path=path,
                        lineno=lineno,
                        line=raw,
                    )
                fields = split_input_value(value)
                if len(fields) not in INPUT_FIELD_COUNTS:
                    raise _fail(
                        (
                            f"'{INPUT_FIELD_SEPARATOR}' の分割数が "
                            f"{sorted(INPUT_FIELD_COUNTS)} のいずれでもありません: {len(fields)}"
                        ),
                        rule_id="R8",
                        path=path,
                        lineno=lineno,
                        line=raw,
                    )
                if INPUT_FIELD_COUNTS[len(fields)] is InputForm.RANGE_5 and fields[4] not in INPUT_FLAGS:
                    raise _fail(
                        f"最適化フラグが {'/'.join(INPUT_FLAGS)} ではありません: {fields[4]!r}",
                        rule_id="R8",
                        path=path,
                        lineno=lineno,
                        line=raw,
                    )
            lines.append(
                IniLine(
                    kind=kind,
                    text=raw,
                    lineno=lineno,
                    section=current_section,
                    key=key,
                    value=value,
                )
            )
            continue

        # COMMENT / BLANK は原文と位置だけを保持する（R3）。
        lines.append(IniLine(kind=kind, text=raw, lineno=lineno, section=current_section))

    if tuple(section_sequence) != SECTION_ORDER:
        raise IniFormatError(
            reason=(
                f"セクション構成が {' → '.join(SECTION_ORDER)} ではありません: "
                f"{' → '.join(section_sequence) if section_sequence else '（なし）'}"
            ),
            rule_id="R4",
            path=path,
        )

    return IniDocument(
        lines=tuple(lines),
        encoding=encoding,
        newline=newline,
        has_bom=True,
        trailing_newline=trailing_newline,
    )


def serialize(doc: IniDocument) -> bytes:
    """R1・R2・R9: BOM ＋ 行原文 ＋ newline を ``doc.encoding`` でバイト列にする。

    事前条件: なし。
    事後条件: ``read_document`` で読んだ文書は元ファイルとバイト列一致で復元される
    （UTF-16LE / UTF-16BE のいずれでも）。

    復元に使うのは ``IniLine.text``（行原文）だけであり、``key`` / ``value`` は
    使わない（R7。数値・日付の再整形を構造的に不可能にする）。

    符号化を ``doc.encoding`` に従わせる理由（是正 2・レビュー指摘 🟡-2）:
    ``decode`` は BOM で LE / BE を判定して BE を正規受理する（R1 の読込側）。書出しを
    LE 固定にすると、BE 入力で R9（読込元があるならバイト一致）が例外も警告もなく破れ
    る。読める入力クラスを狭める（BE を拒否する）のではなく、沈黙で表現を変える経路を
    消す。R1 の「書出しは LE 固定」は**新規生成**に対して働く（``document_from_entries``
    が作る文書の ``encoding`` が ``ENCODING_WRITE`` であることで成立する）。

    ``doc.encoding`` を検査する理由（是正 D・レビュー指摘）: ``decode`` の像は BOM 由来
    の 2 値だけだが、``IniDocument.encoding`` は任意の ``str`` を取り得る（``replace``
    で差し替えられる）。無検査だと `utf-8` は「書けるが読み戻せない」バイト列を沈黙で
    生み（🟡-2 と同型）、`cp932` / `bogus-codec` は ``UnicodeEncodeError`` /
    ``LookupError`` を ``SettingsError`` 体系の外へ漏らす。許容集合は読込側と同一の
    宣言（``_BOM_ENCODINGS``）から導出しているため、受理集合と出力集合は定義上一致する。
    """
    if doc.encoding not in WRITE_ENCODINGS:
        raise IniFormatError(
            reason=(
                f"書出しできない符号化です: {doc.encoding!r}"
                f"（許容: {', '.join(sorted(WRITE_ENCODINGS))}）"
            ),
            rule_id="R1",
            value=doc.encoding,
            allowed=sorted(WRITE_ENCODINGS),
        )
    body = "".join(line.text + doc.newline for line in doc.lines)
    if body and not doc.trailing_newline:
        body = body[: -len(doc.newline)]
    prefix = "\ufeff" if doc.has_bom else ""
    return (prefix + body).encode(doc.encoding)


# ---------------------------------------------------------------------------
# 4. 文書 ⇄ ファイル
# ---------------------------------------------------------------------------


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def read_document(path: str | Path) -> IniDocument:
    """`.ini` を読んで ``IniDocument`` にする（``read_bytes`` → ``decode`` → ``parse``）。

    事前条件: ``path`` が存在し ``MAX_FILE_BYTES`` 以下。
    事後条件: ``serialize`` で元バイト列に戻せる（R9）。

    監査ログ（内部設計 §7.3）: `path` / `bytes` / `sha256` / `key_count` を INFO で
    記録する。バイト数と SHA-256 を知るのは本関数だけであり、上位 facade で再取得
    すると同じ知識が 2 箇所になるため、記録位置をここに一本化する。ERROR は境界
    API の責務であり本モジュールでは記録しない（多重出力の禁止）。
    """
    target = Path(path)
    data = read_bytes(target)
    text, encoding = decode(data, path=str(target))
    doc = parse(text, encoding=encoding, path=str(target))
    logger.info(
        ".ini を読み込みました: %s",
        str(target),
        extra={
            "path": str(target),
            "bytes": len(data),
            "sha256": _sha256(data),
            "key_count": len(doc.key_order(SECTION_TESTER)),
        },
    )
    return doc


def write_document(doc: IniDocument, path: str | Path) -> None:
    """``IniDocument`` を `.ini` として書き出す（既存ファイルは上書きしない）。

    事前条件: ``path`` が未存在であること。
    事後条件: ``serialize(doc)`` のバイト列がそのまま書かれる。符号化は ``doc.encoding``
    （読込元がある文書はその符号化・新規生成は UTF-16LE）、BOM・改行は文書の保持値である
    （是正 2。旧記述の「UTF-16LE 固定」は BE 入力で R9 を破るため改めた）。
    既存時は ``FileExistsError``（K-15。上書きは破壊的変更として行わない）。
    """
    target = Path(path)
    data = serialize(doc)
    with open(target, "xb") as handle:  # 既存時 FileExistsError（K-15）
        handle.write(data)
    logger.info(
        ".ini を書き出しました: %s",
        str(target),
        extra={"path": str(target), "bytes": len(data), "sha256": _sha256(data)},
    )


# ---------------------------------------------------------------------------
# 5. 設定 → 文書（新規生成）
# ---------------------------------------------------------------------------


def document_from_entries(
    tester_entries: Mapping[str, str] | Iterable[tuple[str, str]],
    input_lines: Iterable[str] = (),
) -> IniDocument:
    """`[Tester]` の**生トークン対**と `[TesterInputs]` の行原文から文書を組み立てる。

    事前条件: ``tester_entries`` の値は `.ini` に書ける生トークン（既に整形済みの
    文字列）であること。``input_lines`` は ``"名前=値"`` 形式の行原文であること。
    いずれのトークンも**改行を含まない**こと（違反は E-01 / `rule_id="R5"` として
    当該キー・値を指して拒否する＝``_require_single_line_tokens``）。
    事後条件: `[Tester]` のキーは**与えられた順**に並び、値は 1 文字も書き換えずに
    出力される。文書の符号化は新規生成の既定（UTF-16LE + BOM・CRLF）である。

    本関数を字句層の最下位に置く理由（是正 1・レビュー指摘 🟡-1）:
    型付き値からの整形（``build_document``）だけを入口にすると、検証層が受理できる
    表記のうち整形器が出力できないものが「読めるが写像できない値」として残る
    （`Deposit=139500.50` が実例）。生トークンを捨てずに文書化する経路を設けることで
    受理集合と出力集合が一致し、API-03 の像（``source`` を持つ設定）に対して API-04 は
    例外を送出しなくなる。``source`` を持たない直接構築物は射程外であり、そこでは
    ``build_document`` の Fail-Stop（非整数 `Deposit` は E-04・`rule_id="R7"`）が残る。

    組み立てた行は ``parse`` に通す。行種別付与とセクション・`||` の構文検査を読込
    経路と同一の実装で行い、生成物にも同じ不変条件を課すためである。
    """
    pairs = tuple(tester_entries.items() if isinstance(tester_entries, Mapping) else tester_entries)
    raw_input_lines = tuple(input_lines)
    _require_single_line_tokens(pairs, raw_input_lines)

    texts: list[str] = [SECTION_TESTER]
    texts.extend(f"{key}{KEY_VALUE_SEPARATOR}{value}" for key, value in pairs)
    texts.append(SECTION_TESTER_INPUTS)
    texts.extend(raw_input_lines)

    text = "".join(line + NEWLINE_WRITE for line in texts)
    return parse(text, encoding=ENCODING_WRITE)


def build_document(settings: TesterSettings) -> IniDocument:
    """読込元を持たない設定から `.ini` 文書を新規生成する（R6 の標準キー順）。

    事前条件: ``settings`` が検証層を通過していること（本関数は意味を検証しない）。
    事後条件: `[Tester]` のキーは ``STANDARD_KEY_ORDER`` の順に並び、値が ``None``
    のキーは出力されない（キーを発明しない）。`[TesterInputs]` は空でも出力する（R4）。

    本関数の責務は「型付き値 → 生トークン整形」だけであり、行・セクションの組み立ては
    ``document_from_entries`` へ委譲する（整形規則も文書組立も 1 箇所に置く）。
    ``_format_deposit`` の Fail-Stop はこの新規生成経路にのみ効く。

    コメント行は生成しない（R3・内部設計 §4.1.2 補足規則 3）。``header_comment`` を
    持つ設定でも出力しない（MT5 生成情報を偽造しないため）。読込元がある往復経路は
    本関数を通らず ``settings.source`` の行列をそのまま用いる。

    `[TesterInputs]` の各行は ``TesterInput.raw``（行原文）をそのまま用いる。
    フィールドから行を組み立て直すと `||` 連結の知識が本モジュールと検証層の 2 箇所
    に生じ、書式の再整形リスク（R7）も生むため。
    """
    entries: list[tuple[str, str]] = []
    for spec in TESTER_KEY_SPECS:
        value = spec.extract(settings)
        if value is None:
            continue
        entries.append((spec.key, value))
    return document_from_entries(entries, tuple(item.raw for item in settings.inputs))
