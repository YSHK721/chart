"""1 行目コメントと `[Tester]` 値の突合（T-05・内部設計 §9.2・基本設計 F-3〜F-9 / F-18）。

MT5 は `.ini` の 1 行目に人間可読の要約コメントを書く。コメントは**正典ではない**
（正典は `[Tester]`）が、両者が食い違えば「どちらかの読みが誤っている」ことの
検出器になる。本モジュールはその突合を corpus 44 件で固定する。

期待値の出所:
    - 語彙表（Model 語 / 期間語 / テスト種別語）は基本設計 §2.2.4 の F-3〜F-8・F-18。
      `header_comment.py` は「語彙の対応表は本モジュールに持たない。突合はテスト側
      （T-05）の責務」と宣言しており、表の所在は本モジュール 1 箇所である。
    - `[Tester]` 側の値は `corpus_tester_entries`（**実装の parse を通さない直読**）。
      実装で読んだ値どうしの比較にすると、実装の誤りが両辺に等しく乗って検出できない。

合否基準（内部設計 §9.2 T-05・ISSUE-390 の対策案）:
    不一致は既知の 1 件のみ。`TC24051903_24052301.JP225_ver24051601.H8.20120101_20121231.121.ini`
    は `ForwardMode=4` でありながらコメント末尾語 `, with forward period` を持たない
    （同ファイルは `ForwardDate=1970.01.01` という退化値を持つ唯一のファイルでもある）。
    それ以外の不一致が 1 件でも出れば失敗する。既知 1 件は**ファイル名で固定**し、
    集合等価で判定するため「既知の 1 件が解消した」場合にも落ちて気付ける。

⚠️ ISSUE-390 は本テスト作成時点で OPEN である（基本設計 F-9 の記述「forward 有効
14 件すべてで成立」は実測 13/14 と食い違う）。本モジュールは ISSUE-390 の対策案
（および内部設計 §9.2 T-05 の合否基準）に一致する形で固定するが、基本設計 F-9 本文の
訂正は本フェーズの範囲外である。
"""
from __future__ import annotations

from simulator.adapter.tester_settings.header_comment import parse_header_comment
from simulator.framework.tester_settings.loader import load_tester_settings
from simulator.tests.regression.corpus_cases import (
    CORPUS_FILES,
    corpus_case,
    corpus_first_line,
    corpus_tester_entries,
    requires_corpus,
)
from simulator.usecase.tester_settings.enums import (
    DatesPreset,
    ForwardMode,
    OptimizationMode,
    SubjectKind,
    TickModel,
)

#: `Model` 値 → コメントの Model 語（基本設計 F-3 / F-4 / F-5 / F-6）。
#: `MATH_CALCULATIONS`（=3）は corpus 未出現のため語が実測されておらず、表に載せない
#: （実測のない語を発明しない）。
MODEL_WORD_BY_TICK_MODEL: dict[TickModel, str] = {
    TickModel.EVERY_TICK: "every tick",
    TickModel.ONE_MINUTE_OHLC: "m1 ohlc",
    TickModel.OPEN_PRICES_ONLY: "open prices",
    TickModel.REAL_TICKS: "real ticks",
}

#: `Dates` 値 → コメントの期間語（基本設計 F-7）。
PERIOD_WORD_BY_DATES_PRESET: dict[DatesPreset, str] = {
    DatesPreset.ENTIRE_HISTORY: "entire history",
    DatesPreset.LAST_YEAR: "last year",
}

#: `Optimization` 値 → コメントのテスト種別語（基本設計 F-8）。
TEST_KIND_BY_OPTIMIZATION: dict[OptimizationMode, str] = {
    OptimizationMode.FULL_SLOW_COMPLETE: "Full optimization",
    OptimizationMode.GENETIC: "Genetic optimization",
}

#: 最適化なしのときのテスト種別語（基本設計 F-18 の実測 4 種のうち残り 2 種）。
VISUAL_TEST_KIND_BY_SUBJECT: dict[SubjectKind, str] = {
    SubjectKind.EXPERT: "Expert Advisor visual test",
    SubjectKind.INDICATOR: "Indicator visual test",
}

