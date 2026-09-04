"""計算量 13（ISSUE-487）: 表示時点への巻き戻しの周期判定は、素材本数に比例させない。

表示時点への巻き戻し（gateway）の「遅延時点より後の周期の行だけ落とす」判定（ISSUE-487 の根治）は、
周期計算（`period_start_unix`）を**ラベルが cutoff より未来の行**（高々、形成中の 1〜2 行）
にだけ発行する。全行へ回すと、毎要求・毎時間足で素材本数（1m は数万行）に比例した
周期計算を発行することになる（出力は正しいままなので状態検証では落ちない・ISSUE-450 同型）。

CLAUDE.md 絶対命令 §4.1: 測るのは回数。回数そのものは焼き込まず、固定するのは
**素材本数 2 点（120 / 3000）で発行数が変わらない**ことである。
"""
from __future__ import annotations

import pandas as pd
import pytest

from dashboard_ui.adapter.gateway import indicator_ui_compute_gateway as gw_mod
from dashboard_ui.adapter.gateway.indicator_ui_compute_gateway import (
    IndicatorUiComputeGateway,
)
from dashboard_ui.tests.unit.test_indicator_ui_compute_gateway import (
    REF,
    START,
    RewindSpy,
    frame,
)


class PeriodSpy:
    """`period_start_unix` の Test Spy（周期計算の発行はこの面からしか起きない）。"""

    def __init__(self, monkeypatch) -> None:
        self.calls = 0
        original = gw_mod.period_start_unix

        def counted(unix, timeframe):
            self.calls += 1
            return original(unix, timeframe)

        monkeypatch.setattr(gw_mod, "period_start_unix", counted)


@pytest.fixture
def period_spy(monkeypatch) -> PeriodSpy:
    return PeriodSpy(monkeypatch)


def test_the_period_check_count_does_not_grow_with_the_material_length(
    period_spy: PeriodSpy,
) -> None:
    """オーダーの表明（2 点固定）: 素材 120 本と 3000 本で周期計算の発行数は同じ。"""
    counts = {}
    for rows in (120, 3000):
        material = frame(rows)
        cutoff_now = START + (rows - 3) * 60 + 12    # 末尾 2 本だけが遅延時点より未来
        spy = RewindSpy({"1m": material}, forming=None)
        gateway = IndicatorUiComputeGateway(
            bridge=spy.namespace(), now=lambda: cutoff_now,
        )
        period_spy.calls = 0

        bars = gateway.bars(dataset_ref=REF, timeframe="1m")

        assert len(bars) == rows - 2                 # 落とす規則そのものは生きている
        counts[rows] = period_spy.calls

    assert counts[120] == counts[3000]
