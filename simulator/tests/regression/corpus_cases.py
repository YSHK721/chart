"""regression スイートが共有する corpus パラメータ化（複製を作らないための単一ソース）。

条件付きスキップ機構そのもの（`requires_corpus` / `CORPUS_DIR` / `CORPUS_REQUIRED` /
corpus 直読補助）は `simulator/tests/unit/tester_settings_corpus.py` が唯一の実装
（内部設計 §9.3 D-06）であり、本モジュールは**再実装せず import** して再輸出する。

`unit/` 配下の機構を `regression/` から import する判断の理由:
    機構本体を共有しやすい場所へ移すと、`tests/unit/tester_settings_corpus.py` と
    それを import 済みの既存単体テスト（`test_tester_ini_codec.py` ほか）の改変が
    発生し、本フェーズの絶対制約「既存ファイル改変 0 件」および内部設計 §9.4 G-1
    （新規追加のみ）に反する。したがって**実装は移さず import 経路だけを通す**。
    移設が妥当になるのは既存ファイル改変が承認された時点であり、本書ではその
    判断を行わない。

本モジュールが持つ固有の関心は「corpus 44 件を 1 ケース 1 ファイルへ parametrize
する」ことだけである。走査もスキップ判定も上流（単一ソース）に委ねる。

本モジュールは**テストではない**（`test` で始まらないため pytest は収集しない）。
"""
from __future__ import annotations

from pathlib import Path

import pytest

from simulator.tests.unit.tester_settings_corpus import (
    CORPUS_DIR,
    CORPUS_REQUIRED,
    corpus_files,
    corpus_first_line,
    corpus_tester_entries,
    corpus_tester_keys,
    requires_corpus,
)

__all__ = [
    "CORPUS_DIR",
    "CORPUS_FILES",
    "CORPUS_FILE_COUNT",
    "CORPUS_REQUIRED",
    "corpus_case",
    "corpus_first_line",
    "corpus_id",
    "corpus_report_lines",
    "corpus_tester_entries",
    "corpus_tester_keys",
    "requires_corpus",
]

#: corpus のファイル一覧（収集時に 1 回だけ確定する）。不在なら空列。
CORPUS_FILES: tuple[Path, ...] = tuple(corpus_files())

#: corpus の件数（基本設計 §2.2.3 の実測。設計文書由来であり実装からは導けない）。
CORPUS_FILE_COUNT: int = 44


def corpus_id(path: Path) -> str:
    """parametrize の case id（どのファイルで落ちたかを一意に示す）。"""
    return path.name


def corpus_report_lines() -> list[str]:
    """pytest ヘッダへ出す corpus の状態行（内部設計 §9.3「スキップの可視化」）。

    corpus は環境によって存在しない（`sample/` は Git 追跡外＝F-20・CON-05）。
    スキップが**沈黙**すると「corpus を読まずに緑になった実行」と「44 件を実測して
    緑になった実行」が報告上区別できない。そのため所在・件数・必須化フラグを毎回示す。

    文言の生成をここ 1 箇所に置く理由: フックを置く `conftest.py` は起動形ごとに
    読まれる位置が変わり得る（pytest は起動時に「引数ディレクトリとその祖先」の
    conftest しか読まない）。文言を各 conftest に書き写すと表示が食い違うため、
    実装は本モジュールに 1 つだけ置き、conftest はそれを呼ぶだけにする。
    """
    state = "present" if CORPUS_FILES else "ABSENT"
    line = (
        f"tester-settings corpus: {state} files={len(CORPUS_FILES)}"
        f"/{CORPUS_FILE_COUNT} dir={CORPUS_DIR} "
        f"TESTER_INI_CORPUS_REQUIRED={'1' if CORPUS_REQUIRED else '0'}"
    )
    if not CORPUS_FILES:
        line += (
            " -> corpus 依存テスト（T-01/T-05/T-07/T-08/T-09）はスキップされる。"
            "必須化するには TESTER_INI_CORPUS_REQUIRED=1 を与える"
        )
    return [line]


#: corpus 1 件を 1 ケースへ割り当てる parametrize。
#:
#: corpus 不在時は空パラメータ集合になり pytest が当該テストをスキップ扱いにする。
#: そのスキップが沈黙にならないよう、(1) `conftest.pytest_report_header` が
#: corpus の有無と件数を毎回表示し、(2) `test_corpus_structural_facts.py` の
#: 件数テスト（parametrize しない）が `TESTER_INI_CORPUS_REQUIRED=1` の実行で
#: **失敗**する。両者で「corpus を読まずに緑になる」経路を塞ぐ。
corpus_case = pytest.mark.parametrize("corpus_path", CORPUS_FILES, ids=corpus_id)
