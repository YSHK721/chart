"""計算量 9（ISSUE-466）: 受理集合（paramScopes）の**解決**は発行ごとに起きない。

ISSUE-466 の是正は「送る前に params を variant の受理集合へ絞る」ことだが、絞るために
受理集合を**発行のたびに解決し直す**と ISSUE-464 と同型の無駄（epoch の中で不変な量を
毎回作り直す）を新たに作ることになる。受理集合は指標記述子から導かれる定数であり、
プロセスの中で変わらない。

CLAUDE.md 絶対命令 §4.1 に従い、測るのは時間ではなく**回数**である。回数そのものは
期待値に焼き込まない。固定するのは次の 2 つだけである。

- **無駄の不在**: 発行を N 本に増やしても、受理集合の解決の**追加は 0**。
- **オーダーの表明**: N を 2 点（3 / 30）変えても解決回数は同一である
  （＝解決回数は発行数に依存しない）。
"""
from __future__ import annotations

from types import SimpleNamespace

import pandas as pd

from dashboard_ui.adapter.gateway.indicator_ui_compute_gateway import (
    IndicatorUiComputeGateway,
)
from dashboard_ui.adapter.gateway.param_scopes import ParamScopes

REF = "jp225_tick"
START = 1_787_003_400


def _frame(rows: int = 4) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "open": [100.0 + i for i in range(rows)],
            "high": [110.0 + i for i in range(rows)],
            "low": [90.0 + i for i in range(rows)],
            "close": [105.0 + i for i in range(rows)],
            "volume": [1.0] * rows,
        },
        index=pd.to_datetime([START + i * 60 for i in range(rows)], unit="s"),
    )


class ScopesSpy:
    """受理集合の**解決**だけを数える Test Spy（絞り込みの回数は数えない）。"""

    def __init__(self) -> None:
        self.resolved = 0

    def __call__(self):
        self.resolved += 1
        return {"osc": {"default": ["length"]}}


class BridgeSpy:
    def __init__(self, scopes: ScopesSpy) -> None:
        self._scopes = scopes

    def is_known(self, ref) -> bool:
        return ref == REF

    def is_known_timeframe(self, timeframe) -> bool:
        return timeframe == "1m"

    def load_dataframe(self, ref, timeframe=None):
        return _frame()

    def full_compute(self, adapter, indicator, variant, df, params):
        return [{"name": indicator, "kind": "line",
                 "data": [{"time": START, "value": 1.0}]}]

    def latest_compute(self, adapter, indicator, variant, df, params):
        return []

    def namespace(self) -> SimpleNamespace:
        return SimpleNamespace(
            dataset=self, adapter=object(), full_compute=self.full_compute,
            latest_compute=self.latest_compute, compute_error=(),
            catalog_param_scopes=self._scopes,
        )


def _issue(gateway: IndicatorUiComputeGateway, count: int) -> None:
    for index in range(count):
        gateway.full_series(indicator_id="osc", variant="default",
                            params={"length": index, "wait_for_close": True},
                            dataset_ref=REF, timeframe="1m")


def test_more_issuances_do_not_resolve_the_accepted_set_again() -> None:
    """オーダーの表明（2 点固定）: 発行 3 本でも 30 本でも解決回数は変わらない。"""
    resolved = {}
    for issuances in (3, 30):
        spy = ScopesSpy()
        gateway = IndicatorUiComputeGateway(bridge=BridgeSpy(spy).namespace())
        _issue(gateway, issuances)
        resolved[issuances] = spy.resolved

    assert resolved[3] == resolved[30]


def test_a_shared_scope_holder_is_resolved_once_across_requests() -> None:
    """口を要求ごとに組み直しても、共有した受理集合の追加解決は 0（プロセス寿命の単一解決）。

    Composition Root は素材ストアと同じ理由でこの保持を口の外に置く（epoch にも要求にも
    依らない定数だから）。ここでは繰り返し数 2 点でその追加が 0 であることを固定する。
    """
    additional = {}
    for requests in (2, 10):
        spy = ScopesSpy()
        scopes = ParamScopes(source=spy)
        bridge = BridgeSpy(spy).namespace()
        _issue(IndicatorUiComputeGateway(bridge=bridge, param_scopes=scopes), 1)
        warmed = spy.resolved
        for _ in range(requests):
            _issue(IndicatorUiComputeGateway(bridge=bridge, param_scopes=scopes), 1)
        additional[requests] = spy.resolved - warmed

    assert additional[2] == 0
    assert additional[10] == 0
