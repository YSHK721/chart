"""corpus の構造的事実の固定（T-09・内部設計 §9.2・基本設計 §2.2.4 F-1〜F-20）。

基本設計の規則（規則 D / E / F / G / B・§4.5.5）は「corpus でこう観測された」を
根拠に定めた。本モジュールはその**根拠そのもの**を corpus 直読で再測し、規則の
前提が崩れていないことを固定する。

読み方は `[Tester]` の直読（`corpus_tester_keys` / `corpus_tester_entries`）であり
実装の parse を通さない。実装で読んだ値を実装の期待値と比べても、実装の誤りは
両辺に等しく乗って検出できないためである。

員数リテラルを置かない方針:
    `[Tester]` の許容キー集合は実装の単一ソース `ini_codec.TESTER_KEYS`（＝
    `STANDARD_KEY_ORDER` から導出）と**集合として**突合する。「18 個」を数値で
    書き写すと、実装の順序表と本テストの二重管理になり必ず片方が腐る。
    Expert 専用 8 キーも同様に `validation.EXPERT_ONLY_KEYS` を参照する。
    ファイル件数 44 だけは実装から導けない corpus 側の事実（基本設計 §2.2.3）で
    あるため `corpus_cases.CORPUS_FILE_COUNT` の 1 箇所に置く。
"""
from __future__ import annotations

from simulator.adapter.tester_settings.ini_codec import TESTER_KEYS
from simulator.framework.tester_settings.validation import EXPERT_ONLY_KEYS
from simulator.tests.regression.corpus_cases import (
    CORPUS_FILE_COUNT,
    CORPUS_FILES,
    corpus_case,
    corpus_tester_entries,
    corpus_tester_keys,
    requires_corpus,
)
from simulator.usecase.tester_settings.enums import ForwardMode, OptimizationMode

#: 対象種別を表すキー（F-1 の排他対象）。
SUBJECT_KEYS: frozenset[str] = frozenset({"Expert", "Indicator"})

#: 期間指定の 2 形式（F-2 の排他対象）。
PRESET_DATE_KEY: str = "Dates"
CUSTOM_DATE_KEYS: frozenset[str] = frozenset({"FromDate", "ToDate"})

#: `Visual` キーが欠落する `Optimization` 値（F-11）。
OPTIMIZATION_WITHOUT_VISUAL: frozenset[OptimizationMode] = frozenset(
    {OptimizationMode.FULL_SLOW_COMPLETE, OptimizationMode.GENETIC}
)

#: forward 有効を表す `ForwardMode` 値（F-9・F-16 の第 3 桁）。
FORWARD_ENABLED_MODES: frozenset[ForwardMode] = frozenset(
    {ForwardMode.PRESET_SPLIT, ForwardMode.CUSTOM_DATE}
)

#: ファイル名の区切り（F-16 の末尾数字は最後から 2 番目のフィールド）。
FILENAME_FIELD_SEPARATOR: str = "."


def _ini_value(value: int) -> str:
    """列挙値を `.ini` の生トークン表記（10 進文字列）へ直す。"""
    return str(int(value))


def _filename_tail_digits(name: str) -> str:
    """`<...>.<数字>.ini` の数字フィールドを取り出す（F-16）。"""
    return name.rsplit(FILENAME_FIELD_SEPARATOR, 2)[-2]


@requires_corpus
class TestCorpusInventory:
    """corpus 自体の員数（不在環境で沈黙のまま緑にならないための番兵でもある）。"""

    def test_the_corpus_holds_forty_four_ini_files(self):
        # Arrange / Act
        names = [path.name for path in CORPUS_FILES]

        # Assert
        assert len(names) == CORPUS_FILE_COUNT, (
            f"corpus 件数が {len(names)} 件（期待 {CORPUS_FILE_COUNT} 件・基本設計 §2.2.3）。"
            " 0 件なら corpus 不在のまま必須化フラグで実行している"
        )
        assert len(set(names)) == len(names), "corpus にファイル名の重複がある"

    def test_the_union_of_tester_keys_equals_the_implementation_key_set(self):
        # Arrange / Act
        observed: set[str] = set()
        for path in CORPUS_FILES:
            observed.update(corpus_tester_keys(path))

        # Assert（員数を書き写さず実装の単一ソースと集合等価で突合する）
        assert observed == set(TESTER_KEYS), (
            "corpus の `[Tester]` キー和集合が実装の許容キー集合と一致しない。"
            f" corpus のみ: {sorted(observed - set(TESTER_KEYS))} /"
            f" 実装のみ: {sorted(set(TESTER_KEYS) - observed)}"
        )


