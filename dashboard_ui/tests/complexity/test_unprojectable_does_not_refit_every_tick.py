"""計算量 8（§7・レビュー 🟡-2 の派生）: 出せない instance が当てはめを毎ティック起こさない。

§5.5.1 の構造的除外（`breakpoints()` を提供できない指標）は当てはめの対象から外れるため、
持ち越しキャッシュの「覆えているか」の判定にも入らない。前進評価できない instance も
**同じ扱い**でなければならない。片方だけを外し忘れると、そのキーは永遠にキャッシュに現れず、
覆えているかの判定が毎回キャッシュを捨てて **同じ時間足の全 instance を当て直す**。

出力は正しいまま（背景も並びも同じ）1 ティックあたりの仕事だけが膨らむ ＝ ISSUE-450 と同型で
あり、**状態検証では原理的に落ちない**。よって発行回数で表明する。
"""
from __future__ import annotations

from dashboard_ui.adapter.breakpoints import BreakpointRegistry
from dashboard_ui.adapter.controller.reach_sheet_controller import ReachSheetController
from dashboard_ui.adapter.gateway.elapsed_comparison_gateway import (
    ElapsedComparisonGateway,
)
from dashboard_ui.adapter.series_role_table import SeriesRoleTable
from dashboard_ui.domain.bar import Bar
from dashboard_ui.usecase.sheet_ports import ForwardEvaluationUnavailable

REF = "jp225_tick"
START = 1_787_004_000
PRICE = 65_760.0


def _bars(count: int, *, step: int = 60) -> "tuple[Bar, ...]":
    return tuple(
        Bar(time=START + index * step, open=PRICE, high=PRICE + 20.0,
            low=PRICE - 20.0, close=PRICE + index * 0.5, volume=10.0 + index)
        for index in range(count)
    )


def _points(count: int, value_of, *, step: int = 60):
    return tuple((START + index * step, float(value_of(index))) for index in range(count))


class _SeriesPort:
    def __init__(self, series) -> None:
        self._series = dict(series)

    def full_series(self, *, indicator_id, variant, params, dataset_ref, timeframe):
        return self._series.get((indicator_id, timeframe), {})


class _BarPort:
    def __init__(self, bars_by_timeframe) -> None:
        self._bars = dict(bars_by_timeframe)

    def bars(self, *, dataset_ref, timeframe):
        return self._bars.get(timeframe, ())

    def forming_bar(self, *, dataset_ref, timeframe, now_unix):
        supplied = self._bars.get(timeframe) or ()
        return supplied[-1] if supplied else None


class _PartlyUnavailableForward:
    """P-3 の Test Spy。3 本のうち 1 本だけ値を出せない（増分器が無い等）。"""

    def __init__(self) -> None:
        self.calls: "list[tuple[str, str, float]]" = []

    def value_at_close(self, *, indicator_id, variant, params, dataset_ref,
                       timeframe, close):
        self.calls.append((indicator_id, timeframe, close))
        if indicator_id == "profit_rsi":
            raise ForwardEvaluationUnavailable(
                "増分器が宣言されていないため前進評価できません"
            )
        return (2.0 * close + 300.0) / (close + 200.0)


def _material():
    return {
        ("moving_averages", "1m"): {
            "MA": _points(60, lambda i: PRICE - 5.0 + i * 0.1),
        },
        ("ma_marod", "1m"): {
            "ma_marod": _points(60, lambda i: 1.8 + i * 0.005),
            "ma_marod_q95": _points(60, lambda i: 3.0),
        },
        ("profit_rsi", "1m"): {
            "rsi": _points(60, lambda i: 40.0 + (i % 11)),
            "rsi_q90": _points(60, lambda i: 80.0),
        },
    }


def _controller(forward) -> ReachSheetController:
    series = _SeriesPort(_material())
    return ReachSheetController(
        series_port=series,
        bar_port=_BarPort({"1m": _bars(60)}),
        roles=SeriesRoleTable(),
        registry=BreakpointRegistry(),
        forward_port=forward,
        elapsed_gateway=ElapsedComparisonGateway(series_port=series),
        is_intrabar_capable=lambda indicator_id, variant, params: True,
    )


def _body(mode: str = "full") -> dict:
    return {
        "dataset_ref": REF, "chart_timeframe": "1m", "mode": mode,
        "instances": [
            {"instance_id": "a", "indicator_id": "moving_averages",
             "variant": "default", "params": {"length": 24}},
            {"instance_id": "b", "indicator_id": "ma_marod", "variant": "default",
             "params": {"source": "hlc3", "length": 50, "q_high": 0.95}},
            {"instance_id": "c", "indicator_id": "profit_rsi", "variant": "default",
             "params": {"rsi_period": 6}},
        ],
    }


def test_a_plain_tick_issues_nothing_even_when_one_instance_cannot_be_projected() -> None:
    """発行 − 使用 = 0。出せない 1 本が居ても epoch 不変なら当てはめは起きない。"""
    # Arrange
    forward = _PartlyUnavailableForward()
    controller = _controller(forward)
    controller.handle(_body())
    issued_after_stage_one = len(forward.calls)

    # Act
    response = controller.handle(_body(mode="tick"))

    # Assert
    assert response["ok"] is True
    assert issued_after_stage_one > 0            # 検出力: 段 1 では実際に当てはめている
    assert len(forward.calls) - issued_after_stage_one == 0


def test_the_issue_count_does_not_grow_with_the_number_of_plain_ticks() -> None:
    """オーダーの表明（2 点固定）: ティック 20 本と 200 本で発行が変わらない。"""
    issued = {}
    for tick_count in (20, 200):
        forward = _PartlyUnavailableForward()
        controller = _controller(forward)
        controller.handle(_body())
        forward.calls.clear()

        for _ in range(tick_count):
            controller.handle(_body(mode="tick"))

        issued[tick_count] = len(forward.calls)

    assert issued[20] == issued[200] == 0
