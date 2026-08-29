"""上位足計算（計算.時間足）は「各バーの時点で計算できた値」を返す（ISSUE-287〜294）。

経緯（すべて実 UI／実データの実測で確定）:
    ISSUE-287: front の `computeTimeframe` をリプレイ core が無言で捨て、チャート足で計算していた。
    ISSUE-291: H 源の進行中期間の足（保存済みロールアップ＝期間全体の OHLC）をそのまま計算へ
        入れていた＝未来混入（T=08-07 02:00 UTC で当日 high=66700.24 を使用）。
    ISSUE-292: 期間の属否を**ラベル**で判定していた。1D はラベル（暦日の UTC 深夜）と期間始端
        （前日 21:00 UTC）が別物のため、期間前半の C 足が 1 本も選ばれなかった。
    ISSUE-293/294（依頼）: 「過去に確定したラインを更新するな」「過去のデータも固定しろ」。
        点の意味を **value(τ) = τ 時点で計算できた値** へ揃える。これにより系列は時刻不変になり、
        後から塗り替える必要がなくなる（実測: T を 22:20→02:00 へ進めても重なり 1238 点が不変）。

    value(τ) = 指標( [τ の期間より前の確定 H 足] + [τ の期間の C 足を τ まで畳んだ H 足] )

本ファイルの Fake は 1D と同型の「ラベル ≠ 期間始端」を再現する。構造: Arrange-Act-Assert。
"""
from __future__ import annotations

from simulator.replay_ui.usecase.causal_compute import CausalComputeRequest, causal_compute

HOUR = 3600
DAY = 86400

# C（1h）足。10 日目の期間（始端 10*DAY-3h・ラベル 10*DAY）に属する 2 本は **ラベルより前の時刻**。
_CHART = [
    {"time": 9 * DAY - 2 * HOUR, "open": 1, "high": 2, "low": 0.5, "close": 1.5, "volume": 1},
    {"time": 9 * DAY + 1 * HOUR, "open": 1.5, "high": 1.9, "low": 1.2, "close": 1.6, "volume": 1},
    {"time": 10 * DAY - 2 * HOUR, "open": 2, "high": 3, "low": 1.8, "close": 2.5, "volume": 1},
    {"time": 10 * DAY - 1 * HOUR, "open": 2.5, "high": 4, "low": 2.4, "close": 3.0, "volume": 2},
]


