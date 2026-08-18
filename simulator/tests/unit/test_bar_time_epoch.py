"""`simulator.domain.bar_time`（A-3）: `Bar.time` の epoch 正規化の**単一ソース**。

A-3 の要求: 窓デコレータ（`WindowedMarketDataRepository`）は `bar.time` の比較を要する。
同等の正規化は `simulator/main/tester_settings/window.py` に既にあったため、書き直すと
手書き複製になる。正規化は `Bar.time` の型契約（`domain/bar.py`: numpy.datetime64 または
epoch int）に属するため domain へ移し、`window.py` と本デコレータの双方が同一実体を読む。

本モジュールが固定する契約:
  1. 3 表現（epoch int / datetime / numpy.datetime64）→ UTC 基準 epoch 秒。
  2. naive datetime は UTC とみなす（プロセスのローカル TZ に依存しない・W-3 の原因除去）。
  3. 未対応表現は推測で解釈せず `ConfigError`。
  4. **単一ソース**: `window.py` が公開する `epoch_seconds` / `EPOCH_CONVERTERS` は
     domain の実体と同一オブジェクトである（複製が入り込むと本テストが落ちる）。
"""
from __future__ import annotations

import os
import time
from datetime import datetime, timezone

import numpy as np
import pytest

from simulator.domain.bar_time import EPOCH_CONVERTERS, epoch_seconds
from simulator.domain.exceptions import ConfigError


class TestEpochSeconds:
    def test_epoch_int_passes_through(self):
        assert epoch_seconds(1_704_067_200) == 1_704_067_200

    def test_numpy_int64_is_accepted(self):
        assert epoch_seconds(np.int64(1_704_067_200)) == 1_704_067_200

    def test_bool_is_not_a_time_representation(self):
        with pytest.raises(ConfigError):
            epoch_seconds(True)

    def test_aware_datetime_uses_its_own_offset(self):
        assert epoch_seconds(datetime(2024, 1, 1, tzinfo=timezone.utc)) == 1_704_067_200

    def test_naive_datetime_is_interpreted_as_utc(self):
        # W-3 の原因除去: ローカル TZ で解釈しない。
        assert epoch_seconds(datetime(2024, 1, 1)) == 1_704_067_200

    def test_numpy_datetime64_is_converted(self):
        assert epoch_seconds(np.datetime64("2024-01-01T00:00:00")) == 1_704_067_200

    def test_numpy_datetime64_sub_second_unit_is_truncated_to_seconds(self):
        assert epoch_seconds(np.datetime64("2024-01-01T00:00:00.500")) == 1_704_067_200

    def test_unsupported_representation_raises_config_error(self):
        with pytest.raises(ConfigError):
            epoch_seconds("2024-01-01T00:00:00")


class TestLocalTimezoneIndependence:
    """同一入力はプロセスのローカル TZ に依存せず同一 epoch を返す。"""

    def test_naive_datetime_is_identical_under_any_local_timezone(self):
        saved_tz, saved_tzname = os.environ.get("TZ"), time.tzname
        results = []
        try:
            for tz_name in ("UTC", "Asia/Tokyo"):
                os.environ["TZ"] = tz_name
                time.tzset()
                results.append(epoch_seconds(datetime(2024, 1, 1)))
        finally:
            if saved_tz is None:
                os.environ.pop("TZ", None)
            else:
                os.environ["TZ"] = saved_tz
            time.tzset()
        assert time.tzname == saved_tzname
        assert results[0] == results[1] == 1_704_067_200


class TestSingleSource:
    """`window.py` は domain の実体を読む（手書き複製が入ると落ちる）。"""

    def test_window_module_reuses_the_domain_implementation(self):
        from simulator.main.tester_settings import window as window_module

        assert window_module.epoch_seconds is epoch_seconds
        assert window_module.EPOCH_CONVERTERS is EPOCH_CONVERTERS

    def test_public_reexport_is_the_same_object(self):
        from simulator.main.tester_settings import epoch_seconds as reexported

        assert reexported is epoch_seconds

    def test_converters_table_is_the_extension_point(self):
        # 表現の追加は本表への 1 エントリ追加で済む（OCP）。
        assert all(callable(m) and callable(c) for m, c in EPOCH_CONVERTERS)
        assert len(EPOCH_CONVERTERS) >= 3


class TestIsSupportedTime:
    """受理集合の公開述語（ISSUE-411 スライス 3: `Bar` 契約表明の判定に使う）。

    受理集合の定義は `EPOCH_CONVERTERS` が唯一持つ。述語はそこから導出され、
    列挙を第 2 の場所へ書き写さない（写しが入ると本クラスの最後の検定が落ちる）。
    """

    @pytest.mark.parametrize(
        "value",
        [
            1_704_067_200,
            np.int64(1_704_067_200),
            np.datetime64("2024-01-01T00:00:00"),
            datetime(2024, 1, 1),
            datetime(2024, 1, 1, tzinfo=timezone.utc),
        ],
        ids=["int", "np.int64", "np.datetime64", "naive datetime", "aware datetime"],
    )
    def test_supported_representations_are_accepted(self, value):
        from simulator.domain.bar_time import is_supported_time

        assert is_supported_time(value) is True

    @pytest.mark.parametrize(
        "value",
        ["2024-01-01T00:00:00", 1.5, True, None, object()],
        ids=["str", "float", "bool", "None", "object"],
    )
    def test_unsupported_representations_are_rejected(self, value):
        from simulator.domain.bar_time import is_supported_time

        assert is_supported_time(value) is False

    def test_predicate_agrees_with_epoch_seconds_on_every_input(self):
        """述語と変換の受理集合が一致する（片方だけ広い／狭いを許さない）。"""
        from simulator.domain.bar_time import is_supported_time

        for value in [
            1, np.int64(1), np.datetime64("2024-01-01"), datetime(2024, 1, 1),
            "2024-01-01", 1.5, True, None, object(),
        ]:
            if is_supported_time(value):
                epoch_seconds(value)  # 受理を宣言したなら変換できなければならない
            else:
                with pytest.raises(ConfigError):
                    epoch_seconds(value)

    def test_predicate_is_derived_from_the_converters_table(self):
        """表へ 1 エントリ足せば述語が追随する（列挙の写しを持たない＝OCP）。"""
        import simulator.domain.bar_time as bar_time_module

        class _Marker:
            pass

        added = (lambda v: isinstance(v, _Marker), lambda v: 0)
        original = bar_time_module.EPOCH_CONVERTERS
        bar_time_module.EPOCH_CONVERTERS = original + (added,)
        try:
            assert bar_time_module.is_supported_time(_Marker()) is True
        finally:
            bar_time_module.EPOCH_CONVERTERS = original
        assert bar_time_module.is_supported_time(_Marker()) is False
