"""1 行目コメント解析 `parse_header_comment` の単体テスト（API-08・F-18）。

是正 E: 本モジュール追加前、`parse_header_comment` / `HeaderCommentInfo` を参照する
テストは **0 件**であり（`grep -rn "parse_header_comment\\|HeaderCommentInfo"
simulator/tests/` → 0）、`header_comment.py` の行カバレッジは 47.2%、46 行の解析
ロジック（`", "` 分解・forward 語の剥がし・`rsplit` による symbol / period 分離）は
一度も実行されていなかった。

固定する仕様:
    1. corpus 44 件の 1 行目がすべて解析でき、`[Tester]` の `Symbol` / `Period` /
       Model 語 / 期間語と整合すること（corpus 直読との突合）。
       ⚠️ `with_forward` の不一致は既知の 1 件のみ（ISSUE-390。F-9 の合否基準が
       未確定のため本ラウンドでは**現状をファイル名で固定**するに留める）。
    2. corpus 非依存の常時実行分（forward 語の有無・`", "` を含む対象名・`;` で
       始まらない入力・項目数不足・``None``）。**解析不能は ``None`` を返し例外を
       出さない**（検証補助であり正典ではないため＝内部設計 §4.5.3）。

⚠️ 語彙の対応表（Model 語 → `Model` の生値、期間語 → `Dates` / `FromDate`+`ToDate`）は
**本テストが所有する**。`header_comment.py` は対応表を持たない（持たせると同じ知識が
2 箇所になる＝モジュール docstring の設計判断）。
"""
from __future__ import annotations

from dataclasses import FrozenInstanceError

import pytest

from simulator.adapter.tester_settings.header_comment import (
    HeaderCommentInfo,
    parse_header_comment,
)
from simulator.tests.unit.tester_settings_corpus import (
    corpus_files,
    corpus_first_line,
    corpus_tester_entries,
    requires_corpus,
)
from simulator.usecase.tester_settings.enums import DatesPreset, TickModel

#: Model 語 → `Model` の生値（corpus 実測 4 種。F-3〜F-6）。本テストが所有する。
MODEL_WORDS: dict[str, TickModel] = {
    "every tick": TickModel.EVERY_TICK,
    "m1 ohlc": TickModel.ONE_MINUTE_OHLC,
    "open prices": TickModel.OPEN_PRICES_ONLY,
    "real ticks": TickModel.REAL_TICKS,
}
#: 期間語 → `Dates` の生値（corpus 実測 2 種。F-7）。本テストが所有する。
PRESET_WORDS: dict[str, DatesPreset] = {
    "entire history": DatesPreset.ENTIRE_HISTORY,
    "last year": DatesPreset.LAST_YEAR,
}

#: ISSUE-390（OPEN）: `with_forward`（コメント）と `ForwardMode`（`[Tester]`）が
#: 食い違う既知の 1 件。F-9 の合否基準が未確定のため、**現状をファイル名で固定**して
#: 「2 件目が現れたら落ちる」「この 1 件が解消したら落ちる」状態にする。
KNOWN_FORWARD_MISMATCH: str = (
    "TC24051903_24052301.JP225_ver24051601.H8.20120101_20121231.121.ini"
)


def expected_period_word(entries: dict[str, str]) -> str:
    """`[Tester]` の期間指定から期待される期間語（F-2・F-7）。"""
    from_date, to_date = entries.get("FromDate"), entries.get("ToDate")
    if from_date is not None and to_date is not None:
        return f"{from_date} - {to_date}"
    return {value.value: word for word, value in PRESET_WORDS.items()}[int(entries["Dates"])]


