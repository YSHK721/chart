"""`simulator.domain.bar_time.epoch_millis`（RUN_TRACE_BASIC_DESIGN §9.0）。

なぜ加法するか（実測・憶測ではない）:
    trace_points.parquet の time 列は `epoch_seconds()` を通っており、
    _from_numpy_datetime64 が `.astype("datetime64[s]")` で秒未満を切り捨てる。
    実ティック 1 ヶ月 run（JP225 2026-01・`simulator/tests/confirmation/2026-01_ma-market`）
    を実走して測ると、記録 **1,036,394 行のうち 407,745 行（39.3%）が他の行と同じ
    time 値**になり、1 つの秒を最大 14 行が共有していた（2026-09-10 実測）。
    「ティック粒度での推移」という要件に対する欠陥である。

固定する契約:
  1. `epoch_millis` は `EPOCH_CONVERTERS` の**受理集合そのもの**を受ける
     （解釈規則は 1 つのまま・出力単位だけが増える）。
  2. 返すのは epoch ミリ秒の `int` であり、秒未満が保存される。
  3. 未対応の表現は `epoch_seconds` と同じく `ConfigError`。
  4. **`epoch_seconds` の挙動は 1 bit も変わらない**（同一入力で同一出力・同一例外）。
  5. 受理集合の各エントリがミリ秒変換を持つ（表と表の取り落としを機械で赤にする）。
"""
from __future__ import annotations

from datetime import datetime, timezone

import numpy as np
import pytest

from simulator.domain.bar_time import EPOCH_CONVERTERS, epoch_millis, epoch_seconds
from simulator.domain.exceptions import ConfigError

_EPOCH = 1_704_067_200  # 2024-01-01T00:00:00Z
#: 2026-01-29T01:00:57Z（confirmation tick-store の実ティックが集まる秒）。
_SECOND_2026 = 1_769_648_457


class TestEpochMillisPreservesSubSecond:
    """秒未満が保存される（これが是正の目的そのもの）。"""

    def test_numpy_datetime64_with_milliseconds_keeps_them(self):
        # Arrange: 実ティック store の実型（RealTickModel.ticks_of が
        # `pandas.Timestamp.to_datetime64()` で作る numpy.datetime64）。
        value = np.datetime64("2024-01-01T00:00:00.729")

        # Act
        got = epoch_millis(value)

        # Assert
        assert got == _EPOCH * 1000 + 729

    def test_two_ticks_in_the_same_second_do_not_collapse(self):
        """秒精度なら同値になる 2 点が、ミリ秒では別の値になる。"""
        # Arrange
        first = np.datetime64("2026-01-29T01:00:57.729")
        second = np.datetime64("2026-01-29T01:00:57.865")

        # Act / Assert: 秒では潰れる（現行の欠陥）が、ミリ秒では潰れない。
        # 期待値はリテラル（両辺を被検査関数にすると「秒が壊れても一致する」形になる）。
        assert epoch_seconds(first) == _SECOND_2026
        assert epoch_seconds(second) == _SECOND_2026
        assert epoch_millis(first) == _SECOND_2026 * 1000 + 729
        assert epoch_millis(second) == _SECOND_2026 * 1000 + 865

    def test_microsecond_resolution_input_truncates_to_milliseconds(self):
        """`datetime64[us]`（confirmation tick-store の実 dtype）はミリ秒へ切り捨てる。"""
        # Arrange
        value = np.datetime64("2024-01-01T00:00:00.123456")

        # Act / Assert: 単位はミリ秒であり、それ以下は表現しない（切り上げない）。
        assert epoch_millis(value) == _EPOCH * 1000 + 123


class TestEpochMillisAcceptsTheSameSet:
    """解釈規則（受理集合）は 1 つのままで、出力単位だけが増える。"""

    def test_epoch_integer_is_seconds_and_is_scaled(self):
        # comma 形式 CSV 由来の bar.time は epoch **秒**の整数である。
        assert epoch_millis(_EPOCH) == _EPOCH * 1000

    def test_numpy_int64_is_accepted(self):
        assert epoch_millis(np.int64(_EPOCH)) == _EPOCH * 1000

    def test_bool_is_not_a_time_representation(self):
        with pytest.raises(ConfigError):
            epoch_millis(True)

    def test_aware_datetime_uses_its_own_offset(self):
        assert epoch_millis(datetime(2024, 1, 1, tzinfo=timezone.utc)) == _EPOCH * 1000

    def test_naive_datetime_is_interpreted_as_utc(self):
        assert epoch_millis(datetime(2024, 1, 1)) == _EPOCH * 1000

    def test_datetime_microseconds_are_kept_as_milliseconds(self):
        value = datetime(2024, 1, 1, 0, 0, 0, 729_000, tzinfo=timezone.utc)
        assert epoch_millis(value) == _EPOCH * 1000 + 729

    def test_unsupported_representation_raises_config_error(self):
        with pytest.raises(ConfigError):
            epoch_millis("2024-01-01T00:00:00")

    def test_the_result_is_a_plain_int(self):
        # parquet の int64 列・ブラウザの `Date` が受けるのは素の整数である。
        assert type(epoch_millis(np.datetime64("2024-01-01T00:00:00.729"))) is int


