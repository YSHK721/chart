"""P-3 前進評価（`forward(C) -> value`）の実装規律を固定する。

§5.5.4: 係数決定に要る前進評価は「窓の末尾バーだけを終値候補で差し替えて増分器を呼ぶ」
形である（参照実装 `tools/measure/issue449/probe_inverse.py:90-118`・ライブ側
`adapter/compute/live_tick_tails.py` の末尾差し替え関数と同じ規律）。

ここで固定するのは**規律**である:
    - 窓は instance ごとに 1 回だけ複製し、以降は末尾行の代入だけを繰り返す
      （毎回 DataFrame を作り直すと 1 ステップの費用が窓長に比例する）。
    - 走行極値は `H = max(H0, C)` / `L = min(L0, C)`（domain の Bar / 参照実装と同一規約）。
    - 増分器を持たない指標は**明示的に落とす**（黙って窓全体の再計算へ落ちない）。

値そのものの一致は `test_forward_matches_reference_probe.py` が参照実装と突き合わせる。
"""
from __future__ import annotations

from types import SimpleNamespace

import pandas as pd
import pytest

from dashboard_ui.adapter.gateway.forward_evaluation_gateway import (
    ForwardEvaluationGateway,
    MissingIncrementalError,
)

REF = "jp225_tick"
START = 1_787_003_400
H0 = 113.0
L0 = 93.0


def frame() -> pd.DataFrame:
    index = pd.to_datetime([START, START + 60], unit="s")
    return pd.DataFrame(
        {"open": [100.0, 103.0], "high": [110.0, H0], "low": [90.0, L0],
         "close": [105.0, 108.0], "volume": [10.0, 11.0]},
        index=index,
    )


class BridgeSpy:
    """dataset ＋ latest 計算面の Test Spy（受け取った窓をそのまま記録する）。"""

    def __init__(self, series=None) -> None:
        self.loaded: "list[tuple[str, str]]" = []
        self.calls: "list[tuple[int, float, float, float]]" = []
        self.frames: "list[int]" = []
        self._series = series if series is not None else [
            {"name": "ma_marod", "kind": "line",
             "data": [{"time": START, "value": 1.0}, {"time": START + 60, "value": 2.0}]}
        ]

    def is_known(self, ref) -> bool:
        return ref == REF

    def is_known_timeframe(self, timeframe) -> bool:
        return timeframe in {"1m", "5m"}

    def load_dataframe(self, ref, timeframe=None):
        self.loaded.append((ref, timeframe))
        return frame()

    def latest_compute(self, adapter, indicator, variant, df, params):
        self.frames.append(id(df))
        self.calls.append((len(df), float(df["close"].iloc[-1]),
                           float(df["high"].iloc[-1]), float(df["low"].iloc[-1])))
        self.received = df.copy()
        return self._series

    def namespace(self) -> SimpleNamespace:
        return SimpleNamespace(
            dataset=self, adapter=object(), latest_compute=self.latest_compute
        )


def gateway_of(spy: BridgeSpy, *, incremental: bool = True) -> ForwardEvaluationGateway:
    return ForwardEvaluationGateway(
        bridge=spy.namespace(),
        value_series_of=lambda indicator_id, variant, params: "ma_marod",
        is_incremental=lambda indicator_id, variant, params: incremental,
    )


def evaluate(gateway: ForwardEvaluationGateway, close: float) -> float:
    return gateway.value_at_close(
        indicator_id="ma_marod", variant="default", params={}, dataset_ref=REF,
        timeframe="1m", close=close,
    )


def test_the_value_comes_from_the_declared_series_tail() -> None:
    spy = BridgeSpy()

    value = evaluate(gateway_of(spy), 100.0)

    assert value == 2.0


def test_only_the_last_bar_is_replaced() -> None:
    """確定した過去は動かさない（因果境界）。差し替えるのは形成中バーの 1 行だけ。"""
    spy = BridgeSpy()

    evaluate(gateway_of(spy), 100.0)

    assert list(spy.received["close"]) == [105.0, 100.0]
    assert list(spy.received["open"]) == [100.0, 103.0]


@pytest.mark.parametrize(
    "close, expected_high, expected_low",
    [
        (H0 + 5.0, H0 + 5.0, L0),      # 終値候補が高値を越えれば高値も動く
        (L0 - 5.0, H0, L0 - 5.0),      # 安値を割れば安値も動く
        (100.0, H0, L0),               # 区分の内側では走行極値は動かない
        (H0, H0, L0),                  # 境界値: 走行 H に一致（越えていない）
        (L0, H0, L0),                  # 境界値: 走行 L に一致
    ],
)
def test_the_running_extremes_follow_the_close_candidate(
    close: float, expected_high: float, expected_low: float
) -> None:
    spy = BridgeSpy()

    evaluate(gateway_of(spy), close)

    assert spy.calls[-1] == (2, close, expected_high, expected_low)


def test_the_window_is_copied_once_per_instance() -> None:
    """窓の複製は 1 回。以降は末尾行の代入だけ（費用が窓長に比例しない）。"""
    spy = BridgeSpy()
    gateway = gateway_of(spy)

    for close in (95.0, 100.0, 120.0):
        evaluate(gateway, close)

    assert spy.loaded == [(REF, "1m")]
    assert len(set(spy.frames)) == 1


def test_every_evaluation_issues_exactly_one_computation() -> None:
    """発行 − 使用 = 0（前進評価はこの面からしか出ない・§7 の Spy が数える唯一の面）。"""
    spy = BridgeSpy()
    gateway = gateway_of(spy)
    closes = (95.0, 100.0, 120.0, 130.0)

    values = [evaluate(gateway, close) for close in closes]

    assert len(spy.calls) - len(values) == 0


def test_an_indicator_without_an_incremental_engine_is_rejected() -> None:
    """増分器不在は明示エラー（窓全体の再計算へ黙って落ちない・§7）。"""
    spy = BridgeSpy()

    with pytest.raises(MissingIncrementalError, match="ma_marod"):
        evaluate(gateway_of(spy, incremental=False), 100.0)

    assert spy.calls == []


def test_a_missing_value_series_is_rejected() -> None:
    """宣言した系列が返らないときも黙って NaN を返さない。"""
    spy = BridgeSpy(series=[{"name": "other", "kind": "line", "data": []}])

    with pytest.raises(ValueError, match="ma_marod"):
        evaluate(gateway_of(spy), 100.0)