@requires_corpus
class TestTesterSectionHasNoDuplicateKeys:
    """`[Tester]` 内のキー重複は 0 件（規則 R5 の前提）。"""

    @corpus_case
    def test_each_key_appears_at_most_once(self, corpus_path):
        # Arrange / Act
        keys = corpus_tester_keys(corpus_path)

        # Assert
        duplicated = sorted({key for key in keys if keys.count(key) > 1})
        assert duplicated == [], f"{corpus_path.name}: `[Tester]` にキー重複がある: {duplicated}"


@requires_corpus
class TestStructuralFacts:
    """F-1 / F-2 / F-10 / F-11 / F-12 / F-16 を corpus 全件で再測する。"""

    @corpus_case
    def test_f1_expert_and_indicator_are_mutually_exclusive(self, corpus_path):
        # Arrange / Act
        keys = set(corpus_tester_keys(corpus_path))

        # Assert
        present = keys & SUBJECT_KEYS
        assert len(present) == 1, (
            f"{corpus_path.name}: F-1 違反。Expert / Indicator は片方のみ必須（実測 {sorted(present)}）"
        )

    @corpus_case
    def test_f2_preset_dates_and_custom_dates_are_mutually_exclusive(self, corpus_path):
        # Arrange / Act
        keys = set(corpus_tester_keys(corpus_path))

        # Assert
        has_preset = PRESET_DATE_KEY in keys
        custom = keys & CUSTOM_DATE_KEYS
        assert has_preset != bool(custom), (
            f"{corpus_path.name}: F-2 違反。Dates と FromDate/ToDate は排他"
            f"（Dates={has_preset} / custom={sorted(custom)}）"
        )
        if custom:
            assert custom == CUSTOM_DATE_KEYS, (
                f"{corpus_path.name}: F-2 違反。FromDate / ToDate は対で出現する（実測 {sorted(custom)}）"
            )

    @corpus_case
    def test_f10_forward_mode_four_is_equivalent_to_having_a_forward_date(self, corpus_path):
        # Arrange / Act
        entries = corpus_tester_entries(corpus_path)

        # Assert
        is_custom_date = entries.get("ForwardMode") == _ini_value(ForwardMode.CUSTOM_DATE)
        has_forward_date = "ForwardDate" in entries
        assert is_custom_date == has_forward_date, (
            f"{corpus_path.name}: F-10 違反。ForwardMode=4 ⇔ ForwardDate 併記"
            f"（ForwardMode={entries.get('ForwardMode')!r} / ForwardDate={entries.get('ForwardDate')!r}）"
        )

    @corpus_case
    def test_f11_a_missing_visual_key_is_equivalent_to_an_active_optimization(self, corpus_path):
        # Arrange / Act
        entries = corpus_tester_entries(corpus_path)

        # Assert
        optimization = entries.get("Optimization")
        optimizing = optimization is not None and optimization in {
            _ini_value(mode) for mode in OPTIMIZATION_WITHOUT_VISUAL
        }
        assert ("Visual" not in entries) == optimizing, (
            f"{corpus_path.name}: F-11 違反。Visual 欠落 ⇔ Optimization∈{{1,2}}"
            f"（Optimization={optimization!r} / Visual={entries.get('Visual')!r}）"
        )

    @corpus_case
    def test_f12_indicator_files_carry_no_expert_only_keys(self, corpus_path):
        # Arrange / Act
        keys = set(corpus_tester_keys(corpus_path))

        # Assert
        if "Indicator" not in keys:
            assert set(EXPERT_ONLY_KEYS) <= keys, (
                f"{corpus_path.name}: F-12 の対偶違反。Expert テストは専用 8 キーをすべて持つ"
                f"（欠落 {sorted(set(EXPERT_ONLY_KEYS) - keys)}）"
            )
            return
        intruders = keys & set(EXPERT_ONLY_KEYS)
        assert intruders == set(), (
            f"{corpus_path.name}: F-12 違反。Indicator テストが Expert 専用キーを持つ: {sorted(intruders)}"
        )

    @corpus_case
    def test_f16_the_filename_tail_digits_encode_the_tester_values(self, corpus_path):
        # Arrange
        entries = corpus_tester_entries(corpus_path)

        # Act
        tail = _filename_tail_digits(corpus_path.name)

        # Assert
        if "Expert" in entries:
            forward_flag = "1" if entries.get("ForwardMode") in {
                _ini_value(mode) for mode in FORWARD_ENABLED_MODES
            } else "0"
            expected = f"{entries['Model']}{entries['Optimization']}{forward_flag}"
        else:
            expected = entries["Model"]
        assert tail == expected, (
            f"{corpus_path.name}: F-16 違反。ファイル名末尾 {tail!r} が期待 {expected!r} と一致しない"
        )
