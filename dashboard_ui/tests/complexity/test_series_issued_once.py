"""計算量 1（T-1）: 同一キーの full 系列発行は 1 回以下・発行 − 使用 = 0。

根拠（§7 実測 2026-08-29）: 8 時間足ぶんの価格水準（3 指標・71 本）で 2,316ms、同じ 8 時間足の
全指標（105 本）で 3,637ms。ラダーの 71 本は全指標 105 本の**部分集合**であり、別々に計算すると
2,316ms が丸ごと無駄になる（ISSUE-450 と同型の「作ってから捨てる」欠陥）。

状態検証では原理的に落ちない: 2 回計算しても出力は正しいままである。
"""
from __future__ import annotations

from dashboard_ui.tests.complexity.conftest import (
    BarSpy,
    Roles,
    SeriesSpy,
    bars,
    ma_instance,
    points,
    request_of,
    rsi_spec,
)
from dashboard_ui.usecase.build_reach_sheet import build_reach_sheet
from dashboard_ui.usecase.sheet_models import SheetInstance


def _sheet(instances, spy: SeriesSpy, *, specs=None, chart="1m"):
    timeframes = {instance.timeframe for instance in instances} | {chart}
    bar_spy = BarSpy({tf: bars([100.0] * 6) for tf in timeframes})
    return build_reach_sheet(request_of(*instances, chart=chart), series_port=spy,
                             bar_port=bar_spy, roles=Roles(specs))


def test_no_key_is_ever_issued_twice() -> None:
    instances = [ma_instance(index) for index in range(1, 12)]
    spy = SeriesSpy()
    for index, instance in enumerate(instances):
        spy.add(instance, {"MA": points([100.0 + index] * 6)})

    _sheet(instances * 3, spy)

    assert len(spy.issued) == len(set(spy.issued))


def test_every_issued_key_is_used_by_the_output() -> None:
    """発行 − 使用 = 0。使われない発行が 1 本でもあれば無駄である。"""
    instances = [ma_instance(index) for index in range(1, 12)]
    spy = SeriesSpy()
    for index, instance in enumerate(instances):
        spy.add(instance, {"MA": points([100.0 + index] * 6)})

    sheet = _sheet(instances, spy)

    used = {(row.timeframe, row.label.split("|")[-1]) for row in sheet.rows}
    issued = {(key[3], key[2]) for key in spy.issued}
    assert issued - used == set()


def test_one_issue_feeds_every_consumer_of_that_instance() -> None:
    """P-1 は束契約（1 呼出 = 1 計算 = 複数消費者で共有）。消費者が増えても発行は増えない。"""
    osc = SheetInstance("profit_rsi", "default", {}, "1m", intrabar_capable=True)
    spy = SeriesSpy()
    spy.add(osc, {"rsi": points([10.0, 20.0, 30.0, 40.0, 25.0, 26.0]),
                  "rsi_q90": points([90.0] * 6)})

    sheet = _sheet([osc], spy, specs={"profit_rsi": rsi_spec()})

    assert len(sheet.cells) == 1
    assert len(spy.issued) == 1


def test_the_issue_count_tracks_unique_instances_and_nothing_else() -> None:
    """オーダーの表明（2 点固定）: 束の重複度を変えても発行はユニーク数のまま。"""
    counts = {}
    for unique_count in (11, 23):
        instances = [ma_instance(index) for index in range(1, unique_count + 1)]
        spy = SeriesSpy()
        for index, instance in enumerate(instances):
            spy.add(instance, {"MA": points([100.0 + index] * 6)})

        _sheet(instances * 4, spy)
        counts[unique_count] = len(spy.issued)

    assert counts[11] == 11
    assert counts[23] == 23


def test_bars_are_not_re_fetched_per_instance() -> None:
    """オーダーの表明: 同じ時間足の instance が増えても足の取得は増えない。"""
    requested = {}
    for instance_count in (11, 23):
        instances = [ma_instance(index) for index in range(1, instance_count + 1)]
        spy = SeriesSpy()
        for index, instance in enumerate(instances):
            spy.add(instance, {"MA": points([100.0 + index] * 6)})
        bar_spy = BarSpy({"1m": bars([100.0] * 6)})

        build_reach_sheet(request_of(*instances), series_port=spy,
                          bar_port=bar_spy, roles=Roles())
        requested[instance_count] = len(bar_spy.requested)

    assert requested[11] == requested[23]
