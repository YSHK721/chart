"""corpus（一次情報）直読の共有補助（内部設計 §9.3 D-06）。

`sample/MQL5/Profiles/Tester/*.ini` は MT5 が実際に書いた 44 件であり、本機能の
仕様の一次情報である。ただし `sample/` は Git 追跡外（F-20・CON-05）であるため、
corpus 不在の環境では corpus 依存テストを条件付きスキップし、
``TESTER_INI_CORPUS_REQUIRED=1`` を与えた実行では必須化（skip 0）する。

本モジュールに置く理由: この条件付きスキップ機構を各テストモジュールが書き写すと、
必須化の判定条件が複数箇所に生じ、片方だけが腐る。機構の宣言は 1 箇所に置き、
corpus を読むテストモジュールはこれを import して使う（合成データ生成器を
`tester_settings_synthetic.py` の 1 箇所に置いているのと同じ方針）。

本モジュールは**テストではない**（`test` で始まらないため pytest は収集しない）。
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest

from simulator.tests.unit.tester_settings_synthetic import CRLF, TESTER_SECTION

#: 一次情報 corpus。`sample/` は Git 追跡外（F-20・CON-05）。
CORPUS_DIR: Path = Path(__file__).resolve().parents[3] / "sample" / "MQL5" / "Profiles" / "Tester"
#: `1` を与えた実行ではスキップせず失敗させる（リリース前チェック・開発機での必須化）。
CORPUS_REQUIRED: bool = os.environ.get("TESTER_INI_CORPUS_REQUIRED") == "1"


def corpus_files() -> list[Path]:
    """corpus の `.ini` 一覧（不在なら空列）。"""
    if not CORPUS_DIR.is_dir():
        return []
    return sorted(CORPUS_DIR.glob("*.ini"))


def corpus_tester_keys(path: Path) -> tuple[str, ...]:
    """corpus 1 件の `[Tester]` キーを**出現順**で返す（実装の parse を通さない）。"""
    text = path.read_bytes().decode("utf-16")
    keys: list[str] = []
    in_tester = False
    for raw in text.split(CRLF):
        if raw == TESTER_SECTION:
            in_tester = True
            continue
        if raw.startswith("["):
            in_tester = False
            continue
        if in_tester and "=" in raw:
            keys.append(raw.split("=", 1)[0])
    return tuple(keys)


def corpus_tester_entries(path: Path) -> dict[str, str]:
    """corpus 1 件の `[Tester]` の (キー→値) を返す（実装の parse を通さない）。

    corpus と実装の突合を「実装で読んだ値どうしの比較」にしないための直読である。
    """
    text = path.read_bytes().decode("utf-16")
    entries: dict[str, str] = {}
    in_tester = False
    for raw in text.split(CRLF):
        if raw == TESTER_SECTION:
            in_tester = True
            continue
        if raw.startswith("["):
            in_tester = False
            continue
        if in_tester and "=" in raw:
            key, value = raw.split("=", 1)
            entries[key] = value
    return entries


def corpus_first_line(path: Path) -> str | None:
    """corpus 1 件の 1 行目（存在すれば原文・無ければ ``None``）。"""
    text = path.read_bytes().decode("utf-16")
    lines = text.split(CRLF)
    return lines[0] if lines and lines[0] != "" else None


requires_corpus = pytest.mark.skipif(
    not corpus_files() and not CORPUS_REQUIRED,
    reason=(
        "sample/ は Git 追跡外（CON-05）のため corpus 不在。"
        "TESTER_INI_CORPUS_REQUIRED=1 で必須化する（内部設計 §9.3 D-06）"
    ),
)
