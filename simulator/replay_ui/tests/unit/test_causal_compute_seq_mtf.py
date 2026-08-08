"""足内一括計算の上位足対応はライブと同一設計（ISSUE-290）。

ライブ（ISSUE-274 D-4）は `/live_ticks` を**計算足ごとのグループ処理**にし、その足の形成中バーへ
畳んでから計算する。リプレイの足内一括計算（mode=latest_seq）は同じことを、ティックの代わりに
足内スナップショット（C 足の暫定 OHLC）から行う:

  1. H の**確定足だけ**を窓に採る（データ由来の進行中 H 足は期間全体の OHLC＝未来を含むため捨てる）
  2. 進行中 H 足を「リビール T までの C 確定足 ＋ その時点のスナップショット」から畳んで作る
  3. その窓で latest 計算し、値を C の形成足時刻へ載せる
"""
from __future__ import annotations

from simulator.replay_ui.usecase.causal_compute import CausalComputeSeqRequest, causal_compute_seq

HOUR = 3600
DAY = 86400


class _Port:
    def __init__(self) -> None:
        self.windows: "list[list[dict]]" = []

    def load_source(self, ref, timeframe):
        if timeframe == "1D":
            # 10日目は「期間全体」の OHLC（データ由来＝未来を含む）。捨てられることを検証する。
            return [
                {"time": 9 * DAY, "open": 1, "high": 2, "low": 0.5, "close": 1.5, "volume": 10},
                {"time": 10 * DAY, "open": 2, "high": 99, "low": 0.1, "close": 50, "volume": 99},
            ]
        return [
            {"time": 10 * DAY - 2 * HOUR, "open": 2, "high": 3, "low": 1.8, "close": 2.5, "volume": 1},
            {"time": 10 * DAY - HOUR, "open": 2.5, "high": 4, "low": 2.4, "close": 3.0, "volume": 2},
        ]

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
        return [{"name": "MA", "kind": "line", "data": [{"time": bars[-1]["time"], "value": bars[-1]["close"]}]}]

    def compute_latest_seq(self, indicator, variant, prefix_bars, tails, params):
        """従来経路（チャート足）の一括計算。窓を記録して形だけ返す。"""
        self.windows.extend([[*prefix_bars, *t] for t in tails])
        return [[{"name": "MA", "kind": "line", "data": [{"time": 0, "value": 1.0}]}] for _ in tails]

    def project(self, series, chart_times, compute_tf):        # 本経路では使わない
        raise AssertionError("足内は投影ではなく H 形成足で計算する")


def _req(**over):
    base = dict(indicator="moving_averages", variant="default", ref="jp225_tick",
                timeframe="1h", limit=None, until_time=10 * DAY - HOUR,
                forming_seq=[
                    {"time": 10 * DAY - HOUR, "open": 2.5, "high": 3.2, "low": 2.4, "close": 3.1, "to": 10 * DAY - HOUR + 600},
                    {"time": 10 * DAY - HOUR, "open": 2.5, "high": 5.0, "low": 2.4, "close": 4.8, "to": 10 * DAY - HOUR + 1200},
                ],
                params={}, compute_timeframe="1D")
    base.update(over)
    return CausalComputeSeqRequest(**base)


def test_uses_reconstructed_forming_h_bar_not_the_dataset_one():
    port = _Port()

    out = causal_compute_seq(request=_req(), compute_port=port)

    assert len(out) == 2, "各時点ぶん返す"
    for window in port.windows:
        last = window[-1]
        assert last["time"] == 10 * DAY, "進行中 H 足の time は当該期間"
        assert last["high"] != 99, "データ由来の進行中 H 足（未来入り）を使っている"
        assert window[0]["time"] == 9 * DAY, "確定 H 足は窓に残る"


def test_forming_h_bar_accumulates_the_intrabar_snapshot():
    port = _Port()

    causal_compute_seq(request=_req(), compute_port=port)

    first, second = port.windows[0][-1], port.windows[1][-1]
    assert first["open"] == 2, "open は当該 H 期間の最初の C 足の open"
    assert first["high"] == 3.2 and first["close"] == 3.1
    assert second["high"] == 5.0 and second["close"] == 4.8, "時点が進むと高値・終値が伸びる"


def test_values_are_placed_on_the_chart_forming_bar_time():
    port = _Port()

    out = causal_compute_seq(request=_req(), compute_port=port)

    assert out[0][0]["data"][0]["time"] == 10 * DAY - HOUR, "C の形成足時刻へ載せる"


def test_chart_timeframe_instances_keep_the_existing_path():
    port = _Port()

    out = causal_compute_seq(request=_req(compute_timeframe=None), compute_port=port)

    assert len(out) == 2
    for window in port.windows:
        assert window[0]["time"] == 10 * DAY - 2 * HOUR, "チャート足の窓で計算する（従来経路）"
