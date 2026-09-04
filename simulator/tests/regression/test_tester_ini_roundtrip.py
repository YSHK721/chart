"""corpus 44 件の往復バイト一致（T-01・内部設計 §9.2 / NFR-02）。

MT5 が実際に書いた `.ini` を読み、書き戻したバイト列が**元ファイルと 1 バイトも
違わない**ことを固定する。ここが崩れると「MT5 の設定ファイルを壊さずに扱える」
という本機能の前提（基本設計 NFR-02）が失われる。

期待値は corpus ファイルそのもの（一次情報）であり、実装から生成した値ではない
（characterization テストにしない）。

検証経路は 2 本:
    1. `serialize(settings.source)` — 字句層の復元（R6 / R7 / R9）
    2. `dump_tester_settings` — 公開 API-02 の経路（符号化・BOM・改行の書出しを含む）
2 本に分ける理由: 1 が通っても書出し側（`write_document`）が符号化を取り違えれば
実ファイルは壊れる。往復の保証はファイルに落ちるところまで測らないと成立しない。
"""
from __future__ import annotations

from simulator.adapter.tester_settings.ini_codec import serialize
from simulator.framework.tester_settings.loader import dump_tester_settings, load_tester_settings
from simulator.tests.regression.corpus_cases import corpus_case, requires_corpus


@requires_corpus
class TestCorpusRoundTripIsByteExact:
    """corpus 1 件ごとに往復バイト一致を固定する（落ちたファイルは case id で判る）。"""

    @corpus_case
    def test_serialize_of_the_loaded_source_equals_the_original_bytes(self, corpus_path):
        # Arrange
        original = corpus_path.read_bytes()

        # Act
        settings = load_tester_settings(corpus_path)

        # Assert
        assert settings.source is not None, f"{corpus_path.name}: source（生表現）が保持されていない"
        restored = serialize(settings.source)
        assert restored == original, (
            f"{corpus_path.name}: serialize が元バイト列と不一致 "
            f"(元 {len(original)} bytes / 復元 {len(restored)} bytes)"
        )

    @corpus_case
    def test_dump_writes_back_the_original_bytes(self, corpus_path, tmp_path):
        # Arrange
        original = corpus_path.read_bytes()
        settings = load_tester_settings(corpus_path)
        destination = tmp_path / corpus_path.name

        # Act
        dump_tester_settings(settings, destination)

        # Assert
        written = destination.read_bytes()
        assert written == original, (
            f"{corpus_path.name}: dump が元バイト列と不一致 "
            f"(元 {len(original)} bytes / 書出 {len(written)} bytes)"
        )
