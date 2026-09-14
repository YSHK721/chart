"""backtest adapter 層 tester_settings パッケージ（`.ini` 字句層）。

1. 層名/責務:
    adapter 層。MT5 ストラテジーテスターの `.ini` を**書式としてのみ**読み書きする
    技術ドライバ。値の意味（列挙・値域・活性依存）は解釈しない（検証層の責務）。

2. 含む構造:
    ini_codec      : バイト ⇄ 文字列 ⇄ 行 ⇄ 文書 ⇄ ファイルの 8 関数と上限定数、
                     `[Tester]` の標準キー順（唯一の宣言）。
    header_comment : 1 行目コメントの読取専用解析（API-08）。

3. 元 MQL 対応:
    `MQL5/Profiles/Tester/*.ini`（UTF-16LE + BOM・CRLF・2 セクション）。

4. 依存:
    標準: なし（再エクスポートのみ）
    外部: なし（**pydantic を import しない**＝内部設計 §3.3 I-2）
    プロジェクト内: simulator.domain.tester_settings_exceptions /
                    simulator.usecase.tester_settings
"""
from __future__ import annotations

from simulator.adapter.tester_settings.header_comment import (
    HeaderCommentInfo,
    parse_header_comment,
)
from simulator.adapter.tester_settings.ini_codec import (
    MAX_FILE_BYTES,
    MAX_INPUT_LINES,
    MAX_LINE_CHARS,
    SECTION_ORDER,
    SECTION_TESTER,
    SECTION_TESTER_INPUTS,
    STANDARD_KEY_ORDER,
    TESTER_KEY_SPECS,
    TESTER_KEYS,
    build_document,
    document_from_entries,
    decode,
    parse,
    read_bytes,
    read_document,
    serialize,
    split_input_value,
    split_lines,
    write_document,
)

__all__ = [
    # 上限・書式定数
    "MAX_FILE_BYTES",
    "MAX_INPUT_LINES",
    "MAX_LINE_CHARS",
    "SECTION_ORDER",
    "SECTION_TESTER",
    "SECTION_TESTER_INPUTS",
    "STANDARD_KEY_ORDER",
    "TESTER_KEYS",
    "TESTER_KEY_SPECS",
    # 字句層 8 関数（＋ `||` 分解の単一ソース）
    "build_document",
    "document_from_entries",
    "decode",
    "parse",
    "read_bytes",
    "read_document",
    "serialize",
    "split_input_value",
    "split_lines",
    "write_document",
    # 1 行目コメント（API-08）
    "HeaderCommentInfo",
    "parse_header_comment",
]
