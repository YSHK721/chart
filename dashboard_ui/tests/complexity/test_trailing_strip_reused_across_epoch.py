"""計算量 12（§5.2 背景ストリップ・依頼者指示 2026-09-04）: 直近区間の読みは epoch で 1 回。

背景ストリップ（直近の指標 10 区間分の読み）は**確定した履歴**（当該バーを除いた観測）
だけから決まる。したがって epoch の中では不変であり、毎秒のポーリング要求ごとに読み直すと
出力は正しいまま同じ読みを作り続ける——状態検証では原理的に落ちない ISSUE-450 / ISSUE-464
と同型の浪費になる。

CLAUDE.md 絶対命令 §4.1 に従い、測るのは時間ではなく**回数**。回数そのものは期待値に
焼き込まない。固定するのは次だけである。

- **不変量 A（要求で増えない）**: 確定履歴が変わらない限り、要求を N 回繰り返しても追加は 0。
  N は 2 点（5 / 20）で固定する。
- **不変量 B（形成中バーで増えない）**: 形成中バーだけが動くティックでは読み直さない。
- **不変量 C（退化していない）**: バーが確定したら読み直す（「二度と読まない」ではない）。
- **不変量 D（オーダー）**: 系列長 2 点（800 / 3000）で読みの計算回数が変わらない。
- **不変量 E（帯内は当てはめ 0）**: 帯内だけのストリップは GPD の当てはめを 1 回も発行しない
  （帯内の読みに当てはめは使われない＝作ってから捨てない）。

なお「発行した読み − 出力に使った読み = 0」は test_in_band_rank_issued_once が
経験順位の面で固定している（ストリップの読みも使用数に数える）。
"""
from __future__ import annotations

import pytest

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
    HistoryStripCache,
    TailFitCache,
    build_reach_sheet,
)
from dashboard_ui.usecase.sheet_models import SheetInstance

_OSC = SheetInstance("profit_rsi", "default", {}, "1m", intrabar_capable=True)


class StripSpy:
    """直近区間の読み面の Test Spy（読みはこの 2 関数からしか作られない）。"""

    def __init__(self, monkeypatch) -> None:
        self.calls = 0
        for name in ("trailing_readings", "trailing_ranks"):
            original = getattr(_cq, name)

            def counted(*args, _original=original, **kwargs):
                self.calls += 1
                return _original(*args, **kwargs)

            monkeypatch.setattr(_cq, name, counted)


class FitSpy:
    """`fit_tail` の Test Spy（当てはめの発行はこの面からしか起きない）。"""

    def __init__(self, monkeypatch) -> None:
        self.calls = 0
        original = _cq.fit_tail

        def counted(events, *, k_events):
            self.calls += 1
            return original(events, k_events=k_events)

        monkeypatch.setattr(_cq, "fit_tail", counted)


@pytest.fixture
def strip_spy(monkeypatch) -> StripSpy:
    return StripSpy(monkeypatch)


@pytest.fixture
def fit_spy(monkeypatch) -> FitSpy:
    return FitSpy(monkeypatch)


def _series(values):
    return {"rsi": points(values), "rsi_q90": points([90.0] * len(values))}


class _Caches:
    """controller の SheetState 相当（要求をまたいで持ち越す口）。"""

    def __init__(self) -> None:
        self.tails = TailFitCache()
        self.events = ExcessEventCache()
        self.history = HistoryStripCache()


def _request(spy: SeriesSpy, caches: _Caches) -> None:
    build_reach_sheet(
        request_of(_OSC), series_port=spy, bar_port=BarSpy({"1m": bars([100.0] * 8)}),
        roles=Roles({"profit_rsi": rsi_spec()}),
        tail_fit_cache=caches.tails, event_cache=caches.events,
        history_cache=caches.history,
    )


def _oscillating(length: int) -> "list[float]":
    """帯（90）を出入りする値列（エピソードが閉じる＝観測が増える形）。"""
    return [10.0 if index % 2 else 95.0 + index % 5 for index in range(length)]


def test_repeating_the_request_recomputes_no_strip(strip_spy: StripSpy) -> None:
    """不変量 A（2 点固定）: 繰り返し 5 / 20 のどちらでも追加は 0。"""
    additional = {}
    for repeats in (5, 20):
        spy = SeriesSpy()
        spy.add(_OSC, _series(_oscillating(120)))
        caches = _Caches()
        _request(spy, caches)
        warmed = strip_spy.calls
        for _ in range(repeats):
            _request(spy, caches)
        additional[repeats] = strip_spy.calls - warmed

    assert additional[5] == 0
    assert additional[20] == 0


def test_a_moving_forming_bar_does_not_recompute_the_strip(strip_spy: StripSpy) -> None:
    """不変量 B: 形成中バーは確定履歴に入らない（ティックでは読み直さない）。"""
    values = _oscillating(120)
    spy = SeriesSpy()
    spy.add(_OSC, _series(values))
    caches = _Caches()
    _request(spy, caches)
    warmed = strip_spy.calls

    spy.add(_OSC, _series([*values[:-1], 99.0]))     # 末尾（形成中）だけが動く
    _request(spy, caches)

    assert strip_spy.calls - warmed == 0


def test_a_newly_confirmed_bar_recomputes_the_strip(strip_spy: StripSpy) -> None:
    """不変量 C: 規則が「二度と読まない」に退化していないこと（キャッシュの自己検査）。"""
    values = _oscillating(120)
    spy = SeriesSpy()
    spy.add(_OSC, _series(values))
    caches = _Caches()
    _request(spy, caches)
    warmed = strip_spy.calls

    spy.add(_OSC, _series([*values, 10.0]))          # バーが 1 本確定した
    _request(spy, caches)

    assert strip_spy.calls - warmed > 0


def test_the_series_length_does_not_change_the_strip_count(strip_spy: StripSpy) -> None:
    """不変量 D（2 点固定）: 系列長 800 / 3000 で読みの計算回数は変わらない。"""
    counts = {}
    for length in (800, 3000):
        spy = SeriesSpy()
        spy.add(_OSC, _series(_oscillating(length)))
        strip_spy.calls = 0

        _request(spy, _Caches())
        counts[length] = strip_spy.calls

    assert counts[800] == counts[3000]


def test_an_in_band_strip_issues_no_tail_fit(fit_spy: FitSpy) -> None:
    """不変量 E（2 点固定）: 帯内だけの直近区間は当てはめを増やさない。

    基準は「履歴 1 本＝ストリップが空」の同一素材。系列を 40 / 3000 本に伸ばして
    ストリップが満たされても、当てはめの発行数は基準と同じである（帯内の読みへ
    当てはめを発行すれば p_at は捨てる＝「作ってから捨てる」の再発）。
    """
    fit_counts = {}
    for length in (1, 40, 3000):
        spy = SeriesSpy()
        spy.add(_OSC, _series([10.0 + (index % 7) for index in range(length)]))
        fit_spy.calls = 0

        _request(spy, _Caches())
        fit_counts[length] = fit_spy.calls

    assert fit_counts[40] == fit_counts[1]
    assert fit_counts[3000] == fit_counts[1]
