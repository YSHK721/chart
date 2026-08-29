"""P-3 の値が**参照実装と一致する**ことを固定する（arch-spec §8・絶対遵守）。

参照実装は `tools/measure/issue449/probe_inverse.py:90-118` の前進評価であり、各指標の core を
無改変で直接呼ぶ形である。`ForwardEvaluationGateway` は同じ入力に対して**同じ値**を返さねば
ならない。ここが一致しないと、§5.5 の価格投影は「別の指標の逆写像」を描くことになる。

本検定は fake を挟まない（実データ `jp225_tick`・実 core・実増分器）。期待値は被検査コードの
式からではなく**参照実装の直接呼び出し**から作る（§7.1「どう書くか」の 3 つの失敗形を踏まない）。

単調性（§5.5.2「全区分で単調増加」）も同じ素材で固定する。ここが崩れると価格の交差による
到達判定と指標値の交差の同値性（§6.1）が失われる。
"""
from __future__ import annotations

import importlib

import numpy as np
import pandas as pd
import pytest

from dashboard_ui.adapter.gateway.forward_evaluation_gateway import (
    ForwardEvaluationGateway,
)
from dashboard_ui.adapter.series_role_table import SeriesRoleTable
from dashboard_ui.usecase.sheet_models import SheetInstance

REF = "jp225_tick"
TIMEFRAME = "5m"
#: 参照実装が前進評価に使う直近バー数（`probe_inverse.py:44` の `NPROBE`）。
NPROBE = 600

#: 実設定に合わせた 3 指標（§5.5.1 の「価格へ逆算できる」3 種）。
CASES = {
    "ma_marod": {"source": "hlc3", "ma_type": "ema", "length": 50},
    "btlm_trail_marod": {"source": "hlc3", "maxbars": 300},
    "profit_rsi": {"rsi_period": 6, "apply": 5},
}


@pytest.fixture(scope="module")
def bridge():
    from simulator.replay_ui.adapter import _indicator_ui_bridge

    return _indicator_ui_bridge.load_compute()


@pytest.fixture(scope="module")
def window(bridge) -> pd.DataFrame:
    return bridge.dataset.load_dataframe(REF, TIMEFRAME).tail(NPROBE)


@pytest.fixture(scope="module")
def reference_forward(bridge):
    """参照実装（各指標の core を直接呼ぶ）— `probe_inverse.py:90-118` と同じ形。"""
    from adapter.compute.call_binding import indicator_src

    # 束縛名を指標 id と同名にしない（宣言整合性検定の記号索引はリポジトリ全体で 1 つで、
    # ここで束縛すると他モジュールの散文中の同名語まで「実在する記号」に変わるため）。
    marod = importlib.import_module(indicator_src("ma_marod").__name__ + ".core")
    btlm = importlib.import_module(indicator_src("btlm_trail_marod").__name__ + ".core")
    rsi = importlib.import_module(indicator_src("profit_rsi").__name__ + ".core")

    def forward(frame: pd.DataFrame, indicator_id: str, params: dict, close: float) -> float:
        # 素材の配列は読み取り専用で供給される（`dataset` が writeable=False を立てる）ため、
        # 末尾を差し替える前に必ず複製する（参照実装 probe_inverse.py:102 の `copy()` と同じ）。
        open_ = np.array(frame["open"], dtype=np.float64)
        high = np.array(frame["high"], dtype=np.float64)
        low = np.array(frame["low"], dtype=np.float64)
        closes = np.array(frame["close"], dtype=np.float64)
        high[-1] = max(high[-1], close)     # 形成中バーの走行極値（C が越えれば高値も動く）
        low[-1] = min(low[-1], close)
        closes[-1] = close
        if indicator_id == "ma_marod":
            candles = pd.DataFrame({"open": open_, "high": high, "low": low, "close": closes})
            return float(marod.ma_marod_series(
                candles, source=params["source"], ma_type=params["ma_type"],
                length=int(params["length"]))[-1])
        if indicator_id == "btlm_trail_marod":
            candles = pd.DataFrame({"open": open_, "high": high, "low": low, "close": closes})
            return float(btlm.marod_series(
                candles, source=params["source"], maxbars=int(params["maxbars"]))[-1])
        result = rsi.compute_rsi_full(
            open_, high, low, closes, rsi_period=int(params["rsi_period"]),
            apply=int(params["apply"]))
        return float(np.asarray(result.rsi)[-1])

    return forward


@pytest.fixture(scope="module")
def gateway() -> ForwardEvaluationGateway:
    table = SeriesRoleTable()

    def value_series_of(indicator_id: str, variant: str, params) -> str:
        instance = SheetInstance.of(indicator_id, variant, dict(params),
                                    chart_timeframe=TIMEFRAME)
        return table.oscillator_spec(
            instance=instance, series_names=frozenset()
        ).value_series

    return ForwardEvaluationGateway(
        value_series_of=value_series_of, bar_limits={TIMEFRAME: NPROBE}
    )


def close_candidates(window: pd.DataFrame) -> "list[float]":
    """区分をまたぐ終値候補（`C < L` / 区分の内側 / 境界 / `C > H`）。"""
    high = float(window["high"].iloc[-1])
    low = float(window["low"].iloc[-1])
    span = max(high - low, 1.0)
    return [low - 2.0 * span, low, (low + high) / 2.0, high, high + 2.0 * span]


