"""corpus 44 件のロード決定論（T-07・内部設計 §9.2）。

同じ `.ini` を 2 回読めば、生表現（`source`）を含めて等価な `TesterSettings` が
得られる。ここが崩れると、設定由来の実行が再現しなくなり、MT5 突合（bit-exact）
の前提が失われる。

「等価」は 2 回のロードが**別インスタンス**であることを併せて確認する。同一
オブジェクトを 2 度見ているだけなら `==` は自明に真になり、決定論を何も検定して
いないことになるため。
"""
from __future__ import annotations

from simulator.framework.tester_settings.loader import load_tester_settings
from simulator.tests.regression.corpus_cases import corpus_case, requires_corpus


@requires_corpus
class TestCorpusLoadIsDeterministic:
    """corpus 1 件ごとに 2 回ロードして等価性を固定する。"""

    @corpus_case
    def test_two_loads_produce_equal_settings(self, corpus_path):
        # Arrange / Act
        first = load_tester_settings(corpus_path)
        second = load_tester_settings(corpus_path)

        # Assert
        assert first is not second, f"{corpus_path.name}: 2 回のロードが同一インスタンスを返した"
        assert first == second, f"{corpus_path.name}: 2 回のロード結果が等価でない"

    @corpus_case
    def test_two_loads_produce_equal_raw_documents(self, corpus_path):
        # Arrange / Act
        first = load_tester_settings(corpus_path).source
        second = load_tester_settings(corpus_path).source

        # Assert（`source` は往復の正典。dataclass の `==` は行原文列まで比較する）
        assert first is not None and second is not None
        assert first is not second, f"{corpus_path.name}: 2 回のロードが同一 source を返した"
        assert first == second, f"{corpus_path.name}: source（生表現）が 2 回のロードで一致しない"
        assert first.lines == second.lines
        assert (first.encoding, first.newline, first.has_bom, first.trailing_newline) == (
            second.encoding,
            second.newline,
            second.has_bom,
            second.trailing_newline,
        )
