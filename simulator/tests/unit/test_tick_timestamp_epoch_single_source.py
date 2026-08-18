"""tick timestamp 列 → epoch 秒の規則が**単一ソース**であることを固定する（ISSUE-406）。

是正対象（実測 / 是正前の状態）:
    `simulator/tools/run_scan_contacts_cli.py` の `_default_ticks_factory` が
    「timestamp 列 → epoch 秒」を手書き複製し、しかも ns 解像度前提
    （``astype("int64") // 1_000_000_000``）で書いていた。一方
    `ParquetTickRepository.load_ticks` が読み戻す dtype は parquet 由来の
    ``datetime64[ms]`` / ``datetime64[us]``（実 store で確認）であり、CLI の秒は
    10^6 倍（ms なら 10^6・us なら 10^3）ずれる（実測: 正 1709251200 に対し 1709）。

    原因は除数の値ではなく**規則の複製**である。正しい規則（naive=UTC・秒へ floor・
    dtype 解像度に依存しない）は `load_ticks` の窓フィルタが既に持っていた。

本モジュールが固定する契約:
  1. **解像度非依存**: 同じ瞬間を指す ms / us / ns の timestamp 列は同一の epoch 秒になる。
  2. **naive = UTC・floor**: naive はローカル TZ に依存せず UTC、秒未満は floor。
  3. **CLI の実挙動**: `_default_ticks_factory` が実 parquet store（書込→読戻しで
     dtype が ns でなくなる）から正しい epoch 秒を返す（是正前はここが 10^6 倍ずれた）。
  4. **単一ソース**: CLI と `load_ticks` が読む変換の実体は同一関数オブジェクトである。
  5. **明示拒否**: 黙って誤った秒になる入力（非 datetime64 列・NaT 混入）は `DataError`。
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone

import pandas as pd
import pytest

from simulator.adapter.repository.tick_parquet import (
    ParquetTickRepository,
    timestamp_epoch_seconds,
)


def _epoch(*args: int) -> int:
    return int(datetime(*args, tzinfo=timezone.utc).timestamp())


_TIMES = [
    datetime(2024, 3, 1, 0, 0, 0),
    datetime(2024, 3, 1, 0, 0, 1),
    datetime(2024, 3, 1, 12, 34, 56),
]
_EXPECTED = [
    _epoch(2024, 3, 1, 0, 0, 0),
    _epoch(2024, 3, 1, 0, 0, 1),
    _epoch(2024, 3, 1, 12, 34, 56),
]


class TestTimestampEpochSeconds:
    """契約 1・2: 変換関数そのものの規則。"""

    @pytest.mark.parametrize("unit", ["ms", "us", "ns"])
    def test_the_same_instant_yields_the_same_epoch_for_every_resolution(self, unit):
        series = pd.Series(pd.to_datetime(_TIMES)).astype(f"datetime64[{unit}]")
        assert timestamp_epoch_seconds(series).tolist() == _EXPECTED

    def test_naive_timestamps_are_interpreted_as_utc(self, tokyo_local_timezone):
        # ローカル TZ に依存しないこと（naive=UTC の共有規則・datawindow と同一）。
        # TZ の固定は共有 fixture（conftest.py）で行う。`monkeypatch.setenv` のみでは
        # プロセス TZ は変わらず（tzset 漏れ）、検定が無力化する（レビュー 🟡-1 実測）。
        series = pd.Series(pd.to_datetime(_TIMES))
        assert timestamp_epoch_seconds(series).tolist() == _EXPECTED

    def test_aware_timestamps_convert_by_their_own_offset(self):
        aware = pd.Series(pd.to_datetime(_TIMES)).dt.tz_localize("UTC").dt.tz_convert(
            "Asia/Tokyo"
        )
        assert timestamp_epoch_seconds(aware).tolist() == _EXPECTED

    def test_subsecond_values_floor_to_the_second(self):
        series = pd.Series(pd.to_datetime(["2024-03-01T00:00:00.999999"]))
        assert timestamp_epoch_seconds(series).tolist() == [_epoch(2024, 3, 1)]


class TestTimestampEpochSecondsRejections:
    """契約 5: 黙って誤った秒になる入力の明示拒否（レビュー 🟡-2）。

    どちらも例外なしで通すと ISSUE-406 と同型の「例外なしの桁ずれ」になる入力
    （int64 列は `pd.to_datetime` が ns と解釈して 10^9 倍ずれ・NaT は int64 最小値）。
    """

    def test_a_non_datetime_column_is_rejected(self):
        from simulator.domain.exceptions import DataError

        with pytest.raises(DataError):
            timestamp_epoch_seconds(pd.Series([1709251200, 1709251201], dtype="int64"))

    def test_a_column_containing_nat_is_rejected(self):
        from simulator.domain.exceptions import DataError

        with pytest.raises(DataError):
            timestamp_epoch_seconds(
                pd.Series(pd.to_datetime(["2024-03-01T00:00:00", None]))
            )


class TestDefaultTicksFactoryEpochs:
    """契約 3: CLI の実経路（実 parquet store・書込→読戻しで dtype は ns でない）。"""

    def _ticks_via_cli(self, tmp_path):
        from simulator.tools.run_scan_contacts_cli import _default_ticks_factory

        frame = pd.DataFrame(
            {
                "timestamp": pd.to_datetime(_TIMES),
                "bid": [100.0, 101.0, 102.0],
                "ask": [100.5, 101.5, 102.5],
                "last": [100.25, 101.25, 102.25],
                "volume": [1, 1, 1],
            }
        )
        ParquetTickRepository(root=tmp_path).write_ticks(
            "JP225", frame, mode="overwrite"
        )
        args = argparse.Namespace(tick_store_root=tmp_path, symbol="JP225")
        ticks_fn = _default_ticks_factory(args)
        return ticks_fn(_EXPECTED[0], _EXPECTED[-1] + 1)

    def test_the_cli_returns_true_epoch_seconds_from_a_real_store(self, tmp_path):
        ticks = self._ticks_via_cli(tmp_path)
        assert [sec for sec, _mid in ticks] == _EXPECTED

    def test_the_cli_returns_mid_prices_alongside(self, tmp_path):
        ticks = self._ticks_via_cli(tmp_path)
        assert [mid for _sec, mid in ticks] == [100.25, 101.25, 102.25]


class TestSingleSource:
    """契約 4: 実体の同一性（行動テストが主・本テストは従）。"""

    def test_the_repository_module_exports_the_shared_converter(self):
        from simulator.adapter.repository import _tick_frame, tick_parquet

        assert tick_parquet.timestamp_epoch_seconds is _tick_frame.timestamp_epoch_seconds
