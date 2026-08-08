"""上位足計算（計算.時間足）がリプレイでも投影される（ISSUE-287）。

実際に起きていたこと（2026-08-08 実 HTTP で検出）:
    front は `/compute` へ `computeTimeframe` を送るが、リプレイ core の usecase は
    それを受け取る口を持たず**無言で捨てて**チャート足で計算していた。同一リクエストで
    ライブ core は 2 段の階段（日境界）、リプレイ core は 600 段（5m のまま）＝別物。
    front は投影済みのつもりで描くため、誤りは表示にしか出ない（無言の縮退）。

規約はライブ core と同一（`adapter.compute.mtf_projection` が唯一源）で、本 usecase は
入力を合わせて Port へ渡すだけ。ここでは「H で計算し C の時刻へ投影する」結線を固定する。
構造: Arrange-Act-Assert（AAA）。
"""
from __future__ import annotations

from simulator.replay_ui.usecase.causal_compute import CausalComputeRequest, causal_compute

DAY = 86400


class _FakePort:
    """load_source / compute / project を記録する Port（実計算に依存しない）。"""

    def __init__(self) -> None:
        self.loaded: "list[str | None]" = []
        self.computed_bars: "list[list[dict]]" = []
        self.projected: "list[tuple[list[int], str]]" = []

    def load_source(self, ref: str, timeframe):        # noqa: D401
        self.loaded.append(timeframe)
        if timeframe == "1D":
            return [{"time": 10 * DAY, "close": 1.0}, {"time": 11 * DAY, "close": 2.0}]
        return [{"time": 10 * DAY, "close": 3.0}, {"time": 10 * DAY + 43200, "close": 3.0},
                {"time": 11 * DAY, "close": 3.0}, {"time": 11 * DAY + 300, "close": 3.0}]

    def compute(self, indicator, variant, mode, bars, params):
        self.computed_bars.append(bars)
        return [{"name": "MA", "kind": "line", "data": [{"time": b["time"], "value": 1.0} for b in bars]}]

    def project(self, series, chart_times, compute_tf):
        self.projected.append((list(chart_times), compute_tf))
        return [{**s, "data": [{"time": t, "value": 9.9} for t in chart_times]} for s in series]


def _req(**over):
    base = dict(indicator="moving_averages", variant="default", ref="jp225_tick",
                timeframe="5m", limit=None, until_time=None, mode=None, forming=None,
                params={})
    base.update(over)
    return CausalComputeRequest(**base)


def test_compute_timeframe_computes_on_h_and_projects_onto_chart_times():
    port = _FakePort()

    out = causal_compute(request=_req(compute_timeframe="1D"), compute_port=port)

    assert port.loaded == ["5m", "1D"], "C（時間軸）と H（計算）の両方を読む"
    assert [b["time"] for b in port.computed_bars[0]] == [10 * DAY, 11 * DAY], "計算は H のバーで行う"
    chart_times, tf = port.projected[0]
    assert tf == "1D"
    assert chart_times == [10 * DAY, 10 * DAY + 43200, 11 * DAY, 11 * DAY + 300], "投影先は C のバー時刻"
    assert [p["time"] for p in out[0]["data"]] == chart_times, "応答の時刻は C の時間軸"


def test_no_projection_when_compute_timeframe_is_absent_or_chart():
    for value in (None, "chart", "5m"):
        port = _FakePort()

        causal_compute(request=_req(compute_timeframe=value), compute_port=port)

        assert port.projected == [], f"{value!r} では投影しない（従来経路）"
        assert port.loaded == ["5m"], f"{value!r} では C だけを読む"


def test_causality_is_kept_for_both_timeframes():
    """H・C とも untilTime で切る（リビール T より先の足を計算へ入れない）。"""
    port = _FakePort()

    causal_compute(request=_req(compute_timeframe="1D", until_time=10 * DAY + 43200),
                   compute_port=port)

    assert [b["time"] for b in port.computed_bars[0]] == [10 * DAY], "H が T で切られていない"
    chart_times, _ = port.projected[0]
    assert chart_times == [10 * DAY, 10 * DAY + 43200], "C も T で切られる"


def test_empty_chart_window_returns_no_series():
    """C が空（T より前のバーが無い）なら計算も投影もせず空を返す。"""
    port = _FakePort()

    out = causal_compute(request=_req(compute_timeframe="1D", until_time=10 * DAY - 1),
                         compute_port=port)

    assert out == []
    assert port.computed_bars == [] and port.projected == []
