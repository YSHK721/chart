"""上位足計算（計算.時間足）がリプレイでも投影される（ISSUE-287 / 291 / 292）。

ISSUE-287: front が送る `computeTimeframe` をリプレイ core が無言で捨て、チャート足で計算していた。
ISSUE-291: H 源の**進行中期間の足**をそのまま計算へ入れていた（保存済みロールアップは進行中でも
    期間全体の OHLC を持つ＝未来混入）。実測 T=2026-08-07 02:00 UTC で日足 high=66700.24 を使用。
ISSUE-292: 進行中期間に属する C 足の判定を**ラベル**で行っていた。1D はラベル（暦日の UTC 深夜）と
    期間始端（前日 21:00 UTC）が別物のため、期間の前半（ラベルより前の時刻）に属する C 足が
    1 本も選ばれず、形成中 H 足が作られないまま確定足だけで計算していた。

本ファイルの Fake はこの**ラベル ≠ 期間始端**を再現する（label = 期間右端の深夜 / start = label-3h）。
構造: Arrange-Act-Assert（AAA）。
"""
from __future__ import annotations

from simulator.replay_ui.usecase.causal_compute import CausalComputeRequest, causal_compute

HOUR = 3600
DAY = 86400

# C（1h）足。10 日目の期間（始端 10*DAY-3h・ラベル 10*DAY）に属する 2 本は **ラベルより前の時刻**。
_CHART = [
    {"time": 9 * DAY - 2 * HOUR, "open": 1, "high": 2, "low": 0.5, "close": 1.5, "volume": 1},
    {"time": 9 * DAY + 1 * HOUR, "open": 1.5, "high": 1.9, "low": 1.2, "close": 1.5, "volume": 1},
    {"time": 10 * DAY - 2 * HOUR, "open": 2, "high": 3, "low": 1.8, "close": 2.5, "volume": 1},
    {"time": 10 * DAY - 1 * HOUR, "open": 2.5, "high": 4, "low": 2.4, "close": 3.0, "volume": 2},
]


class _FakePort:
    """load_source / bar_time / period_start / compute / project を記録する Port。"""

    def __init__(self) -> None:
        self.loaded: "list[str | None]" = []
        self.computed_bars: "list[list[dict]]" = []
        self.projected: "list[tuple[list[int], str]]" = []

    def load_source(self, ref: str, timeframe):        # noqa: D401
        self.loaded.append(timeframe)
        if timeframe == "1D":
            # 10 日目は「期間全体」の OHLC（データ由来＝未来を含む）。捨てられることを検証する。
            return [
                {"time": 9 * DAY, "open": 1, "high": 2, "low": 0.5, "close": 1.5, "volume": 10},
                {"time": 10 * DAY, "open": 2, "high": 99, "low": 0.1, "close": 50, "volume": 99},
            ]
        return [dict(b) for b in _CHART]

    # ラベル＝期間の右端の深夜（1D セッション足の実測規約と同型）。
    def bar_time(self, timeframe, unix_sec):
        if timeframe != "1D":
            return int(unix_sec)
        return ((int(unix_sec) + 3 * HOUR) // DAY) * DAY

    # 期間始端＝ラベルの 3 時間前（ラベルとは別物であることを固定する）。
    def period_start(self, timeframe, unix_sec):
        if timeframe != "1D":
            return int(unix_sec)
        return self.bar_time(timeframe, unix_sec) - 3 * HOUR

    def compute(self, indicator, variant, mode, bars, params):
        self.computed_bars.append([dict(b) for b in bars])
        return [{"name": "MA", "kind": "line",
                 "data": [{"time": b["time"], "value": 1.0} for b in bars]}]

    def project(self, series, chart_times, compute_tf):
        self.projected.append((list(chart_times), compute_tf))
        return [{**s, "data": [{"time": t, "value": 9.9} for t in chart_times]} for s in series]


def _req(**over):
    base = dict(indicator="moving_averages", variant="default", ref="jp225_tick",
                timeframe="1h", limit=None, until_time=None, mode=None, forming=None,
                params={})
    base.update(over)
    return CausalComputeRequest(**base)


def test_compute_timeframe_computes_on_h_and_projects_onto_chart_times():
    port = _FakePort()

    out = causal_compute(request=_req(compute_timeframe="1D"), compute_port=port)

    assert set(port.loaded) == {"1h", "1D"}, "C（時間軸）と H（計算）の両方を読む"
    assert [b["time"] for b in port.computed_bars[0]] == [9 * DAY, 10 * DAY], "計算は H の時間軸"
    chart_times, tf = port.projected[0]
    assert tf == "1D"
    assert chart_times == [b["time"] for b in _CHART], "投影先は C のバー時刻"
    assert [p["time"] for p in out[0]["data"]] == chart_times, "応答の時刻は C の時間軸"


def test_in_progress_period_is_decided_by_period_start_not_by_the_label():
    """ラベルより前の時刻に属する C 足も進行中期間として畳む（ISSUE-292）。"""
    port = _FakePort()

    causal_compute(request=_req(compute_timeframe="1D"), compute_port=port)

    forming_h = port.computed_bars[0][-1]
    assert forming_h["time"] == 10 * DAY, "畳んだ足に載せる time は期間のラベル"
    assert forming_h["open"] == 2, "始端(10*DAY-3h)以降の最初の C 足の open"
    assert forming_h["high"] == 4 and forming_h["low"] == 1.8 and forming_h["close"] == 3.0, (
        "ラベルで属否を判定すると C 足が 1 本も選ばれず、この足自体が作られない")


def test_in_progress_h_bar_is_folded_from_chart_bars_not_taken_from_the_source():
    """進行中 H 足はデータ由来（期間全体＝未来入り）を使わず、C 足から畳む（ISSUE-291）。"""
    port = _FakePort()

    causal_compute(request=_req(compute_timeframe="1D"), compute_port=port)

    forming_h = port.computed_bars[0][-1]
    assert forming_h["high"] != 99 and forming_h["close"] != 50, "データ由来の未来入り足を使っている"
    assert port.computed_bars[0][0]["high"] == 2, "確定 H 足は源のまま残る"


def test_causality_is_kept_for_both_timeframes():
    """H・C とも untilTime で切る（リビール T より先の足を計算へ入れない）。"""
    port = _FakePort()

    causal_compute(request=_req(compute_timeframe="1D", until_time=10 * DAY - 2 * HOUR),
                   compute_port=port)

    assert [b["time"] for b in port.computed_bars[0]] == [9 * DAY, 10 * DAY], "H が T で切られていない"
    forming_h = port.computed_bars[0][-1]
    assert forming_h["high"] == 3 and forming_h["close"] == 2.5, "T までの C 足だけで畳む"
    chart_times, _ = port.projected[0]
    assert chart_times == [b["time"] for b in _CHART[:3]], "C も T で切られる"


def test_no_projection_when_compute_timeframe_is_absent_or_chart():
    for value in (None, "chart", "1h"):
        port = _FakePort()

        causal_compute(request=_req(compute_timeframe=value), compute_port=port)

        assert port.projected == [], f"{value!r} では投影しない（従来経路）"
        assert port.loaded == ["1h"], f"{value!r} では C だけを読む"


def test_empty_chart_window_returns_no_series():
    """C が空（T より前のバーが無い）なら計算も投影もせず空を返す。"""
    port = _FakePort()

    out = causal_compute(request=_req(compute_timeframe="1D", until_time=9 * DAY - 3 * HOUR),
                         compute_port=port)

    assert out == []
    assert port.computed_bars == [] and port.projected == []
