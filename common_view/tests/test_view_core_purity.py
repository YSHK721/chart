"""アーキ回帰: ``common_view`` パッケージの依存純度（ISSUE-479 F-7d）。

common_view は「表示・偶有・可変層」の共有プリミティブ（配色・線幅・LWC アダプタ）を置く
パッケージであり、**どのアクターにも属さない**。したがって import してよいのは stdlib と
描画データの型（numpy / pandas）と自パッケージのみで、シミュレータ・指標 UI・ダッシュボード・
統合 UI・市場データ・運用スクリプトといったアクター、および計算層（common）を参照しては
ならない（参照すると共有プリミティブが特定アクターに縛られ、安定度逆転・循環の起点になる）。
禁止する根の一覧は _FORBIDDEN_ROOTS が単一の定義である。

現状すべて緑＝**回帰錨**である（違反を直すためではなく、違反の混入を検出するために置く）。
検査に力があること（合成ソースで違反を実際に検出し、非違反を誤検出しないこと）を併せて固定する。

走査ヘルパはリポジトリのアーキ回帰テストの既存様式に倣い各テストモジュールで自己完結させる。
パッケージ独立性を検査するモジュールが他パッケージのテストを import すると、検査対象の性質
そのものを壊すため。
"""
from __future__ import annotations

import ast
import sys
from pathlib import Path

import pytest

_PKG = Path(__file__).resolve().parents[1]

#: 走査対象（本番のみ・``tests`` は下位ディレクトリなので glob で自然に除外される）。
_SOURCES = sorted(_PKG.glob("*.py"))

#: 自パッケージ名（ディレクトリ名から導出。名前を焼き込まない）。
_SELF = _PKG.name

#: 描画データの型として許す偶有的技術。
_ALLOWED_TECH = {"numpy", "pandas"}

#: 参照してはならないアクター／層（明示。allowlist の裏返しだが、違反時のメッセージを具体化する）。
_FORBIDDEN_ROOTS = {
    "simulator",
    "indigators",
    "dashboard_ui",
    "unified_ui",
    "marketdata",
    "tools",
    "common",
}


def _imported_roots(source: str) -> set[str]:
    """ソース文字列の絶対 import 文から、パッケージ根の集合を返す（相対 import は自パッケージ）。"""
    roots: set[str] = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            for alias in node.names:
                roots.add(alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom):
            if node.level == 0 and node.module:
                roots.add(node.module.split(".")[0])
    return roots


def test_scan_target_is_not_empty() -> None:
    """走査対象が空なら本テストは恒真式に退化する（検査の生存確認）。"""
    assert _SOURCES, f"走査対象が空です: {_PKG}"


@pytest.mark.parametrize("path", _SOURCES, ids=lambda p: p.name)
def test_module_imports_only_stdlib_numpy_pandas_or_self(path: Path) -> None:
    allowed = set(sys.stdlib_module_names) | _ALLOWED_TECH | {_SELF}
    roots = _imported_roots(path.read_text(encoding="utf-8"))
    outside = roots - allowed
    assert not outside, (
        f"{path.name} が許可外のパッケージを import: {sorted(outside)}"
        f"（{_SELF} は stdlib / {sorted(_ALLOWED_TECH)} / 自パッケージのみに依存する）"
    )


@pytest.mark.parametrize("path", _SOURCES, ids=lambda p: p.name)
def test_module_does_not_depend_on_any_actor(path: Path) -> None:
    roots = _imported_roots(path.read_text(encoding="utf-8"))
    leaked = roots & _FORBIDDEN_ROOTS
    assert not leaked, f"{path.name} がアクター／他層に依存: {sorted(leaked)}"


def test_the_check_detects_synthetic_violations() -> None:
    """検出力: 合成ソース（実ファイルを作らない）で違反を検出し、非違反を誤検出しない。"""
    for offender in (
        "from simulator.framework import x\n",
        "import marketdata.tick_m1\n",
        "from common.applied_price import applied_price\n",
        "import tools.watch_loop\n",
    ):
        assert _imported_roots(offender) & _FORBIDDEN_ROOTS, f"検出できていません: {offender!r}"

    for clean in (
        "from __future__ import annotations\n",
        "import numpy as np\nimport pandas as pd\n",
        "from .level_colors import level_colors\n",      # 相対 import＝自パッケージ
        f"from {_SELF}.level_style import LEVEL_LINE_WIDTH\n",
        "from typing import Protocol\n",
    ):
        roots = _imported_roots(clean)
        assert not (roots & _FORBIDDEN_ROOTS), f"誤検出しています: {clean!r}"
        assert not (roots - (set(sys.stdlib_module_names) | _ALLOWED_TECH | {_SELF}))


def test_scan_parses_each_source_exactly_once() -> None:
    """計算量テスト: 対象ファイル数 == AST パース回数（発行 − 判定に使ったソース数 = 0）。

    オーダー表明として対象 1 件 / 2 件の 2 点で、発行が対象数だけで決まることを固定する
    （ファイルの長さ・import 数では増えない）。回数リテラルは焼き込まない。
    """
    parsed: list[str] = []
    real_parse = ast.parse

    def _spy(source, *args, **kwargs):
        parsed.append(source)
        return real_parse(source, *args, **kwargs)

    ast.parse = _spy
    try:
        one = _SOURCES[:1]
        used_one = [_imported_roots(p.read_text(encoding="utf-8")) for p in one]
        issued_one = len(parsed)
        parsed.clear()

        two = _SOURCES[:2] if len(_SOURCES) >= 2 else _SOURCES
        used_two = [_imported_roots(p.read_text(encoding="utf-8")) for p in two]
        issued_two = len(parsed)
    finally:
        ast.parse = real_parse

    assert issued_one - len(used_one) == 0, "1 ファイルあたりのパース発行が判定使用数を超えています"
    assert issued_two - len(used_two) == 0, "1 ファイルあたりのパース発行が判定使用数を超えています"
    assert issued_two == len(two), "パース発行が対象ファイル数以外の要因で増えています"
