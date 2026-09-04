"""regression スイートが共有する corpus パラメータ化（複製を作らないための単一ソース）。

走査・ゲート・同一性判定の機構そのもの（`CORPUS_DIR` / `ORIGIN_DIR` / `CORPUS_REQUIRED` /
`requires_corpus` / `requires_origin` / `file_digest` / corpus 直読補助）は
`simulator/tests/unit/tester_settings_corpus.py` が唯一の実装（内部設計 §9.3 D-06・
A-4 で改訂）であり、本モジュールは**再実装せず import** して再輸出する。

`unit/` 配下の機構を `regression/` から import する判断の理由:
    機構本体を共有しやすい場所へ移すと、`tests/unit/tester_settings_corpus.py` を
    import 済みの既存単体テスト（`test_tester_ini_codec.py` ほか）の改変が発生する。
    移設は本件（A-4）の目的（CI の空洞を塞ぐ）に不要であり、目的外の改変を増やすため
    行わない。**実装は移さず import 経路だけを通す**。

本モジュールが持つ固有の関心は「corpus 44 件を 1 ケース 1 ファイルへ parametrize
する」ことだけである。走査もゲート判定も上流（単一ソース）に委ねる。

本モジュールは**テストではない**（`test` で始まらないため pytest は収集しない）。
"""
from __future__ import annotations

from pathlib import Path

import pytest

from simulator.tests.unit.tester_settings_corpus import (
    CORPUS_DIR,
    CORPUS_REQUIRED,
    ORIGIN_DIR,
    corpus_files,
    corpus_first_line,
    corpus_tester_entries,
    corpus_tester_keys,
    file_digest,
    origin_files,
    requires_corpus,
    requires_origin,
)

__all__ = [
    "CORPUS_DIR",
    "CORPUS_FILES",
    "CORPUS_FILE_COUNT",
    "CORPUS_REQUIRED",
    "ORIGIN_DIR",
    "corpus_case",
    "corpus_first_line",
    "corpus_id",
    "corpus_report_lines",
    "corpus_tester_entries",
    "corpus_tester_keys",
    "file_digest",
    "origin_files",
    "requires_corpus",
    "requires_origin",
]

#: corpus のファイル一覧＝既定の入力源（追跡フィクスチャ）。収集時に 1 回だけ確定する。
CORPUS_FILES: tuple[Path, ...] = tuple(corpus_files())

#: corpus の件数（基本設計 §2.2.3 の実測。設計文書由来であり実装からは導けない）。
CORPUS_FILE_COUNT: int = 44


def corpus_id(path: Path) -> str:
    """parametrize の case id（どのファイルで落ちたかを一意に示す）。"""
    return path.name


def corpus_report_lines() -> list[str]:
    """pytest ヘッダへ出す corpus の状態行（内部設計 §9.3「スキップの可視化」）。

    入力源（追跡フィクスチャ）と原典（`sample/`・Git 追跡外＝F-20・CON-05）は別物で
    あり、後者は環境によって存在しない。どちらの状態も**沈黙**させると
    「原典と突合せずに緑になった実行」と「原典と全件突合して緑になった実行」が
    報告上区別できない。そのため両者の所在・件数・必須化フラグを毎回示す。

    文言の生成をここ 1 箇所に置く理由: フックを置く `conftest.py` は起動形ごとに
    読まれる位置が変わり得る（pytest は起動時に「引数ディレクトリとその祖先」の
    conftest しか読まない）。文言を各 conftest に書き写すと表示が食い違うため、
    実装は本モジュールに 1 つだけ置き、conftest はそれを呼ぶだけにする。
    """
    origins = origin_files()
    source_state = "present" if CORPUS_FILES else "ABSENT"
    origin_state = "present" if origins else "ABSENT"
    lines = [
        f"tester-settings corpus source (tracked fixture): {source_state} "
        f"files={len(CORPUS_FILES)}/{CORPUS_FILE_COUNT} dir={CORPUS_DIR}",
        f"tester-settings corpus origin (untracked): {origin_state} "
        f"files={len(origins)}/{CORPUS_FILE_COUNT} dir={ORIGIN_DIR} "
        f"TESTER_INI_CORPUS_REQUIRED={'1' if CORPUS_REQUIRED else '0'}",
    ]
    if not CORPUS_FILES:
        lines.append(
            " -> 追跡フィクスチャが失われている。corpus 依存テスト"
            "（T-01/T-05/T-07/T-08/T-09）が走らない壊れたチェックアウトである"
        )
    if not origins:
        lines.append(
            " -> 原典不在のため「フィクスチャと原典の一致検証」のみスキップする"
            "（corpus 依存テスト自体はフィクスチャで走る）。"
            "必須化するには TESTER_INI_CORPUS_REQUIRED=1 を与える"
        )
    return lines


#: corpus 1 件を 1 ケースへ割り当てる parametrize（入力源＝追跡フィクスチャ）。
#:
#: 入力源は Git 追跡下にあるため、通常のチェックアウトでは常に 44 ケースが生成され
#: 空パラメータ集合（＝沈黙スキップ）にはならない。フィクスチャごと失われた場合に
#: 備え、(1) `conftest.pytest_report_header` が入力源と原典の有無・件数を毎回表示し、
#: (2) `test_corpus_structural_facts.py` の件数テスト（parametrize しない）と
#: `test_corpus_fixture_parity.py` の入力源テストが**失敗**する。
#: 三者で「corpus を読まずに緑になる」経路を塞ぐ。
corpus_case = pytest.mark.parametrize("corpus_path", CORPUS_FILES, ids=corpus_id)
