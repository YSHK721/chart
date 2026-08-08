"""上位足計算（計算.時間足）がリプレイでも投影される（ISSUE-287 / ISSUE-291）。

ISSUE-287 で起きていたこと（2026-08-08 実 HTTP で検出）:
    front は `/compute` へ `computeTimeframe` を送るが、リプレイ core の usecase は
    それを受け取る口を持たず**無言で捨てて**チャート足で計算していた。同一リクエストで
    ライブ core は 2 段の階段（日境界）、リプレイ core は 600 段（5m のまま）＝別物。

ISSUE-291 で起きていたこと（同日・実 UI で検出）:
    リビール経路は H 源の**進行中期間の足**をそのまま計算へ入れていた。リプレイの H 源
    （保存済みロールアップ）は進行中期間でも「その期間の全 OHLC」を持つため、リビール T
    より先の高値・安値・終値＝未来が窓に入っていた。実測（5m×1D EMA5）: 当日 02:00 UTC 時点で
    日足の high=66700.24 / close=66304.97（当日全体）を使用。足内経路は C 足から畳んだ
    形成中 H 足を使うため、同じ瞬間に 1128 の段差が出た。

規約はライブ core と同一（`adapter.compute.mtf_projection` が唯一源）で、本 usecase は
入力を合わせて Port へ渡すだけ。ここでは「確定 H 足＋C から畳んだ進行中 H 足で計算し、
C の時刻へ投影する」結線を固定する。構造: Arrange-Act-Assert（AAA）。
"""
from __future__ import annotations

from simulator.replay_ui.usecase.causal_compute import CausalComputeRequest, causal_compute

HOUR = 3600
DAY = 86400


class _FakePort:
    """load_source / bar_time / compute / project を記録する Port（実計算に依存しない）。"""

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
        return [
            {"time": 9 * DAY, "open": 1, "high": 2, "low": 0.5, "close": 1.5, "volume": 1},
            {"time": 9 * DAY + HOUR, "open": 1.5, "high": 1.9, "low": 1.2, "close": 1.5, "volume": 1},
            {"time": 10 * DAY, "open": 2, "high": 3, "low": 1.8, "close": 2.5, "volume": 1},
            {"time": 10 * DAY + HOUR, "open": 2.5, "high": 4, "low": 2.4, "close": 3.0, "volume": 2},
        ]

    def bar_time(self, timeframe, unix_sec):
        return (int(unix_sec) // DAY) * DAY if timeframe == "1D" else int(unix_sec)

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
    assert chart_times == [9 * DAY, 9 * DAY + HOUR, 10 * DAY, 10 * DAY + HOUR], "投影先は C のバー時刻"
    assert [p["time"] for p in out[0]["data"]] == chart_times, "応答の時刻は C の時間軸"


def test_in_progress_h_bar_is_folded_from_chart_bars_not_taken_from_the_source():
    """進行中 H 足はデータ由来（期間全体＝未来入り）を使わず、C 足から畳む（ISSUE-291）。"""
    port = _FakePort()

    causal_compute(request=_req(compute_timeframe="1D"), compute_port=port)

    forming_h = port.computed_bars[0][-1]
    assert forming_h["time"] == 10 * DAY, "進行中 H 足の time は当該期間"
    assert forming_h["high"] != 99 and forming_h["close"] != 50, "データ由来の未来入り足を使っている"
    assert forming_h["open"] == 2, "open は当該 H 期間の最初の C 足の open"
    assert forming_h["high"] == 4 and forming_h["low"] == 1.8 and forming_h["close"] == 3.0
    assert port.computed_bars[0][0]["high"] == 2, "確定 H 足は源のまま残る"


def test_causality_is_kept_for_both_timeframes():
    """H・C とも untilTime で切る（リビール T より先の足を計算へ入れない）。"""
    port = _FakePort()

    causal_compute(request=_req(compute_timeframe="1D", until_time=10 * DAY),
                   compute_port=port)

    times = [b["time"] for b in port.computed_bars[0]]
    assert times == [9 * DAY, 10 * DAY], "H が T で切られていない"
    forming_h = port.computed_bars[0][-1]
    assert forming_h["high"] == 3 and forming_h["close"] == 2.5, "T までの C 足だけで畳む"
    chart_times, _ = port.projected[0]
    assert chart_times == [9 * DAY, 9 * DAY + HOUR, 10 * DAY], "C も T で切られる"


def test_no_projection_when_compute_timeframe_is_absent_or_chart():
    for value in (None, "chart", "1h"):
        port = _FakePort()

        causal_compute(request=_req(compute_timeframe=value), compute_port=port)

        assert port.projected == [], f"{value!r} では投影しない（従来経路）"
        assert port.loaded == ["1h"], f"{value!r} では C だけを読む"


def test_empty_chart_window_returns_no_series():
    """C が空（T より前のバーが無い）なら計算も投影もせず空を返す。"""
    port = _FakePort()

    out = causal_compute(request=_req(compute_timeframe="1D", until_time=9 * DAY - 1),
                         compute_port=port)

    assert out == []
    assert port.computed_bars == [] and port.projected == []
