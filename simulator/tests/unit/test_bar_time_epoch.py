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
        assert all(callable(m) and callable(c) and isinstance(t, str)
                   for m, c, t in EPOCH_CONVERTERS)
        assert len(EPOCH_CONVERTERS) >= 3


class TestIsEpochIntegerIsPublic:
    """整数エントリの判定述語は公開名 `is_epoch_integer` として読める（ISSUE-412 S-0）。

    なぜ公開するか（実測に基づく理由・推測しない）:
        `Bar.time` が epoch 整数か否かの判定を、利用側（tools 層の
        `walk_forward_cli._normalize_span` / `run_is_oos_cli.normalize_time`）が
        手書きしていた。その手書きは `isinstance(v, int)` であり
        `isinstance(np.int64(1), int)` は **False**（numpy 2.4.6 実測）なので、
        comma 形式 CSV 由来の実型が分岐から外れていた（ISSUE-412 (B)/(D)）。
        利用側が判定を書き写さずに済むには、表の整数エントリの判定関数そのものが
        公開名で読める必要がある。

    固定する契約:
        1. `is_epoch_integer` は ``EPOCH_CONVERTERS`` の整数エントリの判定関数
           **そのもの**（同一オブジェクト）である。写しではない。
        2. ``numpy.int64`` を受理する（手書き `isinstance(v, int)` との差そのもの）。
        3. ``bool`` を受理しない（`isinstance(True, int)` は真だが時刻ではない）。
    """

    def test_public_name_is_the_integer_entry_predicate_itself(self):
        # Arrange: 表から BAR タグの整数エントリ（`epoch_seconds(1) == 1` で識別する）
        from simulator.domain.bar_time import BAR, is_epoch_integer

        integer_entries = [
            matches for matches, _c, tag in EPOCH_CONVERTERS
            if tag == BAR and matches(1)
        ]
        # Act / Assert: 表のエントリと公開名が同一オブジェクト（写しがあれば落ちる）
        assert len(integer_entries) == 1
        assert integer_entries[0] is is_epoch_integer

    def test_numpy_int64_is_an_epoch_integer(self):
        from simulator.domain.bar_time import is_epoch_integer

        # 手書き `isinstance(v, int)` が False を返す実型（numpy 2.4.6 実測）。
        assert isinstance(np.int64(1), int) is False  # 前提の実測
        assert is_epoch_integer(np.int64(1_704_067_200)) is True

    def test_bool_is_not_an_epoch_integer(self):
        from simulator.domain.bar_time import is_epoch_integer

        assert isinstance(True, int) is True  # 前提の実測
        assert is_epoch_integer(True) is False


