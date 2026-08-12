"""contacts_supply（adapter 層・Phase 5 F-7）の単体検定。

sim の payload には agg.contacts が無い（実測）。接点マーカー（FR-18）は「その run が
実際に使った EA の MA 系列」と表示足から組む。**算出式は 1 行も書かない**——
`report_ui.tools.contacts_export.compute_segment_contacts`（＝プロト bit 一致の usecase を
呼ぶ結線）を import して使う。本モジュールが持つのは「ジョブ仕様と指標供給 → その関数の
引数」への変換だけである。

固定する不変条件:
    1. 前足 MA を跨いだ確定足 close の向きが agg.contacts の `dir` になる（up / down）。
       期待値は移植元の規約（spec.MovingAverageContact: level=ma[i-1]・
       straddle=low<=level<=high、crossings: 符号反転）から手計算した固定値で置く。
    2. 形状は `[{time, price, dir}]`（フロント chart.js がそのまま食う形）。
    3. MA 系列を持たない EA（例: 既定 TC 経路＝madiff/close のみ）では **[]**。
       接点は「価格×MA の交差」なので、MA が無ければ定義できない。捏造しない。
    4. warmup（NaN）区間の MA は渡さない（値の無い足を 0 とみなさない）。
    5. tick は一切読まない（preview / full_scan=False・移植元 export_report_payload と同一）。
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from simulator.domain.exceptions import IndicatorBufferError
from simulator.sim_ui.adapter.contacts_supply import (
    MA_SERIES_NAME,
    build_contacts,
    ma_values_of,
)

_T0, _T1, _T2 = 1776643200, 1776643260, 1776643320
_NAN = float("nan")


def _bars(closes: "list[float]") -> list:
    """低値 90 / 高値 110 の 3 本足（straddle 条件を常に満たす＝クロス判定だけを見る）。"""
    return [
        SimpleNamespace(time=t, open=c, high=110.0, low=90.0, close=c)
        for t, c in zip((_T0, _T1, _T2), closes)
    ]


class _FakeIndicators:
    """`.get(name)` だけを持つ指標供給ダブル（simulator.main.build_ea_indicators の戻り相当）。"""

    def __init__(self, series: "dict[str, list]") -> None:
        self._series = series

    def get(self, name: str):
        if name not in self._series:
            raise IndicatorBufferError(
                "未登録の指標参照", context={"name": name, "available": list(self._series)}
            )
        return self._series[name]


_BACKTEST = {
    "symbol": "JP225",
    "period": "M1",
    "ma_period": 20,
    "ma_method": "ema",
}


def _contacts(closes, ma, backtest=None):
    return build_contacts(
        bars=_bars(closes),
        backtest=backtest if backtest is not None else _BACKTEST,
        indicators=_FakeIndicators({MA_SERIES_NAME: ma}),
    )


# --- 1/2. クロス方向と形状 ---------------------------------------------------

def test_下から上へ抜けた足がup接点になる() -> None:
    # 前足 close 99 < ma_prev 102 < 今足 close 105 → t2 で up。
    assert _contacts([100.0, 99.0, 105.0], [102.0, 102.0, 102.0]) == [
        {"time": _T2, "price": 105.0, "dir": "up"},
    ]


def test_上から下へ抜けた足がdown接点になる() -> None:
    # closes 100 → 105 → 99（ma_prev は常に 102）。t1 で下→上（up）、t2 で上→下（down）の
    # **2 件**が出る。往復は 2 接点であり、後半だけを数えるのは誤り（手計算で固定する）。
    assert _contacts([100.0, 105.0, 99.0], [102.0, 102.0, 102.0]) == [
        {"time": _T1, "price": 105.0, "dir": "up"},
        {"time": _T2, "price": 99.0, "dir": "down"},
    ]


def test_跨がなければ接点は出ない() -> None:
    assert _contacts([100.0, 99.0, 98.0], [102.0, 102.0, 102.0]) == []


def test_形状はtime_price_dirの3キーだけ() -> None:
    contact = _contacts([100.0, 99.0, 105.0], [102.0, 102.0, 102.0])[0]
    assert set(contact) == {"time", "price", "dir"}
    assert isinstance(contact["time"], int)
    assert isinstance(contact["price"], float)


# --- 3. MA を持たない EA では接点を作らない ---------------------------------

def test_MA系列の無いEAでは空になる() -> None:
    indicators = _FakeIndicators({"madiff": [1.0, 1.0, 1.0], "close": [1.0, 1.0, 1.0]})
    assert build_contacts(
        bars=_bars([100.0, 99.0, 105.0]), backtest=_BACKTEST, indicators=indicators
    ) == []


def test_足が無ければ空になる() -> None:
    indicators = _FakeIndicators({MA_SERIES_NAME: []})
    assert build_contacts(bars=[], backtest=_BACKTEST, indicators=indicators) == []


# --- 4. warmup（NaN）の扱い --------------------------------------------------

def test_warmupのNaNは写像から落ちる() -> None:
    assert ma_values_of([_NAN, 102.0]) == {1: 102.0}


def test_前足MAがNaNの足は接点にならない() -> None:
    # t1 の MA が NaN → t2 の level（ma[t1]）が引けない＝スキップ（0 とみなさない）。
    assert _contacts([100.0, 99.0, 105.0], [102.0, _NAN, 102.0]) == []


def test_ma_values_ofは位置対応のindexを振る() -> None:
    assert ma_values_of([10.0, 11.0, 12.0]) == {0: 10.0, 1: 11.0, 2: 12.0}


# --- 5. 仕様の欠落は黙って埋めない ------------------------------------------

def test_ジョブ仕様のキー欠落はKeyError() -> None:
    with pytest.raises(KeyError):
        _contacts([100.0, 99.0, 105.0], [102.0, 102.0, 102.0], backtest={"symbol": "JP225"})
