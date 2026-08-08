"""リビール経路と足内経路は、同じ瞬間に同じ計算窓を作る（ISSUE-291）。

実 UI で観測された不具合（2026-08-08・5m チャート × 計算.時間足 1D の EMA5）:

    末尾3点: …:66098.5467  …:66098.5467  …:64970.3855   （high 由来・最大跳躍 1128.16）
    末尾3点: …:64163.5843  …:64163.5843  …:64800.2296   （low 由来・最大跳躍 636.65）

古い 2 点はリビール経路（full＋投影）、末尾 1 点は足内経路（latest_seq）が書いた値で、
high 側は下へ・low 側は上へ跳ねて交差していた。原因はリビール経路だけが H 源の
**進行中期間の足**（保存済みロールアップ＝その期間の全 OHLC）を計算へ入れていたこと。
実測: リビール T=02:00 UTC に対し日足 high=66700.24 / low=64679 / close=66304.97（当日全体）。

したがって固定すべき性質は「同じ瞬間なら経路によらず同じ窓」。窓の作り方が 1 箇所
（``_causal_h_window``）に閉じている限りこの性質は保たれ、片側だけを直すと Red になる。
構造: Arrange-Act-Assert（AAA）。
"""
from __future__ import annotations

from simulator.replay_ui.usecase.causal_compute import (
    CausalComputeRequest,
    CausalComputeSeqRequest,
    causal_compute,
    causal_compute_seq,
)

HOUR = 3600
DAY = 86400

#: 進行中 H 期間（10 日目）の C 足。最後の 1 本がリビール時点の足。
_CHART_BARS = [
    {"time": 9 * DAY, "open": 1, "high": 2, "low": 0.5, "close": 1.5, "volume": 1},
    {"time": 10 * DAY, "open": 2, "high": 3, "low": 1.8, "close": 2.5, "volume": 1},
    {"time": 10 * DAY + HOUR, "open": 2.5, "high": 4, "low": 2.4, "close": 3.0, "volume": 2},
]


class _Port:
    def __init__(self) -> None:
        self.windows: "list[list[dict]]" = []

    def load_source(self, ref, timeframe):
        if timeframe == "1D":
            # 10 日目は期間全体の OHLC（＝リビール T より先を含む）。
            return [
                {"time": 9 * DAY, "open": 1, "high": 2, "low": 0.5, "close": 1.5, "volume": 10},
                {"time": 10 * DAY, "open": 2, "high": 99, "low": 0.1, "close": 50, "volume": 99},
            ]
        return [dict(b) for b in _CHART_BARS]

    def bar_time(self, timeframe, unix_sec):
        return (int(unix_sec) // DAY) * DAY if timeframe == "1D" else int(unix_sec)

    def compute(self, indicator, variant, mode, bars, params):
        self.windows.append([dict(b) for b in bars])
        return [{"name": "MA", "kind": "line",
                 "data": [{"time": bars[-1]["time"], "value": bars[-1]["close"]}]}]

    def project(self, series, chart_times, compute_tf):
        return [{**s, "data": [{"time": t, "value": 1.0} for t in chart_times]} for s in series]


def test_reveal_and_intrabar_paths_build_the_same_h_window():
    """足内の最終スナップショット＝確定した C 足のとき、両経路の窓は完全一致する。"""
    until = 10 * DAY + HOUR
    reveal_port, seq_port = _Port(), _Port()

    causal_compute(
        request=CausalComputeRequest(
            indicator="moving_averages", variant="default", ref="jp225_tick", timeframe="1h",
            limit=None, until_time=until, mode=None, forming=None, params={},
            compute_timeframe="1D"),
        compute_port=reveal_port)
    causal_compute_seq(
        request=CausalComputeSeqRequest(
            indicator="moving_averages", variant="default", ref="jp225_tick", timeframe="1h",
            limit=None, until_time=until, forming_seq=[dict(_CHART_BARS[-1])], params={},
            compute_timeframe="1D"),
        compute_port=seq_port)

    assert reveal_port.windows[0] == seq_port.windows[0], "同じ瞬間に窓が食い違う＝表示が段差になる"


def test_reveal_path_does_not_see_beyond_the_reveal_time():
    """リビール経路の進行中 H 足に、T より先の高値・安値・終値が入らない。"""
    reveal_port = _Port()

    causal_compute(
        request=CausalComputeRequest(
            indicator="moving_averages", variant="default", ref="jp225_tick", timeframe="1h",
            limit=None, until_time=10 * DAY, mode=None, forming=None, params={},
            compute_timeframe="1D"),
        compute_port=reveal_port)

    forming_h = reveal_port.windows[0][-1]
    assert forming_h["high"] == 3, "当日全体の高値（99）を見ている＝未来参照"
    assert forming_h["low"] == 1.8 and forming_h["close"] == 2.5
