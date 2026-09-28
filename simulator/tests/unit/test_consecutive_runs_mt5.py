"""連勝・連敗の指標（ISSUE-549）を MT5 実レポートの値で固定する。

状態検証: MT5 実レポート（confirmation の xlsx 9 本）と固定データ golden の Deals 表の損益を
    本番の関数へ入れ、MT5 の表示値と一致すること。損益 0 の取引を含む run（2026-01 / 2026-03 /
    golden）が旧規則（0 は区切る・最初の最長ラン）を落とす。
計算量: ランの区切りは各取引の損益を 1 回だけ読む（読んだ回数 − 取引数 = 0・長さ 50 / 5000）。
    観測点は公開の入力（取引の pnl 読み取り）。
"""
from __future__ import annotations

import glob
import json
import re

import pytest

from simulator.usecase import metrics_spec as ms
from simulator.tests.unit.test_mt5_parity_report_additions import _num, _read_mt5_report

_REPORTS = sorted(glob.glob("/workspaces/app/simulator/tests/confirmation/*/report.xlsx"))
_GOLDEN = "/workspaces/app/simulator/tests/fixtures/mt5/ma_slope_jp225_202501/expected/report.json"


class _Trade:
    reads = 0

    def __init__(self, pnl):
        self._pnl = pnl

    def pnl(self):
        type(self).reads += 1
        return self._pnl


def _pair(text):
    m = re.match(r"\s*(-?[\d ]+(?:\.\d+)?)\s*\((-?[\d ]+(?:\.\d+)?)\)", str(text).replace("\xa0", " "))
    return float(m.group(1).replace(" ", "")), float(m.group(2).replace(" ", ""))


def _ours(trades):
    return {
        "max_wins": (ms.max_consecutive_wins_count(trades), ms.max_consecutive_wins_profit(trades)),
        "max_losses": (ms.max_consecutive_losses_count(trades), ms.max_consecutive_losses_loss(trades)),
        "maximal_profit": (ms.maximal_consecutive_profit_amount(trades), ms.maximal_consecutive_profit_count(trades)),
        "maximal_loss": (ms.maximal_consecutive_loss_amount(trades), ms.maximal_consecutive_loss_count(trades)),
        "avg_wins": round(ms.average_consecutive_wins(trades)),
        "avg_losses": round(ms.average_consecutive_losses(trades)),
    }


@pytest.mark.skipif(not _REPORTS, reason="MT5 実レポートが無い環境")
@pytest.mark.parametrize("path", _REPORTS)
def test_runs_match_the_mt5_report(path):
    values, deals = _read_mt5_report(path)
    trades = [_Trade(_num(d["Profit"]) + _num(d.get("Commission") or 0) + _num(d.get("Swap") or 0))
              for d in deals if d.get("Direction") == "out"]
    expected = {
        "max_wins": _pair(values["Maximum consecutive wins ($):"]),
        "max_losses": _pair(values["Maximum consecutive losses ($):"]),
        "maximal_profit": _pair(values["Maximal consecutive profit (count):"]),
        "maximal_loss": _pair(values["Maximal consecutive loss (count):"]),
        "avg_wins": int(float(values["Average consecutive wins:"])),
        "avg_losses": int(float(values["Average consecutive losses:"])),
    }
    assert _ours(trades) == expected


def test_runs_match_the_golden_fixture():
    golden = json.load(open(_GOLDEN, encoding="utf-8"))
    r = golden["results"]
    trades = [_Trade(d["profit"]) for d in golden["deals"] if d.get("dir") == "out"]
    assert sum(1 for t in trades if t.pnl() == 0) > 0  # 損益 0 を含む（旧規則を落とす入力）
    got = _ours(trades)
    assert got["max_wins"] == (r["max_consecutive_wins"], r["max_consecutive_wins_amount"])
    assert got["max_losses"] == (r["max_consecutive_losses"], r["max_consecutive_losses_amount"])
    assert got["maximal_profit"][0] == r["maximal_consecutive_profit"]
    assert got["maximal_loss"][0] == r["maximal_consecutive_loss"]


@pytest.mark.parametrize("n", [50, 5000])
def test_the_run_split_reads_each_pnl_once(n):
    trades = [_Trade(p) for p in ([10.0, 0.0, -5.0, -5.0, 20.0] * (n // 5))]
    _Trade.reads = 0
    ms._runs(trades)
    assert _Trade.reads - len(trades) == 0