@pytest.mark.parametrize("indicator_id", sorted(CASES))
def test_the_gateway_returns_the_reference_value(
    indicator_id: str, gateway, reference_forward, window
) -> None:
    params = CASES[indicator_id]

    for close in close_candidates(window):
        expected = reference_forward(window.copy(), indicator_id, params, close)
        actual = gateway.value_at_close(
            indicator_id=indicator_id, variant="default", params=params,
            dataset_ref=REF, timeframe=TIMEFRAME, close=close,
        )

        assert actual == pytest.approx(expected, abs=1e-9), (indicator_id, close)


@pytest.mark.parametrize("indicator_id", sorted(CASES))
def test_the_forward_evaluation_is_increasing_in_the_close(
    indicator_id: str, gateway, window
) -> None:
    """§5.5.2: 全区分で単調増加（到達判定を価格の交差と同値にできる条件）。"""
    values = [
        gateway.value_at_close(
            indicator_id=indicator_id, variant="default", params=CASES[indicator_id],
            dataset_ref=REF, timeframe=TIMEFRAME, close=close,
        )
        for close in close_candidates(window)
    ]

    assert values == sorted(values)
    assert values[0] < values[-1]


# ------------------------------------------ 境目 ＋ 当てはめ ＋ 実 core の合成
def forming_bar_of(window: pd.DataFrame):
    from dashboard_ui.domain.bar import Bar

    last = window.iloc[-1]
    return Bar(time=int(window.index[-1].timestamp()), open=float(last["open"]),
               high=float(last["high"]), low=float(last["low"]),
               close=float(last["close"]))


def previous_bar_of(window: pd.DataFrame):
    from dashboard_ui.domain.bar import Bar

    previous = window.iloc[-2]
    return Bar(time=int(window.index[-2].timestamp()), open=float(previous["open"]),
               high=float(previous["high"]), low=float(previous["low"]),
               close=float(previous["close"]))


@pytest.mark.parametrize("indicator_id", sorted(CASES))
def test_the_fitted_map_reproduces_the_real_forward_evaluation(
    indicator_id: str, gateway, window
) -> None:
    """§5.5.2 の核心: 実 core の `v(C)` は、供給した境目で区分メビウスに載る。

    境目（走行 H / L の折れ ＋ RSI の上下分岐）が正しくなければ、探針を置いていない価格で
    当てはめが前進評価から外れる。設計書の残差（marod 6.0e-14 / RSI 3.9e-12）と同じ性質を、
    **実データ・実 core**で固定する（fake を挟まない）。閾値は値の桁に対する相対量で置く。
    """
    from dashboard_ui.adapter.breakpoints import BreakpointRegistry
    from dashboard_ui.domain.price_value_map import PriceValueMap

    params = CASES[indicator_id]
    source = BreakpointRegistry().resolve(indicator_id)
    forming = forming_bar_of(window)
    cuts = source.breakpoints(
        bar=forming, params=params,
        prev_value=source.previous_value(bar=previous_bar_of(window), params=params),
    )

    def forward(close: float) -> float:
        return gateway.value_at_close(
            indicator_id=indicator_id, variant="default", params=params,
            dataset_ref=REF, timeframe=TIMEFRAME, close=float(close),
        )

    fitted = PriceValueMap.fit(
        forward, cuts, span=max(forming.high - forming.low, 1.0)
    )
    span = max(forming.high - forming.low, 1.0)
    probes = [forming.low - 2.0 * span + index * span * 0.25 for index in range(0, 17)]
    scale = max(abs(forward(price)) for price in probes)

    residual = max(abs(fitted.value_at(price) - forward(price)) for price in probes)

    assert residual < 1e-6 * scale, (indicator_id, residual, scale)


@pytest.mark.parametrize("indicator_id", sorted(CASES))
def test_dropping_the_breakpoints_breaks_the_fit(indicator_id: str, gateway, window) -> None:
    """検出力: 境目を 1 点しか渡さないと、実 core に対して残差が桁違いに大きくなる。"""
    from dashboard_ui.adapter.breakpoints import BreakpointRegistry
    from dashboard_ui.domain.price_value_map import PriceValueMap

    params = CASES[indicator_id]
    source = BreakpointRegistry().resolve(indicator_id)
    forming = forming_bar_of(window)
    span = max(forming.high - forming.low, 1.0)

    def forward(close: float) -> float:
        return gateway.value_at_close(
            indicator_id=indicator_id, variant="default", params=params,
            dataset_ref=REF, timeframe=TIMEFRAME, close=float(close),
        )

    probes = [forming.low - 2.0 * span + index * span * 0.25 for index in range(0, 17)]
    full = PriceValueMap.fit(
        forward,
        source.breakpoints(bar=forming, params=params,
                           prev_value=source.previous_value(
                               bar=previous_bar_of(window), params=params)),
        span=span,
    )
    partial = PriceValueMap.fit(forward, (forming.low,), span=span)

    good = max(abs(full.value_at(price) - forward(price)) for price in probes)
    bad = max(abs(partial.value_at(price) - forward(price)) for price in probes)

    assert bad > good * 100.0, (indicator_id, good, bad)