class _FakePort:
    """load_source / bar_time / period_start / compute_latest_seq を記録する Port。"""

    def __init__(self) -> None:
        self.loaded: "list[str | None]" = []
        self.calls: "list[tuple[list[dict], list[list[dict]]]]" = []

    def load_source(self, ref: str, timeframe):        # noqa: D401
        self.loaded.append(timeframe)
        if timeframe == "1D":
            # 10 日目は「期間全体」の OHLC（データ由来＝未来を含む）。使われないことを検証する。
            return [
                {"time": 9 * DAY, "open": 1, "high": 2, "low": 0.5, "close": 1.5, "volume": 10},
                {"time": 10 * DAY, "open": 2, "high": 99, "low": 0.1, "close": 50, "volume": 99},
            ]
        return [dict(b) for b in _CHART]

    # ラベル＝期間の右端の深夜／始端＝その 3 時間前（1D セッション足と同型）。
    def bar_time(self, timeframe, unix_sec):
        if timeframe != "1D":
            return int(unix_sec)
        return ((int(unix_sec) + 3 * HOUR) // DAY) * DAY

    def period_start(self, timeframe, unix_sec):
        if timeframe != "1D":
            return int(unix_sec)
        return self.bar_time(timeframe, unix_sec) - 3 * HOUR

    def compute_latest_seq(self, indicator, variant, prefix_bars, tails, params):
        self.calls.append(([dict(b) for b in prefix_bars], [[dict(b) for b in t] for t in tails]))
        # 値＝その時点の形成中 H 足の close（窓が正しいかを値で追えるようにする）。
        return [[{"name": "MA", "kind": "line",
                  "data": [{"time": t[-1]["time"], "value": t[-1]["close"]}]}] for t in tails]

    def causal_series(self, indicator, variant, chart_bars, source_bars, compute_tf,
                      window_bars, params):
        """規約の実体はライブ core と同一の唯一源（写しを持たない）。"""
        from simulator.replay_ui.adapter import _indicator_ui_bridge

        causal_mtf_series = _indicator_ui_bridge.load_compute().causal_mtf_series
        return causal_mtf_series(
            chart_bars=chart_bars, source_bars=source_bars, compute_tf=compute_tf,
            bar_time_unix=self.bar_time,
            latest_seq=lambda prefix, tails: self.compute_latest_seq(
                indicator, variant, prefix, tails, params),
            window_bars=window_bars,
        )

    def compute(self, indicator, variant, mode, bars, params):   # 本経路では使わない
        raise AssertionError("リビール経路は latest_seq（末尾差分）で計算する")

    # ---- 検証補助 ----
    def formings(self):
        """発行順に並べた「その時点の形成中 H 足」。"""
        return [t[-1] for _prefix, tails in self.calls for t in tails]

    def prefixes(self):
        """各 latest_seq 呼び出しの確定 H 足のラベル列。"""
        return [[int(b["time"]) for b in prefix] for prefix, _tails in self.calls]


def _req(**over):
    base = dict(indicator="moving_averages", variant="default", ref="jp225_tick",
                timeframe="1h", limit=None, until_time=None, mode=None, forming=None,
                params={})
    base.update(over)
    return CausalComputeRequest(**base)


def test_each_chart_bar_gets_the_value_computable_at_that_bar():
    port = _FakePort()

    out = causal_compute(request=_req(compute_timeframe="1D"), compute_port=port)

    assert set(port.loaded) == {"1h", "1D"}, "C（時間軸）と H（計算）の両方を読む"
    assert len(out) == 1 and out[0]["stepped"] is True, "期間境界は段で描く（斜線にしない）"
    assert [p["time"] for p in out[0]["data"]] == [b["time"] for b in _CHART], "全 C バーに点が載る"
    assert [p["value"] for p in out[0]["data"]] == [1.5, 1.6, 2.5, 3.0], (
        "各点はそのバーまでで畳んだ H 足の値（期間の確定値ではない）")


def test_forming_h_bar_accumulates_within_the_period_and_resets_at_the_boundary():
    port = _FakePort()

    causal_compute(request=_req(compute_timeframe="1D"), compute_port=port)

    formings = port.formings()
    assert [f["time"] for f in formings] == [9 * DAY, 9 * DAY, 10 * DAY, 10 * DAY], (
        "畳んだ足に載せる time は期間のラベル（属否は始端で判定する）")
    assert [f["high"] for f in formings] == [2, 2, 3, 4], "期間内は累積し、境界で作り直す"
    assert [f["open"] for f in formings] == [1, 1, 2, 2], "open は期間の最初の C 足"


def test_data_derived_in_progress_h_bar_is_never_used():
    """進行中期間の H 足（期間全体＝未来入り）は確定プレフィクスへ入れない（ISSUE-291）。"""
    port = _FakePort()

    causal_compute(request=_req(compute_timeframe="1D"), compute_port=port)

    assert port.prefixes() == [[], [9 * DAY]], "10*DAY の H 足（high=99）は一度も窓に入らない"


def test_causality_is_kept_for_both_timeframes():
    """H・C とも untilTime で切る（リビール T より先の足を計算へ入れない）。"""
    port = _FakePort()

    out = causal_compute(request=_req(compute_timeframe="1D", until_time=10 * DAY - 2 * HOUR),
                         compute_port=port)

    assert [p["time"] for p in out[0]["data"]] == [b["time"] for b in _CHART[:3]], "C が T で切られる"
    assert [f["high"] for f in port.formings()] == [2, 2, 3], "T までの C 足だけで畳む"


def test_limit_window_still_folds_from_the_period_start():
    """窓（limit）が期間の途中から始まっても、畳みは期間の先頭から行う。

    窓外の C 足は畳み ``acc`` へ寄与させるだけで、その時点の指標計算は発行しない
    （発行しても結果は出力に使われず捨てられる＝ISSUE-450 の真因 A）。よってここで固定するのは
    「発行回数」ではなく **渡された畳み足が期間の先頭から畳まれていること**である。窓の 1 本
    だけで畳むと ``open`` が 2.5・``high`` が 4 になるため、``open`` で判別できる。
    ライブ側 ``indigators/indicator_ui/api/tests/test_mtf_causal.py`` の同名テストと対。
    """
    port = _FakePort()

    out = causal_compute(request=_req(compute_timeframe="1D", limit=1), compute_port=port)

    assert [p["time"] for p in out[0]["data"]] == [_CHART[-1]["time"]], "出力は窓ぶんだけ"

    formings = port.formings()
    assert len(formings) == 1, "窓外のバーぶんの計算は発行しない（捨てる計算を作らない）"
    assert formings[0]["open"] == 2, "畳みは期間の先頭 C 足から（窓の 1 本だけで畳んでいない）"
    assert formings[0]["high"] == 4, "畳みは窓に縛られない"
    assert formings[0]["time"] == 10 * DAY, "畳んだ足に載せる time は期間のラベル"


def test_no_projection_when_compute_timeframe_is_absent_or_chart():
    for value in (None, "chart", "1h"):
        port = _FakePort()
        try:
            causal_compute(request=_req(compute_timeframe=value), compute_port=port)
        except AssertionError as exc:                    # compute（従来経路）が呼ばれる
            assert "リビール経路" in str(exc)
        assert port.loaded == ["1h"], f"{value!r} では C だけを読む"


def test_empty_chart_window_returns_no_series():
    port = _FakePort()

    out = causal_compute(request=_req(compute_timeframe="1D", until_time=9 * DAY - 3 * HOUR),
                         compute_port=port)

    assert out == []
    assert port.calls == []
