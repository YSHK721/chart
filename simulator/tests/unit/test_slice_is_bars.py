"""TDD: slice_is_bars 境界・空区間拒否式（詳細設計 §6.2.1）。

回帰テスト（user memory「bugfix-pair-with-regression-test」）:
  スライス境界バグ（`< split` 境界・head-prefix 位置ずれ）を禁止する。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pytest

from simulator.usecase.run_is_oos import (
    IsOosValidationError,
    RunIsOosRequest,
    run_is_oos,
    slice_is_bars,
)


@dataclass
class _StubBar:
    """domain Bar 相当の最小 stub（.time のみ比較に使う・marketdata 非依存）。"""

    time: Any


def _bars(times):
    return [_StubBar(time=t) for t in times]


def _times(bars):
    return [b.time for b in bars]


# ---- slice_is_bars 純関数（境界）----------------------------------------


def test_slice_is_bars_keeps_strictly_less_than_split_and_excludes_equal():
    # Arrange
    bars = _bars([1, 2, 3])
    # Act
    result = slice_is_bars(bars, split=3)
    # Assert: split==3 は OOS 側（半開区間 [split, end)）のため除外
    assert _times(result) == [1, 2]


def test_slice_is_bars_excludes_equal_split_value():
    # Arrange
    bars = _bars([1, 2, 3])
    # Act
    result = slice_is_bars(bars, split=2)
    # Assert
    assert _times(result) == [1]


def test_slice_is_bars_keeps_all_when_split_above_all():
    # Arrange
    bars = _bars([1, 2])
    # Act
    result = slice_is_bars(bars, split=99)
    # Assert
    assert _times(result) == [1, 2]


def test_slice_is_bars_returns_head_prefix_only_no_gap_fill():
    # Arrange: 昇順前提違反データ。break による head-prefix 打ち切りを実証（中抜き禁止）
    bars = _bars([1, 2, 5, 3])
    # Act
    result = slice_is_bars(bars, split=4)
    # Assert: 5 で打ち切られ [1,2]。3 を拾わない（中抜き禁止の契約）
    assert _times(result) == [1, 2]


def test_slice_is_bars_does_not_mutate_input():
    # Arrange
    bars = _bars([1, 2, 3])
    original_ids = [id(b) for b in bars]
    # Act
    slice_is_bars(bars, split=2)
    # Assert: 入力 list は破壊されない
    assert [id(b) for b in bars] == original_ids
    assert len(bars) == 3


# ---- 空区間拒否式（M-1: run_is_oos 経由）---------------------------------


def _ok_run_segment(bars, trading_start):  # pragma: no cover - 呼ばれない想定
    raise AssertionError("空区間検証は run_segment 呼出前に中断するはず")


def test_run_is_oos_rejects_empty_is_segment():
    # Arrange: split=1 で全バー >= split → IS バー数 0
    req = RunIsOosRequest(split=1, is_trading_start=1)
    full = _bars([5, 6])
    # Act / Assert
    with pytest.raises(IsOosValidationError):
        run_is_oos(request=req, full_bars=full, run_segment=_ok_run_segment)


def test_run_is_oos_rejects_empty_oos_segment():
    # Arrange: split=99 で全バー < split → OOS バー数 0
    req = RunIsOosRequest(split=99, is_trading_start=1)
    full = _bars([1, 2])
    # Act / Assert
    with pytest.raises(IsOosValidationError):
        run_is_oos(request=req, full_bars=full, run_segment=_ok_run_segment)
