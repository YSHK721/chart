"""corpus（一次情報）直読の共有補助（内部設計 §9.3 D-06・A-4 で改訂）。

`sample/MQL5/Profiles/Tester/*.ini` は MT5 が実際に書いた 44 件であり、本機能の
仕様の**一次情報（原典）**である。ただし `sample/` は Git 追跡外（F-20・CON-05）であり、
CI には存在しない。

改訂前の構造とその欠陥（実測 2026-08-18）:
    原典を唯一の入力源にしていたため、corpus 依存の受入条件 T-01・T-05・T-07・T-09 は
    「`sample/` 存在時のみ実行」の条件付きスキップになっていた。結果として CI では
    一次情報との突合が一度も行われず、合成データによる構造網羅だけが緑になっていた
    （内部設計 §9.3 が「CI の空洞化」と認識していた状態）。

改訂後の構造（入力源と検証対象を分ける）:
    - `CORPUS_DIR`（`simulator/tests/fixtures/tester_ini/`）＝ 原典のバイト列複製。
      Git 追跡下にあり常に存在するため、**corpus 依存テストの既定の入力源**とする。
      これにより T-01・T-05・T-07・T-09 は原典不在の環境でも走る（skip 0）。
    - `ORIGIN_DIR`（`sample/…`）＝ 原典。入力源ではなく「複製が原典から乖離して
      いないか」の**検証にのみ**使い、読むだけで書き換えない。原典を持つ環境でのみ
      `requires_origin` 配下の一致検証が走り、`TESTER_INI_CORPUS_REQUIRED=1` を
      与えた実行では原典不在そのものを失敗として扱う。

本モジュールに置く理由: この走査・ゲート・同一性判定を各テストモジュールが書き写すと、
判定条件が複数箇所に生じ、片方だけが腐る。機構の宣言は 1 箇所に置き、
corpus を読むテストモジュールはこれを import して使う（合成データ生成器を
`tester_settings_synthetic.py` の 1 箇所に置いているのと同じ方針）。

本モジュールは**テストではない**（`test` で始まらないため pytest は収集しない）。
"""
from __future__ import annotations

import hashlib
import os
from pathlib import Path

import pytest

from simulator.tests.unit.tester_settings_synthetic import CRLF, TESTER_SECTION

#: corpus 依存テストの**既定の入力源**（Git 追跡下のフィクスチャ。原典のバイト列複製）。
CORPUS_DIR: Path = Path(__file__).resolve().parents[1] / "fixtures" / "tester_ini"
#: 一次情報 corpus の**原典**。`sample/` は Git 追跡外（F-20・CON-05）で不在の環境がある。
#: 入力源ではなく「フィクスチャが原典から乖離していないか」の検証にのみ使う。
ORIGIN_DIR: Path = Path(__file__).resolve().parents[3] / "sample" / "MQL5" / "Profiles" / "Tester"
#: `1` を与えた実行では原典不在をスキップせず失敗させる（リリース前チェック・開発機での必須化）。
CORPUS_REQUIRED: bool = os.environ.get("TESTER_INI_CORPUS_REQUIRED") == "1"


def corpus_files() -> list[Path]:
    """corpus の `.ini` 一覧（既定の入力源＝追跡フィクスチャ。不在なら空列）。"""
    if not CORPUS_DIR.is_dir():
        return []
    return sorted(CORPUS_DIR.glob("*.ini"))


def origin_files() -> list[Path]:
    """原典 corpus の `.ini` 一覧（不在なら空列）。一致検証専用で読むだけである。"""
    if not ORIGIN_DIR.is_dir():
        return []
    return sorted(ORIGIN_DIR.glob("*.ini"))


def file_digest(path: Path) -> str:
    """ファイルの SHA-256（16 進）。バイト列同一性の判定はこの 1 箇所を使う。"""
    return hashlib.sha256(path.read_bytes()).hexdigest()


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


#: corpus 依存テストのゲート。既定の入力源は Git 追跡下のフィクスチャであるため、
#: 健全なチェックアウトでは条件は常に偽＝**スキップしない**（原典 `sample/` の有無に
#: 依存しない）。フィクスチャごと失われた壊れたチェックアウトでのみ発火する保険である。
requires_corpus = pytest.mark.skipif(
    not corpus_files(),
    reason=(
        f"追跡フィクスチャ {CORPUS_DIR} が存在しない（チェックアウトが壊れている）。"
        "本来スキップしてはならない経路である（内部設計 §9.3 D-06 改訂）"
    ),
)

#: 原典突合のゲート。原典 `sample/` は Git 追跡外のため CI では不在であり、
#: そのときだけ一致検証をスキップする。`TESTER_INI_CORPUS_REQUIRED=1` で必須化する。
requires_origin = pytest.mark.skipif(
    not origin_files() and not CORPUS_REQUIRED,
    reason=(
        "sample/ は Git 追跡外（CON-05）のため原典不在。フィクスチャとの一致検証のみ"
        "スキップする（入力源はフィクスチャなので corpus 依存テストは走る）。"
        "TESTER_INI_CORPUS_REQUIRED=1 で必須化する（内部設計 §9.3 D-06 改訂）"
    ),
)