class TestCorpusHeaderComments:
    """corpus 44 件の 1 行目と `[Tester]` の突合（一次情報そのものとの突合）。

    corpus は実装の ``parse`` を通さず直読する（`tester_settings_corpus`）。実装で
    読んだ値どうしを比べると、読込側の誤りが両辺に等しく効いて検出できないため。
    """

    @requires_corpus
    def test_the_corpus_holds_the_expected_number_of_files(self):
        assert len(corpus_files()) == 44

    @requires_corpus
    def test_every_corpus_first_line_is_parsable(self):
        # Arrange
        unparsable = []
        # Act
        for path in corpus_files():
            if parse_header_comment(corpus_first_line(path)) is None:
                unparsable.append(path.name)
        # Assert: F-18 の書式は 44/44 で成立する
        assert unparsable == []

    @requires_corpus
    def test_the_parsed_symbol_and_period_match_the_tester_section(self):
        mismatches = []
        for path in corpus_files():
            info = parse_header_comment(corpus_first_line(path))
            entries = corpus_tester_entries(path)
            if (info.symbol, info.period) != (entries["Symbol"], entries["Period"]):
                mismatches.append(path.name)
        assert mismatches == []

    @requires_corpus
    def test_the_parsed_model_word_matches_the_model_key(self):
        mismatches = []
        for path in corpus_files():
            info = parse_header_comment(corpus_first_line(path))
            entries = corpus_tester_entries(path)
            if MODEL_WORDS[info.model_word] != int(entries["Model"]):
                mismatches.append((path.name, info.model_word, entries["Model"]))
        assert mismatches == []

    @requires_corpus
    def test_the_parsed_period_word_matches_the_date_range(self):
        mismatches = []
        for path in corpus_files():
            info = parse_header_comment(corpus_first_line(path))
            entries = corpus_tester_entries(path)
            if info.period_word != expected_period_word(entries):
                mismatches.append((path.name, info.period_word))
        assert mismatches == []

    @requires_corpus
    def test_the_parsed_subject_matches_the_expert_or_indicator_key(self):
        mismatches = []
        for path in corpus_files():
            info = parse_header_comment(corpus_first_line(path))
            entries = corpus_tester_entries(path)
            subject = entries.get("Expert", entries.get("Indicator"))
            if info.subject not in subject:
                mismatches.append((path.name, info.subject, subject))
        assert mismatches == []

    @requires_corpus
    def test_only_the_known_file_disagrees_about_the_forward_period(self):
        """ISSUE-390（OPEN）: `with_forward` と `ForwardMode` の不一致は 1 件のみ。

        F-9 の合否基準（`ForwardMode != 0` ⇔ コメントの forward 語）が未確定である
        ため、本ラウンドでは**現状を固定**する。2 件目が現れれば落ち、既知の 1 件が
        解消しても落ちる（どちらも仕様判断を要する変化である）。
        """
        mismatches = []
        for path in corpus_files():
            info = parse_header_comment(corpus_first_line(path))
            forward_mode = corpus_tester_entries(path).get("ForwardMode")
            has_forward = forward_mode is not None and int(forward_mode) != 0
            if info.with_forward != has_forward:
                mismatches.append(path.name)
        assert mismatches == [KNOWN_FORWARD_MISMATCH]

    @requires_corpus
    def test_every_observed_vocabulary_word_is_declared_in_this_module(self):
        # 対応表（本テストが所有）が corpus の語彙を過不足なく覆う
        observed_models, observed_presets = set(), set()
        for path in corpus_files():
            info = parse_header_comment(corpus_first_line(path))
            observed_models.add(info.model_word)
            if info.period_word in PRESET_WORDS:
                observed_presets.add(info.period_word)
        assert observed_models == set(MODEL_WORDS)
        assert observed_presets == set(PRESET_WORDS)


