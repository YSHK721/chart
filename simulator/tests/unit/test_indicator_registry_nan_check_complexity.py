"""指標系列の NaN 検査の計算量検定（ISSUE-553 項目 1・絶対命令 2026-08-28）。

なぜ在るか（実測 2026-09-28）: `PandasIndicatorRegistry.get` は呼ばれるたびに系列全体を
``isna`` で走査していた。系列は事前計算で不変なので結果は毎回同じ。215 万行の系列で
1 回 4.0ms × 15,602 回 = 62.6 秒（run の実行 103 秒の約 6 割）。出力は同じなので状態検証では
原理的に落ちない。

観測の境界: registry の入力（登録する系列）そのものを Test Spy にし、``isna`` の発行を数える
（内部名の monkeypatch はしない・絶対命令 2026-09-25）。

表明:
    1. 発行した走査 − 登録系列のうち読んだ系列の数 = 0。
    2. ``get`` の回数 10 / 100 の 2 点で発行が増えない。回数そのものは焼き込まない。
    3. 検査は省かない: 有効区間の NaN は何回目の ``get`` でも IndicatorNaNError。
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from simulator.adapter.indicator.registry import PandasIndicatorRegistry
from simulator.domain.exceptions import IndicatorNaNError


class _ScanSpy(pd.Series):
    """``isna``（系列全体の走査）の発行を数える Test Spy。"""

    scans: "list[int]"

    @property
    def _constructor(self):
        return pd.Series

    def isna(self):
        self.scans.append(1)
        return super().isna()


def _spy(values) -> _ScanSpy:
    series = _ScanSpy(values)
    series.scans = []
    return series


def _scans_after(gets: int) -> int:
    series = {"sma": _spy([np.nan, np.nan, 1.0, 2.0, 3.0]), "open": _spy([1.0, 2.0, 3.0, 4.0, 5.0])}
    registry = PandasIndicatorRegistry(series)
    for _ in range(gets):
        registry.get("sma")
        registry.get("open")
    return sum(len(s.scans) for s in series.values())


def test_each_series_is_scanned_once() -> None:
    # Act / Assert: 発行 − 読んだ系列（2 本）= 0
    assert _scans_after(gets=10) - 2 == 0


def test_scans_do_not_grow_with_gets() -> None:
    # Act / Assert: get 10 回と 100 回で発行が同じ
    assert _scans_after(gets=100) - _scans_after(gets=10) == 0


@pytest.mark.parametrize("gets", [1, 3])
def test_invalid_nan_is_refused_on_every_get(gets) -> None:
    # Arrange: 有効区間（最初の非 NaN 以降）に NaN
    registry = PandasIndicatorRegistry({"sma": pd.Series([np.nan, 1.0, np.nan, 2.0])})

    # Act / Assert: 何回目でも拒否する（失敗の結果を「検査済み」として覚えない）
    for _ in range(gets):
        with pytest.raises(IndicatorNaNError):
            registry.get("sma")
