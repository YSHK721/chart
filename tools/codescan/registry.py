"""言語解析器の境界（Protocol）と拡張子レジストリ。

呼び出し側（収集・重複検出・レポート）は ``LanguageAnalyzer`` にしか依存せず、
Python / JavaScript の具象は知らない（DIP）。言語を増やすときに変更するのは
合成根（``tools/codescan/__init__.py`` の ``default_registry``）1 箇所だけで、
本モジュールにも既存解析器にも手を入れない（OCP）。
"""
from __future__ import annotations

from pathlib import Path
from typing import Protocol, runtime_checkable

from .model import ModuleFacts


@runtime_checkable
class LanguageAnalyzer(Protocol):
    """1 言語分のソースを ``ModuleFacts`` へ変換する境界。"""

    #: レポート・集計で使う言語名。
    language: str
    #: 担当する拡張子（先頭ドット付き・小文字）。
    extensions: "frozenset[str]"

    def analyze(self, path: str, source: str) -> ModuleFacts:
        """``source`` を解析する。``path`` はリポジトリ相対パス（表示・突合用）。

        解析不能な入力でも例外を投げず、``ModuleFacts.errors`` に理由を入れて返す。
        1 ファイルの構文エラーで全体の走査を止めないため。
        """
        ...


class AnalyzerRegistry:
    """拡張子から解析器を引く索引。"""

    def __init__(self) -> None:
        self._by_ext: "dict[str, LanguageAnalyzer]" = {}

    def register(self, analyzer: LanguageAnalyzer) -> "AnalyzerRegistry":
        for ext in analyzer.extensions:
            self._by_ext[ext.lower()] = analyzer
        return self

    def for_path(self, path: "str | Path") -> "LanguageAnalyzer | None":
        return self._by_ext.get(Path(path).suffix.lower())

    @property
    def extensions(self) -> "frozenset[str]":
        return frozenset(self._by_ext)

    @property
    def languages(self) -> "frozenset[str]":
        return frozenset(a.language for a in self._by_ext.values())