#: `FromDate` / `ToDate` 形式のときの期間語の区切り（F-18 の実測書式）。
CUSTOM_PERIOD_WORD_SEPARATOR: str = " - "

#: forward 有効を表す `ForwardMode` の値（基本設計 F-9 / F-10）。
FORWARD_ENABLED_MODES: frozenset[ForwardMode] = frozenset(
    {ForwardMode.PRESET_SPLIT, ForwardMode.CUSTOM_DATE}
)

#: 既知の不一致 1 件（ISSUE-390）。ファイル名で固定する。
KNOWN_FORWARD_WORD_MISMATCH: str = (
    "TC24051903_24052301.JP225_ver24051601.H8.20120101_20121231.121.ini"
)

#: 既知不一致ファイルが同居して持つ `ForwardDate` の退化値（ISSUE-390 事実 3・F-17）。
DEGENERATE_FORWARD_DATE: str = "1970.01.01"


def _ini_value(value: int) -> str:
    """列挙値を `.ini` の生トークン表記（10 進文字列）へ直す。"""
    return str(int(value))


def _expected_test_kind(entries: dict[str, str]) -> str:
    """`[Tester]` の直読値から期待されるテスト種別語を導く（F-8・F-18）。"""
    optimization = entries.get("Optimization")
    if optimization is not None and optimization != _ini_value(OptimizationMode.DISABLED):
        for mode, word in TEST_KIND_BY_OPTIMIZATION.items():
            if optimization == _ini_value(mode):
                return word
        raise AssertionError(f"corpus に未知の Optimization={optimization} が出現した")
    subject = SubjectKind.EXPERT if "Expert" in entries else SubjectKind.INDICATOR
    return VISUAL_TEST_KIND_BY_SUBJECT[subject]


def _expected_period_word(entries: dict[str, str]) -> str:
    """`[Tester]` の直読値から期待される期間語を導く（F-2・F-7・F-18）。"""
    preset = entries.get("Dates")
    if preset is not None:
        for dates, word in PERIOD_WORD_BY_DATES_PRESET.items():
            if preset == _ini_value(dates):
                return word
        raise AssertionError(f"corpus に未知の Dates={preset} が出現した")
    return f"{entries['FromDate']}{CUSTOM_PERIOD_WORD_SEPARATOR}{entries['ToDate']}"


def _forward_enabled(entries: dict[str, str]) -> bool:
    """`[Tester]` の直読値から forward 有効かどうかを導く（F-9・F-10）。"""
    mode = entries.get("ForwardMode")
    return mode is not None and mode in {_ini_value(value) for value in FORWARD_ENABLED_MODES}


@requires_corpus
class TestHeaderCommentIsParsable:
    """44 件すべてが F-18 の書式に一致し、`load` の保持値とも一致する。"""

    @corpus_case
    def test_the_first_line_parses_into_header_comment_info(self, corpus_path):
        # Arrange
        first_line = corpus_first_line(corpus_path)

        # Act
        info = parse_header_comment(first_line)

        # Assert
        assert info is not None, f"{corpus_path.name}: 1 行目が F-18 の書式に一致しない: {first_line!r}"

    @corpus_case
    def test_the_loader_keeps_the_first_line_verbatim(self, corpus_path):
        # Arrange
        first_line = corpus_first_line(corpus_path)

        # Act
        settings = load_tester_settings(corpus_path)

        # Assert
        assert settings.header_comment == first_line, (
            f"{corpus_path.name}: load が 1 行目を原文のまま保持していない"
        )


