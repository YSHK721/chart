"""サマリー (Report) タブへ足した MT5 統計（2026-09-27）の検定。

状態検証: MT5 実レポート（`simulator/tests/confirmation/*/report.xlsx`）の Deals 表から
    決済ごとの残高を取り出し、`balance_linear_regression` / `ghpr` / `total_deals` が
    MT5 の表示値と一致することを確かめる（式を MT5 に校正した根拠そのもの）。
計算量: 系列を 1 回だけ読むこと（読んだ要素数 − 系列長 = 0）を、長さの違う 2 点で表明する。
    観測点は公開の入力（系列）であり、実装の内部名は差し替えない。
"""
from __future__ import annotations

import glob
import math
from collections.abc import Sequence
from types import SimpleNamespace

import numpy as np
import pytest

from simulator.usecase.metrics_spec import ghpr
from simulator.usecase.mt5_parity import (
    balance_linear_regression,
    equity_dd_relative,
    total_deals,
)

_REPORTS = sorted(glob.glob("/workspaces/app/simulator/tests/confirmation/*/report.xlsx"))


def _read_mt5_report(path):
    openpyxl = pytest.importorskip("openpyxl")
    ws = openpyxl.load_workbook(path, read_only=True, data_only=True).active
    rows = [list(r) for r in ws.iter_rows(values_only=True)]
    values, deals, header, in_deals = {}, [], None, False
    for row in rows:
        cells = [c for c in row if c is not None]
        if not cells:
            continue
        if cells[0] == "Deals":
            in_deals = True
            continue
        if in_deals:
            if header is None:
                header = [str(c).strip() if c else "" for c in row]
            else:
                deals.append(dict(zip(header, row)))
            continue
        for i, c in enumerate(cells):
            if isinstance(c, str) and c.endswith(":") and i + 1 < len(cells):
                values[c] = cells[i + 1]
    return values, deals


def _num(v) -> float:
    return float(str(v).replace(" ", "").replace("\xa0", ""))


@pytest.mark.skipif(not _REPORTS, reason="MT5 実レポートが無い環境")
@pytest.mark.parametrize("path", _REPORTS)
def test_linear_regression_and_ghpr_match_the_mt5_report(path):
    values, deals = _read_mt5_report(path)
    deposit = _num(values["Initial Deposit:"])
    balances = [_num(d["Balance"]) for d in deals if d.get("Direction") == "out"]

    corr, stderr = balance_linear_regression(balances, deposit)
    g = ghpr(balances, deposit)

    assert corr == pytest.approx(float(values["LR Correlation:"]), abs=2e-6)
    assert stderr == pytest.approx(float(values["LR Standard Error:"]), abs=1e-6)
    assert f"{g:.4f} ({(g - 1) * 100:.2f}%)" == values["GHPR:"]
    assert 2 * len(balances) == int(_num(values["Total Deals:"]))


def test_total_deals_counts_one_in_per_position_and_one_out_per_trade():
    trades = [SimpleNamespace(exit_reason=r) for r in ("partial", "tp", "sl", "end_of_test")]
    # 玉 3 つ（partial は同じ玉の途中決済）: in 3 + out 4。
    assert total_deals(trades) == 7


def test_equity_dd_relative_is_the_largest_percent_drawdown_and_its_amount():
    # 10000 → 12000 → 9000（25%・3000）→ 20000 → 16000（20%・4000）。
    pct, amount = equity_dd_relative([12000.0, 9000.0, 20000.0, 16000.0], 10000.0)
    assert pct == pytest.approx(25.0)
    assert amount == pytest.approx(3000.0)


def test_degenerate_series_yield_zero_not_nan():
    assert balance_linear_regression([], 10000.0) == (0.0, 0.0)
    corr, stderr = balance_linear_regression([10000.0, 10000.0, 10000.0], 10000.0)
    assert (corr, stderr) == (0.0, 0.0)
    assert not math.isnan(corr)


class _CountingSeries(Sequence):
    """読んだ要素の数を数える系列（計算量の観測点・公開の入力そのもの）。"""

    def __init__(self, values):
        self._values = list(values)
        self.reads = 0

    def __len__(self):
        return len(self._values)

    def __getitem__(self, i):
        if isinstance(i, slice):
            raise TypeError("slice は読み直しとして数えられないので使わせない")
        self.reads += 1
        return self._values[i]

    def __iter__(self):
        for v in self._values:
            self.reads += 1
            yield v


@pytest.mark.parametrize("func", [balance_linear_regression, equity_dd_relative])
@pytest.mark.parametrize("n", [50, 5000])
def test_each_metric_reads_the_series_exactly_once(func, n):
    rng = np.random.default_rng(0)
    series = _CountingSeries(10000.0 + np.cumsum(rng.normal(0, 10, n)))
    func(series, 10000.0)
    # 読んだ数 − 出力に使った数（系列全体を 1 回）= 0。長さを 100 倍にしても 1 回のまま。
    assert series.reads - len(series) == 0
