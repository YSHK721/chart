"""設定ロードの性能予算（T-08・内部設計 §9.2 / §7.1・基本設計 NFR-04）。

予算（基本設計 §7.1 NFR-04）:
    - 1 ファイルのロード＋検証 ≤ 10 ms（`time.perf_counter` の 100 回試行中央値）
    - 44 件一括 ≤ 500 ms（10 回試行の中央値）
    - `tracemalloc` のピーク ≤ 1 MB

計測の作法:
    - 中央値で判定する（平均は外れ値に引きずられる。GC・OS スケジューリングの
      スパイクを評価対象にしない）。
    - 計測前にウォームアップを 1 回行う。正規表現のコンパイルはモジュール読込時に
      1 回だけ（§7.1 の設計前提）であり、その初回コストは 1 ファイルあたりの予算に
      含めない設計であるため。
    - 1 ファイル計測の対象は corpus 中で**最大バイト数**のファイルを選ぶ。最良では
      なく最悪に近い側で予算を測る（速いファイルで通しても保証にならない）。

⚠️ 未達時の扱い（内部設計 §7.1）: 予算を緩めない。実測値を報告し、原因特定は
計測に基づくホットスポット特定として後続工程で行う（推測で最適化しない）。
"""
from __future__ import annotations

import statistics
import time
import tracemalloc

from simulator.framework.tester_settings.loader import load_tester_settings
from simulator.tests.regression.corpus_cases import (
    CORPUS_FILE_COUNT,
    CORPUS_FILES,
    requires_corpus,
)

#: 1 ファイルのロード＋検証の予算（ms・NFR-04）。
SINGLE_FILE_BUDGET_MS: float = 10.0
#: 44 件一括の予算（ms・NFR-04）。
BATCH_BUDGET_MS: float = 500.0
#: `tracemalloc` ピークの予算（bytes・内部設計 §9.2 T-08）。
PEAK_MEMORY_BUDGET_BYTES: int = 1 << 20

#: 1 ファイル計測の試行回数（内部設計 §9.2 T-08）。
SINGLE_FILE_TRIALS: int = 100
#: 一括計測の試行回数（内部設計 §9.2 T-08）。
BATCH_TRIALS: int = 10


def _largest_corpus_file():
    """最大バイト数の corpus ファイル（最悪側で予算を測る）。"""
    return max(CORPUS_FILES, key=lambda path: path.stat().st_size)


def _elapsed_ms(action) -> float:
    """`action()` の実行時間（ms）。"""
    started = time.perf_counter()
    action()
    return (time.perf_counter() - started) * 1000.0


@requires_corpus
class TestLoadPerformanceMeetsTheBudget:
    """NFR-04 の 3 予算を実測で固定する。"""

    def test_single_file_load_median_is_within_ten_milliseconds(self):
        # Arrange
        path = _largest_corpus_file()
        load_tester_settings(path)  # ウォームアップ（正規表現コンパイル等の初回コストを除く）

        # Act
        samples = [
            _elapsed_ms(lambda: load_tester_settings(path)) for _ in range(SINGLE_FILE_TRIALS)
        ]
        median_ms = statistics.median(samples)

        # Assert
        assert median_ms <= SINGLE_FILE_BUDGET_MS, (
            f"NFR-04 未達: {path.name} のロード中央値 {median_ms:.4f} ms > {SINGLE_FILE_BUDGET_MS} ms"
            f"（最大 {max(samples):.4f} ms / 最小 {min(samples):.4f} ms / n={SINGLE_FILE_TRIALS}）"
        )

    def test_batch_load_of_the_whole_corpus_is_within_five_hundred_milliseconds(self):
        # Arrange
        paths = list(CORPUS_FILES)
        # 予算は 44 件一括に対して定義されている。件数が欠けた計測は予算の検定にならない
        # （0 件なら常に 0 ms で通ってしまう＝空振りの緑を作らない）。
        assert len(paths) == CORPUS_FILE_COUNT, (
            f"一括計測の対象が {len(paths)} 件（期待 {CORPUS_FILE_COUNT} 件）。予算 {BATCH_BUDGET_MS} ms は"
            f" {CORPUS_FILE_COUNT} 件一括に対する値である"
        )
        for path in paths:  # ウォームアップ
            load_tester_settings(path)

        def load_all() -> None:
            for target in paths:
                load_tester_settings(target)

        # Act
        samples = [_elapsed_ms(load_all) for _ in range(BATCH_TRIALS)]
        median_ms = statistics.median(samples)

        # Assert
        assert median_ms <= BATCH_BUDGET_MS, (
            f"NFR-04 未達: {len(paths)} 件一括の中央値 {median_ms:.3f} ms > {BATCH_BUDGET_MS} ms"
            f"（最大 {max(samples):.3f} ms / n={BATCH_TRIALS}）"
        )

    def test_peak_memory_of_a_single_load_is_within_one_megabyte(self):
        # Arrange
        path = _largest_corpus_file()
        load_tester_settings(path)  # ウォームアップ（import 時の確保を計測へ混ぜない）

        # Act
        tracemalloc.start()
        try:
            load_tester_settings(path)
            _, peak_bytes = tracemalloc.get_traced_memory()
        finally:
            tracemalloc.stop()

        # Assert
        assert peak_bytes <= PEAK_MEMORY_BUDGET_BYTES, (
            f"NFR-04 未達: {path.name} のロード時ピーク {peak_bytes} bytes"
            f" > {PEAK_MEMORY_BUDGET_BYTES} bytes"
        )
