"""market_profile_forming（adapter/compute/market_profile_forming.py）と dwell.get_active_table の検証。

対象（Phase2 設計 mp_ticklive_design.md「新規 backend compute」）:
  - forming_ticks(symbol, tf, now, since) → {formingStart, ticks:[[sec,mid]...], now}
      formingStart = floor(now, tf)（forming_bar.period_start_unix 再利用）、
      ticks = _load_window_ticks(symbol, formingStart, now)、since 指定時は sec>since の尾部のみ。
  - get_active_table(symbol) → 7×24 の list[list[int]]（dwell._active_table を list 露出）。
  - market_profile_dwell.get_active_table（薄アクセサ）。

設計方針（AAA・既存 test_market_profile_dwell.py の流儀）:
  合成ティックは market_profile_dwell._load_window_ticks（単一注入点）を monkeypatch して注入する。
"""

from __future__ import annotations

import numpy as np
import pytest

from adapter.compute import market_profile_dwell as mpd
from adapter.compute import market_profile_forming as mpf

_DAY = 86400
_DAY0 = 1704067200  # 2024-01-01 00:00 UTC（月曜）。
_H2 = _DAY0 + 7200   # hr2:00（floor(1h) 境界）。


@pytest.fixture(autouse=True)
def _isolate(monkeypatch, tmp_path):
    """キャッシュ隔離＋ディスクキャッシュ基点を tmp へ（既存データ非破壊）。"""
    monkeypatch.setattr(mpd, "_CACHE_ROOT", tmp_path / "cache")
    monkeypatch.setattr(mpd, "_day_source_signature", lambda symbol, day_start: "")
    mpd._reset_caches()
    yield
    mpd._reset_caches()


def _inject(monkeypatch, master):
    s = np.array([t for t, _ in master], dtype=np.int64)
    m = np.array([p for _, p in master], dtype=np.float64)

    def _loader(symbol, start, end):
        win = (s >= int(start)) & (s < int(end))
        s2, m2 = s[win], m[win]
        order = np.argsort(s2, kind="stable")
        return s2[order], m2[order]

    monkeypatch.setattr(mpd, "_load_window_ticks", _loader)


class TestFormingTicks:
    def test_forming_start_is_floor_of_now(self, monkeypatch):
        # Arrange: hr2 に数ティック、now は hr2 の途中。
        master = [(_H2 + 60, 1005.0), (_H2 + 120, 1015.0)]
        _inject(monkeypatch, master)
        now = _H2 + 123
        # Act
        out = mpf.forming_ticks("JP225", "1h", now, since=None)
        # Assert: formingStart = floor(now, 1h) = hr2:00、now も返す。
        assert out["formingStart"] == _H2
        assert out["now"] == now

    def test_returns_all_ticks_in_forming_period(self, monkeypatch):
        # Arrange
        master = [(_H2 + 60, 1005.0), (_H2 + 120, 1015.0), (_H2 + 3600, 1025.0)]
        _inject(monkeypatch, master)
        now = _H2 + 200  # hr2:00..hr2:03:20（hr2+3600 は範囲外）。
        # Act
        out = mpf.forming_ticks("JP225", "1h", now, since=None)
        # Assert: [formingStart, now) の 2 本のみ（sec, mid のペア）。
        assert out["ticks"] == [[_H2 + 60, 1005.0], [_H2 + 120, 1015.0]]

    def test_since_filters_to_tail_only(self, monkeypatch):
        # Arrange
        master = [(_H2 + 60, 1005.0), (_H2 + 120, 1015.0), (_H2 + 180, 1025.0)]
        _inject(monkeypatch, master)
        now = _H2 + 200
        # Act: since=+120 → sec>120 の尾部のみ（+180）。
        out = mpf.forming_ticks("JP225", "1h", now, since=_H2 + 120)
        # Assert
        assert out["ticks"] == [[_H2 + 180, 1025.0]]

    def test_since_none_returns_full_forming(self, monkeypatch):
        # Arrange
        master = [(_H2 + 60, 1005.0)]
        _inject(monkeypatch, master)
        # Act
        out = mpf.forming_ticks("JP225", "1h", _H2 + 200, since=None)
        # Assert
        assert out["ticks"] == [[_H2 + 60, 1005.0]]


class TestGetActiveTable:
    def test_forming_get_active_table_returns_7x24_int_list(self, monkeypatch):
        # Arrange: hr2 に密集ティック（活発判定される）。
        master = [(_H2 + 10 * i, 1005.0) for i in range(30)]
        _inject(monkeypatch, master)
        # Act
        table = mpf.get_active_table("JP225")
        # Assert: 7×24 の list[list[int]]。
        assert isinstance(table, list)
        assert len(table) == 7
        assert all(len(row) == 24 for row in table)
        assert all(isinstance(v, int) for row in table for v in row)

    def test_dwell_get_active_table_accessor_exposes_internal_table(self, monkeypatch):
        # Arrange
        master = [(_H2 + 10 * i, 1005.0) for i in range(30)]
        _inject(monkeypatch, master)
        # Act: dwell の薄アクセサが同じ 7×24 を list で露出する。
        table = mpd.get_active_table("JP225")
        # Assert
        assert len(table) == 7 and all(len(row) == 24 for row in table)