class TestIsSupportedTime:
    """`Bar.time` 契約の公開述語（ISSUE-411 スライス 3 / レビュー 🔴-3）。

    `EPOCH_CONVERTERS` は **2 つの契約**のエントリを 1 表に載せている。
      - ``"BAR"``: `Bar.time` の受理集合（epoch int / ``numpy.datetime64``）。
        既存契約「`pd.Timestamp` 禁止」（`domain/trade_record.py` / `domain/exceptions.py`
        ほかに明文）と一致する。
      - ``"WINDOW"``: 窓境界の受理集合（``datetime``。aware / naive とも）。
        窓境界は `main/tester_settings/window.py` が aware datetime で作る。
    `is_supported_time` は **BAR タグのみ**を見る。`epoch_seconds` は両方を扱う
    （窓境界の正規化に使うため。挙動は是正前と不変）。
    """

    @pytest.mark.parametrize(
        "value",
        [1_704_067_200, np.int64(1_704_067_200), np.datetime64("2024-01-01T00:00:00")],
        ids=["int", "np.int64", "np.datetime64"],
    )
    def test_bar_time_representations_are_accepted(self, value):
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

    @pytest.mark.parametrize(
        "value",
        [datetime(2024, 1, 1), datetime(2024, 1, 1, tzinfo=timezone.utc)],
        ids=["naive datetime", "aware datetime"],
    )
    def test_window_only_representations_are_not_bar_time(self, value):
        """datetime は窓境界の表現であって `Bar.time` ではない（既存契約と整合）。

        `pd.Timestamp` は `datetime` のサブクラスであり、ここが True を返すと
        「`Bar.time` に `pd.Timestamp` 禁止」の明文より契約が広くなる（レビュー 🔴-3）。
        """
        from simulator.domain.bar_time import is_supported_time

        assert is_supported_time(value) is False

    def test_pandas_timestamp_is_not_a_bar_time(self):
        """`pd.Timestamp` は datetime サブクラスだが `Bar.time` の受理集合ではない。"""
        import pandas as pd

        from simulator.domain.bar_time import is_supported_time

        assert isinstance(pd.Timestamp("2024-01-01"), datetime)  # 前提の実測
        assert is_supported_time(pd.Timestamp("2024-01-01")) is False

    def test_bar_contract_is_a_subset_of_the_window_conversion_domain(self):
        """述語が受理するものは必ず変換できる（BAR ⊆ epoch_seconds の定義域）。

        逆は成り立たない（datetime は変換できるが `Bar.time` ではない）。その非対称は
        2 契約を 1 表に載せている構造そのものであり、下の検定で明示する。
        """
        from simulator.domain.bar_time import is_supported_time

        for value in [
            1, np.int64(1), np.datetime64("2024-01-01"), datetime(2024, 1, 1),
            "2024-01-01", 1.5, True, None, object(),
        ]:
            if is_supported_time(value):
                epoch_seconds(value)  # 受理を宣言したなら変換できなければならない

    def test_window_representations_convert_but_are_not_bar_times(self):
        """WINDOW タグは `epoch_seconds` では扱えるが述語では拒否される。"""
        from simulator.domain.bar_time import is_supported_time

        value = datetime(2024, 1, 1, tzinfo=timezone.utc)
        assert epoch_seconds(value) == 1_704_067_200  # 窓境界としては変換できる
        assert is_supported_time(value) is False  # Bar.time としては受理しない

    def test_truly_unsupported_representations_raise_in_epoch_seconds(self):
        """どちらの契約にも属さない表現は変換自体が `ConfigError`。"""
        for value in ["2024-01-01", 1.5, True, None, object()]:
            with pytest.raises(ConfigError):
                epoch_seconds(value)

    def test_predicate_is_derived_from_the_converters_table(self):
        """表へ BAR エントリを足せば述語が追随する（列挙の写しを持たない＝OCP）。"""
        import simulator.domain.bar_time as bar_time_module

        class _Marker:
            pass

        added = (lambda v: isinstance(v, _Marker), lambda v: 0, bar_time_module.BAR)
        original = bar_time_module.EPOCH_CONVERTERS
        bar_time_module.EPOCH_CONVERTERS = original + (added,)
        try:
            assert bar_time_module.is_supported_time(_Marker()) is True
        finally:
            bar_time_module.EPOCH_CONVERTERS = original
        assert bar_time_module.is_supported_time(_Marker()) is False

    def test_a_window_tagged_entry_does_not_widen_the_bar_contract(self):
        """WINDOW タグで足したエントリは述語を広げない（タグが効いていることの固定）。

        識別力: `is_supported_time` がタグを無視して全エントリを見ると本検定が落ちる。
        """
        import simulator.domain.bar_time as bar_time_module

        class _Marker:
            pass

        added = (lambda v: isinstance(v, _Marker), lambda v: 0, bar_time_module.WINDOW)
        original = bar_time_module.EPOCH_CONVERTERS
        bar_time_module.EPOCH_CONVERTERS = original + (added,)
        try:
            assert bar_time_module.is_supported_time(_Marker()) is False
            assert bar_time_module.epoch_seconds(_Marker()) == 0  # 変換はできる
        finally:
            bar_time_module.EPOCH_CONVERTERS = original
