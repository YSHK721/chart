"""計算量: 借りた MP の期間水準（`mp_levels`）が引く足は、足の種類で上から抑えられる。

設計書 §3.5.4 の 2（依頼者裁定 2026-09-29）: 到達時間は定義 D で、期間の始端にその足の
最新バーの time を読む。**サーバはその足の足を 1 回だけ引く（束に同じ足があれば引き直さない）**。

観測の境界: P-2（`bar_port`）は controller が注入で受け取る宣言済みの口であり、Spy はそこだけで
数える（内部名を差し替えない）。

固定するもの（回数そのものは焼き込まない）:
    - 束に同じ足があれば、`mp_levels` が足す足の読み出しは 0。
    - 足の読み出しはどの足も 1 回以下（行の本数ではなく足の種類で抑えられる）。
    - 発行 − 使用 = 0: 読んだ足はすべて応答の行か表示足に使われる。期間の始端に読むのは
      足だけなので、`mp_levels` だけが持ち込む足の形成中足は引かない。
    - オーダー（2 点）: 1 つの足に載る水準を 1 本 → 3 本にしても読み出しは変わらない。
    - 素材が不変（unchanged）の要求では、`mp_levels` の足を 1 本も読まない。

構造は AAA。
"""
from __future__ import annotations

from dashboard_ui.adapter.controller.reach_sheet_controller import ReachSheetController
from dashboard_ui.adapter.gateway.elapsed_comparison_gateway import (
    ElapsedComparisonGateway,
)
from dashboard_ui.tests.complexity.conftest import (
    BarSpy,
    ForwardSpy,
    Registry,
    Roles,
    SeriesSpy,
    bars,
    ma_instance,
    points,
)

REF = "jp225_tick"
CHART = "1m"
#: 設計書 §3.5.1 の対象（日・週・月）。
PERIOD_TIMEFRAMES = ("1D", "1W", "1M")
#: 1 足に載る水準名（設計書 §3.5.3 の行の名前）。
LEVEL_NAMES = ("POC*", "VAH", "VAL")


def _bar_spy() -> BarSpy:
    supplied = {CHART: bars([100.0] * 6)}
    for timeframe in PERIOD_TIMEFRAMES:
        supplied[timeframe] = bars([100.0] * 3, step=86_400)
    return BarSpy(supplied, forming=True)


def _controller(series: SeriesSpy, bar_spy: BarSpy) -> ReachSheetController:
    return ReachSheetController(
        series_port=series,
        bar_port=bar_spy,
        roles=Roles(),
        registry=Registry(set()),
        forward_port=ForwardSpy(),
        elapsed_gateway=ElapsedComparisonGateway(),
        is_intrabar_capable=lambda indicator_id, variant, params: True,
    )


def _mp_levels(timeframes, names) -> "list[dict]":
    return [
        {"timeframe": timeframe, "level": name, "price": 100.0 + index}
        for timeframe in timeframes
        for index, name in enumerate(names)
    ]


def _body(instances=(), mp_levels=None, known_state=None) -> dict:
    body: dict = {
        "dataset_ref": REF,
        "chart_timeframe": CHART,
        "mode": "full",
        "instances": [
            {"indicator_id": instance.indicator_id, "variant": instance.variant,
             "params": dict(instance.params), "timeframe": instance.timeframe}
            for instance in instances
        ],
    }
    if mp_levels is not None:
        body["mp_levels"] = mp_levels
    if known_state is not None:
        body["known_state"] = known_state
    return body


def _bundle_on(timeframe: str) -> "tuple[SeriesSpy, list]":
    instance = ma_instance(5, timeframe)
    series = SeriesSpy()
    series.add(instance, {"MA": points([101.0] * 3, step=86_400)})
    return series, [instance]


def test_levels_on_a_bundle_timeframe_read_no_extra_bars() -> None:
    """束に同じ足があれば、mp_levels が足す足の読み出しは 0。"""
    # Arrange
    series, instances = _bundle_on("1D")
    without, with_levels = _bar_spy(), _bar_spy()

    # Act
    _controller(series, without).handle(_body(instances))
    response = _controller(series, with_levels).handle(
        _body(instances, _mp_levels(["1D"], LEVEL_NAMES))
    )

    # Assert
    assert response["ok"] is True
    assert sorted(with_levels.requested) == sorted(without.requested)
    assert sorted(with_levels.formed) == sorted(without.formed)


def test_each_level_timeframe_is_read_at_most_once() -> None:
    # Arrange
    bar_spy = _bar_spy()

    # Act
    response = _controller(SeriesSpy(), bar_spy).handle(
        _body(mp_levels=_mp_levels(PERIOD_TIMEFRAMES, LEVEL_NAMES))
    )

    # Assert
    assert response["ok"] is True
    assert len(bar_spy.requested) == len(set(bar_spy.requested))
    assert len(bar_spy.formed) == len(set(bar_spy.formed))


def test_every_bar_read_is_used_by_a_row_or_the_chart() -> None:
    """発行 − 使用 = 0（足と形成中足の両方の面）。"""
    # Arrange
    bar_spy = _bar_spy()

    # Act
    response = _controller(SeriesSpy(), bar_spy).handle(
        _body(mp_levels=_mp_levels(PERIOD_TIMEFRAMES, LEVEL_NAMES))
    )

    # Assert: 足は行の到達時間か表示足に使われる。形成中足は表示足（状態トークン）にしか
    #   使われない——MP の行は期間の始端（最新バーの time）しか読まないため。
    used_bars = {CHART} | {row["timeframe"] for row in response["rows"]}
    assert set(bar_spy.requested) - used_bars == set()
    assert set(bar_spy.formed) - {CHART} == set()


def test_bar_reads_do_not_grow_with_levels_per_timeframe() -> None:
    """オーダー（2 点）: 1 足に載る水準の本数を変えても読み出しは変わらない。"""
    # Arrange
    reads = {}

    # Act
    for count in (1, len(LEVEL_NAMES)):
        bar_spy = _bar_spy()
        _controller(SeriesSpy(), bar_spy).handle(
            _body(mp_levels=_mp_levels(PERIOD_TIMEFRAMES, LEVEL_NAMES[:count]))
        )
        reads[count] = (sorted(bar_spy.requested), sorted(bar_spy.formed))

    # Assert
    assert reads[1] == reads[len(LEVEL_NAMES)]


def test_an_unchanged_answer_reads_no_level_timeframe() -> None:
    """素材が不変なら、mp_levels の足を読む前に unchanged で返る（省リソース段階 2）。"""
    # Arrange
    levels = _mp_levels(PERIOD_TIMEFRAMES, LEVEL_NAMES)
    controller_spy = _bar_spy()
    controller = _controller(SeriesSpy(), controller_spy)
    first = controller.handle(_body(mp_levels=levels))
    controller_spy.requested.clear()
    controller_spy.formed.clear()

    # Act
    again = controller.handle(_body(mp_levels=levels, known_state=first["state"]))

    # Assert
    assert again.get("unchanged") is True
    assert set(controller_spy.requested) - {CHART} == set()
    assert set(controller_spy.formed) - {CHART} == set()
