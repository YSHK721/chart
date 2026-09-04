"""`datawindow.half_open`（ISSUE-401 🟡-2）: 取得窓 `[start, end)` の規則の唯一の実体。

本モジュールが固定する契約:
  1. aware datetime は自身の offset で epoch 秒へ（既存の窓生成点＝`resolve_data_window` が
     生成する UTC aware に対して是正前と同値＝byte 等価）。
  2. naive datetime は **UTC** とみなす（3 択のうちの確定。`test_bar_time_epoch.py` の
     既存合意と同一規則）。
  3. 変換結果はプロセスのローカル TZ に依存しない（環境依存という原因の除去）。
  4. 半開判定は `contains` ひとつ（始端は含み、終端は含まない）。
  5. 依存ゼロ: `simulator` / `marketdata` / numpy / pandas を import しない
     （domain 層から読める中立共有パッケージであることの条件）。
"""
from __future__ import annotations

import os
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from datawindow.half_open import HalfOpenEpochWindow, epoch_seconds_of_datetime

_JST = timezone(timedelta(hours=9))


class TestEpochSecondsOfDatetime:
    def test_aware_utc_uses_its_own_offset(self):
        assert epoch_seconds_of_datetime(datetime(2024, 1, 1, tzinfo=timezone.utc)) == 1_704_067_200

    def test_aware_non_utc_uses_its_own_offset(self):
        # 2024-01-01 09:00+09:00 == 2024-01-01 00:00Z
        assert epoch_seconds_of_datetime(datetime(2024, 1, 1, 9, tzinfo=_JST)) == 1_704_067_200

    def test_naive_is_interpreted_as_utc(self):
        assert epoch_seconds_of_datetime(datetime(2024, 1, 1)) == 1_704_067_200

    def test_sub_second_is_truncated_toward_zero(self):
        value = datetime(2024, 1, 1, 0, 0, 0, 500_000, tzinfo=timezone.utc)
        assert epoch_seconds_of_datetime(value) == 1_704_067_200

    def test_return_type_is_int(self):
        assert isinstance(epoch_seconds_of_datetime(datetime(2024, 1, 1)), int)


class TestLocalTimezoneIndependence:
    """同一入力はプロセスのローカル TZ に依存せず同一 epoch を返す（原因の除去）。"""

    def test_naive_datetime_is_identical_under_any_local_timezone(self):
        saved, saved_tzname = os.environ.get("TZ"), time.tzname
        results = []
        try:
            for tz_name in ("UTC", "Asia/Tokyo", "America/New_York"):
                os.environ["TZ"] = tz_name
                time.tzset()
                results.append(epoch_seconds_of_datetime(datetime(2024, 1, 1)))
        finally:
            if saved is None:
                os.environ.pop("TZ", None)
            else:
                os.environ["TZ"] = saved
            time.tzset()
        assert time.tzname == saved_tzname
        assert results == [1_704_067_200] * 3


class TestHalfOpenPredicate:
    _WINDOW = HalfOpenEpochWindow.from_datetimes(
        datetime(2024, 1, 2, tzinfo=timezone.utc),
        datetime(2024, 1, 4, tzinfo=timezone.utc),
    )

    def test_start_boundary_is_inclusive(self):
        assert self._WINDOW.contains(self._WINDOW.start)

    def test_end_boundary_is_exclusive(self):
        assert not self._WINDOW.contains(self._WINDOW.end)

    def test_interior_is_contained(self):
        assert self._WINDOW.contains(self._WINDOW.start + 1)

    def test_before_start_is_excluded(self):
        assert not self._WINDOW.contains(self._WINDOW.start - 1)

    def test_from_datetimes_normalizes_both_bounds(self):
        window = HalfOpenEpochWindow.from_datetimes(datetime(2024, 1, 2), datetime(2024, 1, 4))
        assert (window.start, window.end) == (
            epoch_seconds_of_datetime(datetime(2024, 1, 2, tzinfo=timezone.utc)),
            epoch_seconds_of_datetime(datetime(2024, 1, 4, tzinfo=timezone.utc)),
        )

    def test_inverted_window_contains_nothing(self):
        # 妥当性検査は呼出側の責務。本型は推測で境界を入れ替えない（空窓として扱う）。
        inverted = HalfOpenEpochWindow(start=100, end=10)
        assert not any(inverted.contains(t) for t in (9, 10, 50, 100, 101))

    def test_window_is_immutable(self):
        with pytest.raises(Exception):
            self._WINDOW.start = 0  # type: ignore[misc]


class TestPackageHasNoHeavyDependencies:
    """domain 層から読める条件（`simulator` / `marketdata` / numpy / pandas に依存しない）。"""

    def test_import_does_not_pull_numpy_pandas_or_sibling_packages(self):
        # 別プロセスで測る（本テストプロセスは既に pytest 経由で numpy を読み込んでいる）。
        code = (
            "import sys; import datawindow.half_open; "
            "print(sorted(m for m in ('numpy', 'pandas', 'simulator', 'marketdata', 'common') "
            "if m in sys.modules))"
        )
        root = Path(__file__).resolve().parents[2]
        out = subprocess.run(
            [sys.executable, "-c", code], capture_output=True, text=True, cwd=root, check=True
        )
        assert out.stdout.strip() == "[]", out.stdout
