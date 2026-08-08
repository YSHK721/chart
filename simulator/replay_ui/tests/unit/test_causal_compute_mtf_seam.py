"""リビール経路と足内経路は、同じ瞬間に同じ H 窓を作る（ISSUE-291 / 294）。

実 UI で観測された不具合（2026-08-08・5m チャート × 計算.時間足 1D の EMA5）:

    末尾3点: …:66098.5467  …:66098.5467  …:64970.3855   （high 由来・最大跳躍 1128.16）
    末尾3点: …:64163.5843  …:64163.5843  …:64800.2296   （low 由来・最大跳躍 636.65）

古い 2 点はリビール経路、末尾 1 点は足内経路（latest_seq）が書いた値で、high 側は下へ・
low 側は上へ跳ねて交差していた。リビール側が H 源の進行中期間の足（期間全体の OHLC＝未来）を
使っていたためである。

固定すべき性質は「同じ瞬間なら経路によらず同じ窓」。窓の素材（確定 H 足＋C から畳んだ形成 H 足）が
同じ規則で作られている限りこの性質は保たれ、片側だけを変えると Red になる。構造: AAA。
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

#: 進行中 H 期間（ラベル 10*DAY・始端 10*DAY-3h）の C 足。最後の 1 本がリビール時点の足。
_CHART_BARS = [
    {"time": 9 * DAY, "open": 1, "high": 2, "low": 0.5, "close": 1.5, "volume": 1},
    {"time": 10 * DAY - 2 * HOUR, "open": 2, "high": 3, "low": 1.8, "close": 2.5, "volume": 1},
    {"time": 10 * DAY - HOUR, "open": 2.5, "high": 4, "low": 2.4, "close": 3.0, "volume": 2},
]


class _Port:
    """両経路の H 窓（確定＋形成）を記録する Port。"""

    def __init__(self) -> None:
        self.windows: "list[list[dict]]" = []

    def load_source(self, ref, timeframe):
        if timeframe == "1D":
            # ラベル 10*DAY は期間全体の OHLC（＝リビール T より先を含む）。
            return [
                {"time": 9 * DAY, "open": 1, "high": 2, "low": 0.5, "close": 1.5, "volume": 10},
                {"time": 10 * DAY, "open": 2, "high": 99, "low": 0.1, "close": 50, "volume": 99},
            ]
        return [dict(b) for b in _CHART_BARS]

    # ラベル＝期間の右端の深夜／始端＝その 3 時間前（1D セッション足と同型・ISSUE-292）。
    def bar_time(self, timeframe, unix_sec):
        if timeframe != "1D":
            return int(unix_sec)
        return ((int(unix_sec) + 3 * HOUR) // DAY) * DAY

    def period_start(self, timeframe, unix_sec):
        if timeframe != "1D":
            return int(unix_sec)
        return self.bar_time(timeframe, unix_sec) - 3 * HOUR

    def compute(self, indicator, variant, mode, bars, params):
        self.windows.append([dict(b) for b in bars])
        return [{"name": "MA", "kind": "line",
                 "data": [{"time": bars[-1]["time"], "value": bars[-1]["close"]}]}]

    def compute_latest_seq(self, indicator, variant, prefix_bars, tails, params):
        out = []
        for tail in tails:
            self.windows.append([dict(b) for b in [*prefix_bars, *tail]])
            out.append([{"name": "MA", "kind": "line",
                         "data": [{"time": tail[-1]["time"], "value": tail[-1]["close"]}]}])
        return out


def test_reveal_and_intrabar_paths_build_the_same_h_window():
    """足内の最終スナップショット＝確定した C 足のとき、両経路の窓は完全一致する。"""
    until = 10 * DAY - HOUR
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

    assert reveal_port.windows[-1] == seq_port.windows[-1], (
        "同じ瞬間に窓が食い違う＝表示が段差になる")


def test_reveal_path_does_not_see_beyond_each_bar():
    """各バーの窓に、そのバーより先の情報が入らない（ISSUE-291 / 294）。"""
    port = _Port()

    causal_compute(
        request=CausalComputeRequest(
            indicator="moving_averages", variant="default", ref="jp225_tick", timeframe="1h",
            limit=None, until_time=10 * DAY - HOUR, mode=None, forming=None, params={},
            compute_timeframe="1D"),
        compute_port=port)

    formings = [w[-1] for w in port.windows]
    assert [f["high"] for f in formings] == [2, 3, 4], "そのバーまでの C 足だけで畳む"
    assert all(b["high"] != 99 for w in port.windows for b in w), (
        "データ由来の進行中 H 足（当期間全体）を見ている＝未来参照")
