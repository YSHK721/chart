"""IS/OOS・ウォークフォワード CLI の時刻正規化が時刻表現に依存しないこと（ISSUE-412 (B)/(D)）。

固定する事実:
    N-1: `bar.time` が ``numpy.int64``（comma 形式 CSV を pandas が読むと**必ず**この型に
         なる）のとき、CLI が作る境界・差分は **epoch int** でなければならない。
         是正前は ``isinstance(np.int64(1), int)`` が **False**（実測・numpy 2.4.6）で
         あるため int 分岐に入らず、`run_is_oos_cli.normalize_time` は
         ``numpy.datetime64`` を、`walk_forward_cli._normalize_span` は
         ``numpy.timedelta64`` を返していた。
    N-2: 境界と差分は**対で使われる**。`usecase/walk_forward.py:157-163` が
         ``split = global_start + is_span`` を組み、`usecase/run_is_oos.py:35` が
         ``bar.time < split`` を評価する。したがって「正規化した境界 + 正規化した差分」が
         `bar.time` と比較可能でなければならない。是正前の ``numpy.int64`` 系では
         ``numpy.datetime64 < numpy.int64`` が **UFuncTypeError** を送出する（実測）。
    N-3: 判定・変換の規則は `simulator.domain.bar_time` が唯一持つ。CLI 側は
         `is_epoch_integer` / `epoch_seconds` を**同一オブジェクトとして読む**（写さない）。

なぜ二重表現そのものは残すか（設計確定事項・推測しない）:
    「span を int 秒へ一本化する」案は棄却済みである。engine 側に `bar.time` との生比較が
    複数あり、`bar.time` が ``numpy.datetime64`` の経路（MT5 タブ形式ローダ）では境界も
    ``numpy.datetime64`` でなければ比較できない。よって表現によるディスパッチは維持し、
    **判定述語だけ**を domain の単一ソースへ委譲する。

測り方: 2 つの正規化関数を直接叩く（実経路での到達は
`simulator/tests/integration/test_walk_forward_integration.py` /
`test_is_oos_stop_probe.py` が担う）。流儀は
`simulator/tests/unit/test_bar_period_time_representations.py` に準拠する。
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from simulator.domain import bar_time
from simulator.tools import run_is_oos_cli, walk_forward_cli
from simulator.tools.run_is_oos_cli import normalize_time
from simulator.tools.walk_forward_cli import _normalize_span

#: 2026-04-01T00:00:00Z。
_SPLIT_TEXT = "2026-04-01"
_SPLIT_EPOCH = 1_775_001_600
#: 10 日 = 864000 秒。
_SPAN_TEXT = "10D"
_SPAN_SECONDS = 864_000


class TestSpanNormalizationIsIndependentOfIntegerKind:
    """N-1（差分側）: 整数の「種類」で span の表現が割れない。"""

    def test_numpy_int64_bars_yield_integer_seconds(self):
        # Arrange: comma 形式 CSV → pandas が返す実型。
        sample = np.int64(0)
        assert isinstance(sample, int) is False  # 前提の実測（numpy 2.4.6）
        # Act
        span = _normalize_span(_SPAN_TEXT, sample)
        # Assert: numpy.timedelta64 ではなく int 秒。
        assert span == _SPAN_SECONDS
        assert type(span) is int

    def test_python_int_bars_yield_the_same_span_as_numpy_int64_bars(self):
        assert _normalize_span(_SPAN_TEXT, np.int64(0)) == _normalize_span(_SPAN_TEXT, 0)

    def test_datetime64_bars_still_yield_a_timedelta64(self):
        """既存挙動の保全: ``numpy.datetime64`` 系の境界には timedelta64 が要る。"""
        span = _normalize_span(_SPAN_TEXT, np.datetime64("2026-04-01T00:00:00"))
        assert span == pd.Timedelta(_SPAN_TEXT).to_timedelta64()

    def test_bool_is_not_an_epoch_integer_sample(self):
        """`isinstance(True, int)` は真だが時刻ではない（int 分岐へ入れない）。"""
        assert _normalize_span(_SPAN_TEXT, True) == pd.Timedelta(_SPAN_TEXT).to_timedelta64()


class TestBoundaryNormalizationIsIndependentOfIntegerKind:
    """N-1（境界側）: 整数の「種類」で境界の表現が割れない。"""

    def test_numpy_int64_bars_yield_epoch_seconds(self):
        # Arrange
        sample = np.int64(0)
        # Act
        split = normalize_time(_SPLIT_TEXT, sample)
        # Assert: numpy.datetime64 ではなく epoch 秒 int。
        assert split == _SPLIT_EPOCH
        assert type(split) is int

    def test_python_int_bars_yield_the_same_boundary_as_numpy_int64_bars(self):
        assert normalize_time(_SPLIT_TEXT, np.int64(0)) == normalize_time(_SPLIT_TEXT, 0)

    def test_datetime64_bars_still_yield_a_datetime64(self):
        """既存挙動の保全: ``numpy.datetime64`` 系の bar.time には datetime64 の境界。"""
        split = normalize_time(_SPLIT_TEXT, np.datetime64("2026-04-01T00:00:00"))
        assert split == pd.Timestamp(_SPLIT_TEXT).to_datetime64()

    def test_bool_is_not_an_epoch_integer_sample(self):
        split = normalize_time(_SPLIT_TEXT, True)
        assert split == pd.Timestamp(_SPLIT_TEXT).to_datetime64()


class TestBoundaryAndSpanAreUsedAsAPair:
    """N-2: 「境界 + 差分」が `bar.time` と比較可能である（両者は対で使われる）。

    `usecase/walk_forward.py:157-163` が ``split = global_start + is_span`` を組み、
    `usecase/run_is_oos.py:35` が ``bar.time < split`` を評価する。境界と差分の一方だけを
    直しても、`bar.time` との比較が成立しなければ意味がない。
    """

    @pytest.mark.parametrize(
        "sample_bar_time",
        [np.int64(_SPLIT_EPOCH), int(_SPLIT_EPOCH)],
        ids=["np.int64", "int"],
    )
    def test_epoch_integer_bars_can_be_compared_with_the_derived_boundary(
        self, sample_bar_time
    ):
        # Arrange / Act: CLI が作る 2 値から窓の右端を組む。
        start = normalize_time(_SPLIT_TEXT, sample_bar_time)
        span = _normalize_span(_SPAN_TEXT, sample_bar_time)
        # Assert: 例外なく比較でき、値も epoch 秒として正しい。
        assert bool(sample_bar_time < start + span) is True
        assert start + span == _SPLIT_EPOCH + _SPAN_SECONDS

    def test_datetime64_bars_can_be_compared_with_the_derived_boundary(self):
        sample_bar_time = np.datetime64("2026-04-01T00:00:00")
        start = normalize_time(_SPLIT_TEXT, sample_bar_time)
        span = _normalize_span(_SPAN_TEXT, sample_bar_time)
        assert bool(sample_bar_time < start + span) is True

    def test_the_two_functions_agree_on_the_representation(self):
        """境界と差分が同じ側へ倒れる（片方だけ直すと成立しない性質）。"""
        for sample in (np.int64(0), 0):
            assert type(normalize_time(_SPLIT_TEXT, sample)) is int
            assert type(_normalize_span(_SPAN_TEXT, sample)) is int
        for sample in (np.datetime64("2026-04-01T00:00:00"),):
            assert isinstance(normalize_time(_SPLIT_TEXT, sample), np.datetime64)
            assert isinstance(_normalize_span(_SPAN_TEXT, sample), np.timedelta64)


class TestEpochRuleIsUnchanged:
    """境界の epoch 化が旧実装 ``int(pd.Timestamp(value).timestamp())`` と同値である。

    pandas の naive `Timestamp.timestamp()` は **UTC 基準**であり（実測）、
    `bar_time.epoch_seconds` の datetime 変換（`datawindow.half_open` が唯一所有）も
    naive を UTC とみなす。よって委譲で値は動かない（ISSUE-411 実測記録）。
    プロセスのローカル TZ にも依存しない。
    """

    @pytest.mark.parametrize(
        "text",
        ["2026-04-01", "2026-04-01 12:34:56", "2025-01-10", "1970-01-01"],
    )
    def test_boundary_matches_the_previous_timestamp_rule(self, text):
        assert normalize_time(text, np.int64(0)) == int(pd.Timestamp(text).timestamp())


class TestSingleSourceOfTheRule:
    """N-3: 判定・変換の実体が domain と同一オブジェクトである（写しが入ると落ちる）。

    先例の流儀: `simulator/tests/unit/test_tick_window_single_source.py`
    `test_tick_stage_reads_the_shared_normalizer`。
    """

    def test_walk_forward_cli_reads_the_shared_predicate(self):
        assert walk_forward_cli.is_epoch_integer is bar_time.is_epoch_integer

    def test_run_is_oos_cli_reads_the_shared_predicate(self):
        assert run_is_oos_cli.is_epoch_integer is bar_time.is_epoch_integer

    def test_run_is_oos_cli_reads_the_shared_normalizer(self):
        assert run_is_oos_cli.epoch_seconds is bar_time.epoch_seconds

    def test_the_predicate_is_the_integer_entry_of_the_converters_table(self):
        """CLI が読む述語は受理集合の表そのものから来ている（鎖の途中に複製がない）。"""
        integer_entries = [
            matches for matches, _c, tag in bar_time.EPOCH_CONVERTERS
            if tag == bar_time.BAR and matches(1)
        ]
        assert integer_entries == [walk_forward_cli.is_epoch_integer]