class TestForwardSuffix:
    """`, with forward period` の有無（F-9。corpus 非依存・常時実行）。"""

    BASE = ";Full optimization: TC24051903.ex5, JP225 H8, real ticks, entire history"

    def test_the_suffix_sets_the_flag_and_is_stripped_from_the_period_word(self):
        # Arrange / Act
        info = parse_header_comment(self.BASE + ", with forward period")
        # Assert
        assert info.with_forward is True
        assert info.period_word == "entire history"

    def test_the_absence_of_the_suffix_clears_the_flag(self):
        info = parse_header_comment(self.BASE)
        assert info.with_forward is False
        assert info.period_word == "entire history"

    def test_all_seven_fields_are_taken_verbatim(self):
        # 列挙へ写像せず原文のまま保持する（解析結果は正典ではない）
        assert parse_header_comment(self.BASE) == HeaderCommentInfo(
            test_kind="Full optimization",
            subject="TC24051903.ex5",
            symbol="JP225",
            period="H8",
            model_word="real ticks",
            period_word="entire history",
            with_forward=False,
        )

    def test_a_period_word_containing_a_date_range_is_kept_whole(self):
        info = parse_header_comment(
            ";Genetic optimization: a.ex5, JP225 D1, m1 ohlc, 2012.01.01 - 2012.12.31"
        )
        assert info.period_word == "2012.01.01 - 2012.12.31"


class TestSymbolAndPeriodSplit:
    """`Symbol Period` の分離（末尾の空白 1 個で分ける＝`rsplit`）。"""

    def test_a_symbol_containing_a_space_keeps_the_space(self):
        info = parse_header_comment(
            ";Expert Advisor visual test: a.ex5, JP225 Cash H1, every tick, last year"
        )
        assert (info.symbol, info.period) == ("JP225 Cash", "H1")

    def test_a_symbol_period_without_a_space_is_unparsable(self):
        assert parse_header_comment(";Full optimization: a.ex5, JP225H1, every tick, last year") is None


class TestUnparsableInputsReturnNone:
    """解析不能は ``None``（**例外を送出しない**＝内部設計 §4.5.3）。

    正典は `[Tester]` セクションであり、コメントの解析結果は検証補助にすぎない。
    したがって解析できないコメントで読込全体を失敗させてはならない。
    """

    UNPARSABLE = {
        "none": None,
        "empty": "",
        "not_a_comment": "Full optimization: a.ex5, JP225 H8, real ticks, last year",
        "no_kind_separator": ";Full optimization a.ex5 JP225 H8",
        "too_few_fields": ";Full optimization: a.ex5, JP225 H8, real ticks",
        "too_many_fields": ";Full optimization: a.ex5, JP225 H8, real ticks, last year, extra",
        "subject_with_comma_space": ";Full optimization: My, EA.ex5, JP225 H8, real ticks, last year",
        "empty_kind": ";: a.ex5, JP225 H8, real ticks, last year",
        "empty_subject": ";Full optimization: , JP225 H8, real ticks, last year",
        "only_prefix": ";",
        "only_suffix": ";Full optimization: , with forward period",
    }

    @pytest.mark.parametrize("comment", list(UNPARSABLE.values()), ids=list(UNPARSABLE))
    def test_it_returns_none_without_raising(self, comment):
        # Act / Assert（Self-Validating: 例外が出れば test 自体が失敗する）
        assert parse_header_comment(comment) is None

    def test_a_subject_containing_a_comma_space_is_not_split_incorrectly(self):
        # 誤った分解結果を返さない方向へ倒す（項目数が合わないため None）
        assert parse_header_comment(
            ";Full optimization: My, EA.ex5, JP225 H8, real ticks, last year"
        ) is None


class TestParsingIsPure:
    """F.I.R.S.T（Repeatable）: 同じ入力に対して同じ結果を返し、I/O を行わない。"""

    COMMENT = ";Indicator visual test: PRO!fit_Band.ex5, JP225 H8, real ticks, last year"

    def test_repeated_calls_are_equal(self):
        assert parse_header_comment(self.COMMENT) == parse_header_comment(self.COMMENT)

    def test_the_result_is_frozen(self):
        info = parse_header_comment(self.COMMENT)
        with pytest.raises(FrozenInstanceError):
            info.symbol = "OTHER"
