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