@requires_corpus
class TestHeaderCommentAgreesWithTesterSection:
    """コメントの各構成要素と `[Tester]` の直読値を突合する（forward 語を除く 5 軸）。"""

    @corpus_case
    def test_the_test_kind_word_matches_subject_and_optimization(self, corpus_path):
        # Arrange
        entries = corpus_tester_entries(corpus_path)
        info = parse_header_comment(corpus_first_line(corpus_path))

        # Act
        expected = _expected_test_kind(entries)

        # Assert
        assert info.test_kind == expected, (
            f"{corpus_path.name}: テスト種別語 {info.test_kind!r} が "
            f"Optimization={entries.get('Optimization')!r} と整合しない"
        )

    @corpus_case
    def test_the_model_word_matches_the_model_value(self, corpus_path):
        # Arrange
        entries = corpus_tester_entries(corpus_path)
        info = parse_header_comment(corpus_first_line(corpus_path))
        expected_by_value = {_ini_value(model): word for model, word in MODEL_WORD_BY_TICK_MODEL.items()}

        # Act
        model = entries["Model"]

        # Assert
        assert model in expected_by_value, f"{corpus_path.name}: corpus に未知の Model={model} が出現した"
        assert info.model_word == expected_by_value[model], (
            f"{corpus_path.name}: Model 語 {info.model_word!r} が Model={model} と整合しない"
        )

    @corpus_case
    def test_the_period_word_matches_the_date_keys(self, corpus_path):
        # Arrange
        entries = corpus_tester_entries(corpus_path)
        info = parse_header_comment(corpus_first_line(corpus_path))

        # Act
        expected = _expected_period_word(entries)

        # Assert
        assert info.period_word == expected, (
            f"{corpus_path.name}: 期間語 {info.period_word!r} が期待 {expected!r} と一致しない"
        )

    @corpus_case
    def test_the_symbol_matches_the_symbol_key(self, corpus_path):
        # Arrange
        entries = corpus_tester_entries(corpus_path)

        # Act
        info = parse_header_comment(corpus_first_line(corpus_path))

        # Assert
        assert info.symbol == entries["Symbol"], (
            f"{corpus_path.name}: コメントの symbol {info.symbol!r} が Symbol={entries['Symbol']!r} と一致しない"
        )

    @corpus_case
    def test_the_period_label_matches_the_period_key(self, corpus_path):
        # Arrange
        entries = corpus_tester_entries(corpus_path)

        # Act
        info = parse_header_comment(corpus_first_line(corpus_path))

        # Assert
        assert info.period == entries["Period"], (
            f"{corpus_path.name}: コメントの period {info.period!r} が Period={entries['Period']!r} と一致しない"
        )


@requires_corpus
class TestForwardWordHasExactlyOneKnownMismatch:
    """forward 語だけは 44/44 一致しない。不一致集合が既知 1 件と厳密一致することを固定する。"""

    def test_the_mismatch_set_is_exactly_the_single_known_file(self):
        # Arrange
        mismatched: set[str] = set()

        # Act
        for path in CORPUS_FILES:
            entries = corpus_tester_entries(path)
            info = parse_header_comment(corpus_first_line(path))
            if info.with_forward != _forward_enabled(entries):
                mismatched.add(path.name)

        # Assert
        assert mismatched == {KNOWN_FORWARD_WORD_MISMATCH}, (
            "forward 語と ForwardMode の不一致は既知 1 件（ISSUE-390）のみであるべき。"
            f" 実測の不一致集合: {sorted(mismatched)}"
        )

    def test_the_known_mismatch_is_forward_mode_four_without_the_trailing_word(self):
        # Arrange
        path = next(p for p in CORPUS_FILES if p.name == KNOWN_FORWARD_WORD_MISMATCH)
        entries = corpus_tester_entries(path)

        # Act
        info = parse_header_comment(corpus_first_line(path))

        # Assert
        assert entries["ForwardMode"] == _ini_value(ForwardMode.CUSTOM_DATE)
        assert entries["ForwardDate"] == DEGENERATE_FORWARD_DATE
        assert info.with_forward is False

    def test_no_other_corpus_file_carries_the_degenerate_forward_date(self):
        # Arrange / Act
        degenerate = {
            path.name
            for path in CORPUS_FILES
            if corpus_tester_entries(path).get("ForwardDate") == DEGENERATE_FORWARD_DATE
        }

        # Assert（ISSUE-390 事実 3: 退化値を持つのは既知不一致の 1 件のみ）
        assert degenerate == {KNOWN_FORWARD_WORD_MISMATCH}
