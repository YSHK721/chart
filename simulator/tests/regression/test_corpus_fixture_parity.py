"""corpus 入力源のフィクスチャ化と原典一致の固定（A-4・内部設計 §9.3 D-06 の改訂）。

背景（実測 2026-08-18）:
    一次情報 corpus `sample/MQL5/Profiles/Tester/*.ini`（44 件・UTF-16LE+BOM+CRLF）は
    `.gitignore` の `sample/` により Git 追跡外である。そのため corpus 依存の受入条件
    T-01・T-05・T-07・T-09 は「`sample/` 存在時のみ実行」の条件付きスキップとなり、
    CI では一次情報との突合が一度も行われない（内部設計 §9.3 が「CI の空洞化」と認識）。

対策の構造:
    追跡対象の `simulator/tests/fixtures/tester_ini/` へ 44 件をバイト列そのまま複製し、
    これを corpus 依存テストの**既定の入力源**とする。`sample/` は入力源ではなくなり、
    「複製が原典から乖離していないか」の検証にのみ使う。

    本モジュールはその 2 点を固定する:
      - 既定の入力源が追跡フィクスチャであること（原典不在でも T-01/T-05/T-07/T-09 が走る）
      - 追跡フィクスチャが原典と SHA-256 で全件一致すること（原典存在時のみ検定）

    走査・スキップ機構は `simulator/tests/unit/tester_settings_corpus.py` の 1 箇所だけに
    あり、本モジュールは再実装せず import する（複製を作らない）。
"""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from simulator.tests.regression.corpus_cases import (
    CORPUS_DIR,
    CORPUS_FILE_COUNT,
    CORPUS_FILES,
    ORIGIN_DIR,
    file_digest,
    origin_files,
    requires_origin,
)

#: 追跡フィクスチャの所在（本テストが所有する期待値。実装側の定数と突合する）。
EXPECTED_FIXTURE_DIR: Path = (
    Path(__file__).resolve().parents[1] / "fixtures" / "tester_ini"
)


class TestTheDefaultInputSourceIsTracked:
    """corpus 依存テストの既定の入力源は Git 追跡下のフィクスチャである。"""

    def test_the_default_input_source_is_the_tracked_fixture_directory(self):
        # Arrange / Act
        observed = CORPUS_DIR

        # Assert
        assert observed == EXPECTED_FIXTURE_DIR, (
            "既定の入力源が追跡フィクスチャでない。"
            f"observed={observed} expected={EXPECTED_FIXTURE_DIR}"
        )

    def test_every_corpus_file_is_served_from_the_tracked_fixture_directory(self):
        # Arrange
        files = tuple(CORPUS_FILES)

        # Act
        strays = [path for path in files if path.parent != EXPECTED_FIXTURE_DIR]

        # Assert
        assert len(files) == CORPUS_FILE_COUNT, (
            f"追跡フィクスチャの件数が {CORPUS_FILE_COUNT} でない（実測 {len(files)}）"
        )
        assert strays == [], f"追跡フィクスチャ外から供給されたファイルがある: {strays}"


@requires_origin
class TestTheTrackedFixtureMatchesTheUntrackedOrigin:
    """追跡フィクスチャは一次情報 corpus のバイト列複製である（原典存在時のみ検定）。

    この検定が無いと、複製がいつの間にか原典から乖離しても誰も気付かない。原典を
    持つ環境（開発機・リリース前チェック）でのみ走り、`TESTER_INI_CORPUS_REQUIRED=1`
    を与えた実行では原典不在そのものを失敗として扱う。
    """

    def test_the_fixture_file_names_are_exactly_the_origin_file_names(self):
        # Arrange
        origin_names = {path.name for path in origin_files()}

        # Act
        fixture_names = {path.name for path in CORPUS_FILES}

        # Assert
        assert fixture_names == origin_names, (
            "フィクスチャと原典のファイル名集合が一致しない。"
            f"欠落={sorted(origin_names - fixture_names)} "
            f"余剰={sorted(fixture_names - origin_names)} origin={ORIGIN_DIR}"
        )

    def test_every_fixture_is_byte_identical_to_its_origin(self):
        # Arrange
        origins = {path.name: path for path in origin_files()}
        # 空の原典に対して比較すると本検定は**空虚に真**になる（実測 2026-08-18:
        # TESTER_INI_CORPUS_REQUIRED=1 かつ原典不在で pass した）。件数を先に固定する。
        assert len(origins) == CORPUS_FILE_COUNT, (
            f"原典の件数が {CORPUS_FILE_COUNT} でない（実測 {len(origins)}・{ORIGIN_DIR}）。"
            "この状態では以下のバイト一致検定が空虚に真になるため先に落とす"
        )

        # Act
        diverged = {
            path.name: (file_digest(path), file_digest(origins[path.name]))
            for path in CORPUS_FILES
            if path.name in origins
            and file_digest(path) != file_digest(origins[path.name])
        }

        # Assert
        assert diverged == {}, (
            "追跡フィクスチャが原典から乖離している（SHA-256 不一致）。"
            f"fixture={CORPUS_DIR} origin={ORIGIN_DIR} 乖離={diverged}"
        )


def _inside_git_work_tree() -> bool:
    """フィクスチャが git の作業ツリー内にあるか（属性検定の前提）。"""
    try:
        completed = subprocess.run(
            ["git", "rev-parse", "--is-inside-work-tree"],
            capture_output=True,
            text=True,
            cwd=EXPECTED_FIXTURE_DIR,
            check=False,
        )
    except OSError:
        return False
    return completed.returncode == 0 and completed.stdout.strip() == "true"


#: git 属性の検定は git 作業ツリー内でしか意味を持たない（CI のチェックアウトは常に該当）。
#: リポジトリから切り出した素のディレクトリで走らせたときだけ外れる。
requires_git_work_tree = pytest.mark.skipif(
    not _inside_git_work_tree(),
    reason=(
        f"{EXPECTED_FIXTURE_DIR} が git 作業ツリー内にないため属性を検定できない"
        "（git チェックアウト上では必ず実行される）"
    ),
)


@requires_git_work_tree
class TestGitCannotRewriteTheFixtureBytes:
    """フィクスチャのバイト列が git の改行正規化から保護されている。

    フィクスチャは UTF-16LE + BOM + CRLF である。往復バイト一致（T-01）は
    「チェックアウトされたバイト列が原典と 1 バイトも違わない」ことに依存する。
    git は既定では NUL バイトを含むファイルを binary と自動判定して変換しないが、
    それは**ヒューリスティック**であり、上位に `* text=auto` を持つ `.gitattributes`
    が置かれた時点で CRLF が LF へ潰され、CI だけが落ちる（原典は Git 追跡外なので
    原因が corpus 側に見えない）。自動判定に頼らず属性で固定していることを検定する。
    """

    def test_the_fixture_directory_pins_the_files_as_binary(self):
        # Arrange
        sample = min(CORPUS_FILES, key=lambda path: path.name)

        # Act
        completed = subprocess.run(
            ["git", "check-attr", "text", "eol", "--", str(sample)],
            capture_output=True,
            text=True,
            cwd=sample.parent,
            check=False,
        )
        attributes = completed.stdout

        # Assert
        assert completed.returncode == 0, f"git check-attr が失敗した: {completed.stderr}"
        assert "text: unset" in attributes, (
            "フィクスチャに `-text` が設定されていない。git の binary 自動判定に依存した"
            f"状態であり、上位の `* text=auto` で CRLF が潰される。実測: {attributes!r}"
        )
