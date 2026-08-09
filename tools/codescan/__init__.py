"""codescan — コード重複・依存関係・シンボル種別を 1 行単位で確認するツール。

本ファイルは**合成根**である。具象解析器を知っているのはここだけで、他のモジュールは
``LanguageAnalyzer``（Protocol）としか結線しない。言語を足すときに変更するのは
``default_registry`` の 1 行だけであり、既存の解析器・重複検出・レポートには触れない。
"""
from __future__ import annotations

from .registry import AnalyzerRegistry


def default_registry() -> AnalyzerRegistry:
    """本ツールが既定で使う解析器一式を組み立てる。"""
    from .javascript_analyzer import JavaScriptAnalyzer
    from .python_analyzer import PythonAnalyzer

    return AnalyzerRegistry().register(PythonAnalyzer()).register(JavaScriptAnalyzer())


__all__ = ["AnalyzerRegistry", "default_registry"]
