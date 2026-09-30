"""足 1 本ごとの dict の組み立ての計算量検定（ISSUE-552/554 段階 2-1）。

測るのは回数である（時間は測らない）。足の ``open`` を読むのは足 1 本ごとの dict の組み立て
だけなので（MFE/MAE は ``high`` / ``low`` だけを読む）、``open`` を読んだ回数が「組み立てた足」の数になる。
観測口は UC の引数 ``bars``（呼び出し側が渡す足）と、構築子の引数 ``bar_rows`` だけである。

固定するのは無駄の不在: ``組み立てた足 − 区間に載った足 = 0``。回数そのものは期待値に書かない。
"""
from __future__ import annotations

import pytest

from simulator.report_ui.tests.unit.test_build_report_payload import (
    _ea_params,
    _make_result,
    _meta,
    _spec,
)
from simulator.report_ui.usecase.build_report_payload import BuildReportPayload

#: 足の本数の 2 点（本数を増やしても無駄が生えないことを見る）。
BAR_COUNTS = (5, 500)


class _CountingBar:
    """``open`` を読まれた回数を数える足（Test Spy）。"""

    def __init__(self, time: int, reads: list) -> None:
        self.time = time
        self.high = 39410.0
        self.low = 39390.0
        self.close = 39405.0
        self._reads = reads

    @property
    def open(self) -> float:
        self._reads.append(self.time)
        return 39400.0


def _segment_and_reads(use_case: BuildReportPayload, count: int):
    reads: list = []
    bars = [_CountingBar(1000 * (i + 1), reads) for i in range(count)]
    payload = use_case.execute_single(
        result=_make_result(
            [100.0, -50.0, 30.0], [2000, 3000, 4000], [10100.0, 10050.0, 10080.0]),
        bars=bars,
        spec=_spec(),
        ea_params=_ea_params(),
        meta=_meta("is"),
    )
    return payload.segments["single"], len(reads)


@pytest.mark.parametrize("count", BAR_COUNTS)
def test_既定は組み立てた足をすべて区間に載せる(count: int) -> None:
    """既定（足を組み立てる）の挙動の固定。report_ui 単体の経路はこの形のまま。"""
    segment, built = _segment_and_reads(BuildReportPayload(), count)
    assert built - len(segment.bars) == 0
    assert len(segment.bars) == segment.meta["bars"] == count


def test_足を載せない指定では足を組み立てない() -> None:
    """載せない足を組み立ててから捨てない（足の本数を増やしても組み立ては生えない）。"""
    from simulator.report_ui.usecase.build_report_payload import no_bar_rows

    built_by_count = {}
    for count in BAR_COUNTS:
        segment, built = _segment_and_reads(BuildReportPayload(bar_rows=no_bar_rows), count)
        assert built - len(segment.bars) == 0, (count, built)
        built_by_count[count] = built
    assert len(set(built_by_count.values())) == 1, built_by_count


@pytest.mark.parametrize("count", BAR_COUNTS)
def test_足を載せない指定でも本数と取引の写像は既定と同じ(count: int) -> None:
    """変わるのは区間の足の並びだけ。本数（``meta.bars``）・取引・集計は既定と 1 つも違わない。"""
    from dataclasses import replace

    from simulator.report_ui.usecase.build_report_payload import no_bar_rows

    default, _ = _segment_and_reads(BuildReportPayload(), count)
    without, _ = _segment_and_reads(BuildReportPayload(bar_rows=no_bar_rows), count)
    assert without.bars == []
    assert without == replace(default, bars=[])
