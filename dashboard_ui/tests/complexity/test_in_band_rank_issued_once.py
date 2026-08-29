"""計算量 7（§5.3・レビュー 🔴-1）: 帯内経験順位は**末尾 1 点しか出力に使わない**。

セルの `p` が要るのは当該バー（系列の末尾）1 点だけである。にもかかわらず全系列ぶんの
経験順位を発行すると、系列本数 n に比例した順位算出が丸ごと捨てられる（ISSUE-450 と同型の
「作ってから捨てる」欠陥）。

**状態検証では原理的に落ちない**: n 個作って 1 個だけ使っても、出力（`p`）は正しいままである。
よって Test Spy で経験順位の**発行回数**を数え、「発行 − 使用 = 0」を表明する。

CLAUDE.md 絶対命令 §4.1 に従い、回数そのもの（1 や 3）は期待値に焼き込まない。固定するのは
**無駄の不在**と、**発行が出力量だけで決まる**こと（系列長を変えた 2 点で表明）である。
"""
from __future__ import annotations

import numpy as np
import pytest

from dashboard_ui.domain import continuous_quantile as _cq
from dashboard_ui.tests.complexity.conftest import (
    BarSpy,
    Roles,
    SeriesSpy,
    bars,
    points,
    request_of,
)
from dashboard_ui.usecase.build_reach_sheet import build_reach_sheet
from dashboard_ui.usecase.sheet_models import OscillatorSpec, SheetInstance


class RankSpy:
    """経験順位算出面の Test Spy（数えるのはこの面だけ・§5.3 の唯一の式）。"""

    def __init__(self, inner) -> None:
        self._inner = inner
        self.calls = 0

    def __call__(self, window, current):
        self.calls += 1
        return self._inner(window, current)


def _osc_spec() -> OscillatorSpec:
    return OscillatorSpec(value_series="osc", band_high_series="osc_q90",
                          q_high=0.9, window_n=500, k_events=50)


def _issue_and_use(monkeypatch, *, bar_count: int, cell_count: int) -> "tuple[int, int]":
    """シートを 1 枚組み立て、(発行した経験順位の数, 出力が使った数) を返す。

    帯（500.0）は値（10〜60）より必ず上に置く。よってどのセルも帯内であり、`p` は
    経験順位そのものになる（＝出力から「何個使ったか」が数えられる）。
    """
    values = np.random.default_rng(7).uniform(10.0, 60.0, size=bar_count).tolist()
    instances = [
        SheetInstance(f"osc_{index}", "default", {}, "1m", intrabar_capable=True)
        for index in range(cell_count)
    ]
    series_spy = SeriesSpy()
    specs = {}
    for index, instance in enumerate(instances):
        series_spy.add(instance, {
            "osc": points([value + index for value in values]),
            "osc_q90": points([500.0] * bar_count),
        })
        specs[instance.indicator_id] = _osc_spec()

    rank_spy = RankSpy(_cq.empirical_rank)
    monkeypatch.setattr(_cq, "empirical_rank", rank_spy)

    sheet = build_reach_sheet(
        request_of(*instances),
        series_port=series_spy,
        bar_port=BarSpy({"1m": bars([100.0] * bar_count)}),
        roles=Roles(specs),
    )
    used = [
        cell for cell in sheet.cells
        if cell.p is not None and not cell.tail_unscaled
    ]
    return rank_spy.calls, len(used)


@pytest.mark.parametrize("cell_count", [1, 3])
def test_every_issued_in_band_rank_is_used_by_the_output(monkeypatch, cell_count) -> None:
    """発行 − 使用 = 0。捨てる順位が 1 個でもあれば無駄である。"""
    # Arrange / Act
    issued, used = _issue_and_use(monkeypatch, bar_count=800, cell_count=cell_count)

    # Assert: 使用が 0 だと「何も作らない」実装でも通ってしまう（自己検査）。
    assert used > 0
    assert issued - used == 0


def test_the_in_band_rank_issue_count_does_not_grow_with_the_series_length(
    monkeypatch,
) -> None:
    """オーダーの表明（2 点固定）: 素材が長くなっても発行は増えない。

    `p` は末尾 1 点の量なので、発行は**出力するセル数だけ**で決まる。系列長に比例するなら
    ティックのたびに系列長ぶんの順位を作って捨てていることになる。
    """
    # Arrange / Act
    short_issued, short_used = _issue_and_use(monkeypatch, bar_count=800, cell_count=2)
    long_issued, long_used = _issue_and_use(monkeypatch, bar_count=3000, cell_count=2)

    # Assert
    assert short_used == long_used
    assert short_issued == long_issued