class TestSecondsIsUnchanged:
    """`epoch_seconds` の挙動を 1 bit も変えない（加法であることの表明）。"""

    @pytest.mark.parametrize(
        "value, expected",
        [
            (_EPOCH, _EPOCH),
            (np.int64(_EPOCH), _EPOCH),
            (datetime(2024, 1, 1, tzinfo=timezone.utc), _EPOCH),
            (datetime(2024, 1, 1), _EPOCH),
            (np.datetime64("2024-01-01T00:00:00"), _EPOCH),
            (np.datetime64("2024-01-01T00:00:00.500"), _EPOCH),
            (np.datetime64("2024-01-01T00:00:00.999"), _EPOCH),
        ],
    )
    def test_seconds_still_truncate(self, value, expected):
        assert epoch_seconds(value) == expected

    @pytest.mark.parametrize("value", [True, "2024-01-01T00:00:00", None, 1.5])
    def test_seconds_still_reject_the_same_inputs(self, value):
        with pytest.raises(ConfigError):
            epoch_seconds(value)

    @pytest.mark.parametrize(
        "value, expected_second",
        [
            (_EPOCH, _EPOCH),
            (np.int64(_EPOCH), _EPOCH),
            (datetime(2024, 1, 1, tzinfo=timezone.utc), _EPOCH),
            (np.datetime64("2024-01-01T00:00:00.729"), _EPOCH),
            (np.datetime64("2026-01-29T01:00:57.729"), _SECOND_2026),
        ],
    )
    def test_the_two_units_agree_on_the_second(self, value, expected_second):
        """ミリ秒を秒へ落とすと秒版と一致する（ColumnarRunTrace が窓判定に使う関係）。

        この関係が成り立たなければ「窓が通した点の time 列が窓の外」が起こる
        （§6.5.0 が禁じている食い違い）。

        期待値は**リテラルの秒**で置く。両辺を被検査関数にすると「両方が同じだけ
        ずれた」退行を通してしまう（一致はするが値が誤る）。
        """
        assert epoch_seconds(value) == expected_second
        assert epoch_millis(value) // 1000 == expected_second


class TestTheTwoUnitsCoverTheSameTable:
    """受理集合の各エントリがミリ秒変換を持つ（取り落としを機械で赤にする）。

    2 つの表を手で同期させる形にすると、表現を 1 つ足したときにミリ秒側だけ
    取り残される。エントリ数を手書きせず、`EPOCH_CONVERTERS` の判定関数を
    そのまま回して実証する（宣言からの導出・名前の一覧をテストへ書かない）。
    """

    #: 各判定関数が真になる代表値と、その**リテラルの**epoch 秒。
    #: 表そのものではなく**入力**の供給であり、期待値を被検査関数から作らない。
    _SAMPLES = (
        (_EPOCH, _EPOCH),
        (np.datetime64("2024-01-01T00:00:00.729"), _EPOCH),
        (datetime(2024, 1, 1, tzinfo=timezone.utc), _EPOCH),
    )

    def test_every_accepted_representation_has_a_millis_conversion(self):
        # Arrange: 表の各エントリについて、その判定が真になる標本を 1 つ選ぶ。
        covered = 0
        for matches, _convert, _tag in EPOCH_CONVERTERS:
            pair = next((p for p in self._SAMPLES if matches(p[0])), None)
            assert pair is not None, (
                f"判定 {matches.__name__} に対応する標本がテスト側に無い"
                "（表現が増えたら標本も足す）"
            )
            sample, expected_second = pair
            # Act / Assert: ミリ秒側が例外を出さず、リテラルの秒と秒位で一致する。
            assert epoch_millis(sample) // 1000 == expected_second
            covered += 1
        # 正の対照: 表が空なら上のループは恒真になる。
        assert covered == len(EPOCH_CONVERTERS) >= 3
