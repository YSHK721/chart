"""計算量 11（ISSUE-464 ③）: 帯外イベント履歴は epoch の中で 1 回しか畳まない。

超過エピソードの極値列（`excess_event_history`）は**確定した履歴**（当該バーを除いた観測）
だけから決まる。したがって epoch の中では不変である。にもかかわらず、第 2 表のセルと
§5.5.5 の背景の目盛りが**それぞれ**毎要求畳み直していた（実測 2026-08-30・8 足束 1 要求:
48 回 / 366 ms ＝ 24 instance × 2 消費者）。畳み込みは 1 点ずつの Python ループなので
系列長に比例する。

出力は正しいままなので状態検証では原理的に落ちない（ISSUE-450 / ISSUE-257 と同型）。
CLAUDE.md 絶対命令 §4.1 に従い、測るのは時間ではなく**回数**である。回数そのものは
期待値に焼き込まない。固定するのは次の 4 つだけである。

- **不変量 A（消費者で増えない）**: 同じ instance を 2 人（セル・背景）が読んでも畳み込みは
  1 回（発行 − 使用 = 0 の裏返し）。
- **不変量 B（要求で増えない）**: 確定履歴が変わらない限り、要求を N 回繰り返しても
  **追加は 0**。N は 2 点（5 / 20）で固定する。
- **不変量 C（退化していない）**: 履歴が伸びたら畳み直す（「二度と畳まない」ではない）。
- **不変量 D（オーダー）**: 系列長 2 点（800 / 3000）で畳み込み回数が変わらない。
"""
from __future__ import annotations

import pytest

from dashboard_ui.adapter.quantile_scale_builder import quantile_scale_of
from dashboard_ui.domain import continuous_quantile as _cq
from dashboard_ui.tests.complexity.conftest import (
    BarSpy,
    Roles,
    SeriesSpy,
    bars,
    points,
    request_of,
    rsi_spec,
)
from dashboard_ui.usecase.build_reach_sheet import (
    ExcessEventCache,
    TailFitCache,
    build_reach_sheet,
)
from dashboard_ui.usecase.sheet_models import SheetInstance

_OSC = SheetInstance("profit_rsi", "default", {}, "1m", intrabar_capable=True)


class FoldSpy:
    """`excess_event_history` の Test Spy（履歴の畳み込みはこの面からしか起きない）。"""

    def __init__(self, monkeypatch) -> None:
        self.calls = 0
        original = _cq.excess_event_history

        def counted(values, band_highs, *, excess=None):
            self.calls += 1
            return (
                original(values, band_highs)
                if excess is None
                else original(values, band_highs, excess=excess)
            )

        monkeypatch.setattr(_cq, "excess_event_history", counted)


@pytest.fixture
def fold_spy(monkeypatch) -> FoldSpy:
    return FoldSpy(monkeypatch)


def _series(values):
    return {"rsi": points(values), "rsi_q90": points([90.0] * len(values))}


def _request(spy: SeriesSpy, tails: TailFitCache, events: ExcessEventCache) -> None:
    """要求 1 件ぶん（第 2 表のセル ＋ 背景の目盛り＝同じ観測を読む 2 人の消費者）。"""
    roles = Roles({"profit_rsi": rsi_spec()})
    build_reach_sheet(
        request_of(_OSC), series_port=spy, bar_port=BarSpy({"1m": bars([100.0] * 8)}),
        roles=roles, tail_fit_cache=tails, event_cache=events,
    )
    quantile_scale_of(
        spec=rsi_spec(),
        series=dict(spy.full_series(indicator_id="profit_rsi", variant="default",
                                    params={}, dataset_ref="jp225_tick", timeframe="1m")),
        tails=tails, key=_OSC.key, events=events,
    )


def _oscillating(length: int) -> "list[float]":
    """帯（90）を出入りする値列（エピソードが閉じる＝観測が増える形）。"""
    return [10.0 if index % 2 else 95.0 + index % 5 for index in range(length)]


def test_two_consumers_of_the_same_observation_fold_it_once(fold_spy: FoldSpy) -> None:
    """不変量 A: セルと背景は同じ確定履歴を読む。2 人いても畳み込みは 1 回。"""
    spy = SeriesSpy()
    spy.add(_OSC, _series(_oscillating(120)))

    _request(spy, TailFitCache(), ExcessEventCache())

    assert fold_spy.calls == 1


def test_repeating_the_request_folds_no_additional_history(fold_spy: FoldSpy) -> None:
    """不変量 B（2 点固定）: 繰り返し 5 / 20 のどちらでも追加は 0。"""
    additional = {}
    for repeats in (5, 20):
        spy = SeriesSpy()
        spy.add(_OSC, _series(_oscillating(120)))
        tails, events = TailFitCache(), ExcessEventCache()
        _request(spy, tails, events)
        warmed = fold_spy.calls
        for _ in range(repeats):
            _request(spy, tails, events)
        additional[repeats] = fold_spy.calls - warmed

    assert additional[5] == 0
    assert additional[20] == 0


def test_a_moving_forming_bar_does_not_refold_the_history(fold_spy: FoldSpy) -> None:
    """段 2 の鮮度は畳み込みを増やさない（形成中バーは確定履歴に入らない）。"""
    values = _oscillating(120)
    spy = SeriesSpy()
    spy.add(_OSC, _series(values))
    tails, events = TailFitCache(), ExcessEventCache()
    _request(spy, tails, events)
    warmed = fold_spy.calls

    spy.add(_OSC, _series([*values[:-1], 99.0]))     # 末尾（形成中）だけが動く
    _request(spy, tails, events)

    assert fold_spy.calls - warmed == 0


def test_a_grown_history_is_folded_again(fold_spy: FoldSpy) -> None:
    """不変量 C: 規則が「二度と畳まない」に退化していないこと（キャッシュの自己検査）。"""
    values = _oscillating(120)
    spy = SeriesSpy()
    spy.add(_OSC, _series(values))
    tails, events = TailFitCache(), ExcessEventCache()
    _request(spy, tails, events)
    warmed = fold_spy.calls

    spy.add(_OSC, _series([*values, 10.0]))          # バーが 1 本確定した
    _request(spy, tails, events)

    assert fold_spy.calls - warmed > 0


def test_a_revised_confirmed_value_is_folded_again(fold_spy: FoldSpy) -> None:
    """本数が同じでも**確定した中身が入れ替わったら**畳み直す（古い観測を配らない）。"""
    values = _oscillating(120)
    spy = SeriesSpy()
    spy.add(_OSC, _series(values))
    tails, events = TailFitCache(), ExcessEventCache()
    _request(spy, tails, events)
    warmed = fold_spy.calls

    revised = list(values)
    revised[40] = 97.5                               # 履歴の途中を遡って訂正する
    spy.add(_OSC, _series(revised))
    _request(spy, tails, events)

    assert fold_spy.calls - warmed > 0


def test_the_series_length_does_not_change_the_fold_count(fold_spy: FoldSpy) -> None:
    """不変量 D（2 点固定）: 系列長 800 / 3000 で畳み込み回数は変わらない。"""
    counts = {}
    for length in (800, 3000):
        spy = SeriesSpy()
        spy.add(_OSC, _series(_oscillating(length)))
        fold_spy.calls = 0

        _request(spy, TailFitCache(), ExcessEventCache())
        counts[length] = fold_spy.calls

    assert counts[800] == counts[3000]
