"""口座状態エンジンの移設回帰ゲート（ISSUE-369 Phase 2）。

設計入力（唯一の仕様源）: docs/oanda_indices_cfd_about.md（OANDA 証券公式ページの再構成）と
    prototype_260811-01/README.md「確定した式」。
このテストが固定する回帰: prototype → simulator/ への移設（git mv ＋ import retarget）の
    前後で、実 tick 断片（2026-08-06 00:00–01:10 UTC・14,648 件）に対するエンジン出力が
    byte 一致で不変であること。イベント・summary は全量比較、状態時系列は全配列 JSON の
    SHA-256 で固定する（fixture 生成: prototype_260811-01/make_regression_fixture.py）。

ゲートシナリオ: G1 ロスカット到達 / G2 損切り到達 / G3 難平部分約定のまま終端。
"""
from __future__ import annotations

import csv
import hashlib
import json
from pathlib import Path

import pytest

from simulator.usecase.account_engine import (
    AccountConfig, AccountEngine, EntryOrder, OrderPlan,
)

_FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "account_engine"


def _load_ticks() -> list[tuple[int, float, float]]:
    path = _FIXTURES / "jp225_ticks_20260806_0000_0110.csv"
    out: list[tuple[int, float, float]] = []
    with path.open(newline="", encoding="utf-8") as f:
        reader = csv.reader(f)
        next(reader)  # header
        for ts, bid, ask in reader:
            out.append((int(ts), float(bid), float(ask)))
    return out


def _series_sha256(series) -> str:
    payload = json.dumps({
        "ts": series.ts, "bid": series.bid, "ask": series.ask,
        "balance": series.balance, "equity": series.equity,
        "required_margin": series.required_margin, "margin_ratio": series.margin_ratio,
        "open_units": series.open_units,
    }, ensure_ascii=False)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _scenarios() -> dict[str, tuple[OrderPlan, AccountConfig]]:
    # make_regression_fixture.gate_scenarios と同一定義（fixture の由来を本テストにも明記）
    return {
        "G1_long_losscut": (
            OrderPlan(direction="long", entries=[EntryOrder(units=25.0)]),
            AccountConfig(balance=172000.0)),
        "G2_long_stop": (
            OrderPlan(direction="long", entries=[EntryOrder(units=20.0)], stop_price=65100.0),
            AccountConfig(balance=172000.0)),
        "G3_split_partial": (
            OrderPlan(direction="long", entries=[EntryOrder(units=6.0),
                                                 EntryOrder(units=8.0, price=65300.0),
                                                 EntryOrder(units=10.0, price=65000.0)]),
            AccountConfig(balance=172000.0)),
    }


@pytest.fixture(scope="module")
def ticks():
    return _load_ticks()


@pytest.fixture(scope="module")
def expected():
    return json.loads((_FIXTURES / "expected_gate.json").read_text(encoding="utf-8"))


@pytest.mark.parametrize("name", ["G1_long_losscut", "G2_long_stop", "G3_split_partial"])
def test_gate_output_is_byte_identical(name, ticks, expected):
    plan, cfg = _scenarios()[name]
    r = AccountEngine(plan, cfg).run(iter(ticks))
    exp = expected[name]
    got_events = [{"ts": e.ts, "kind": e.kind, "price": e.price, "units": e.units,
                   "pnl": e.pnl, "note": e.note} for e in r.events]
    assert got_events == exp["events"]
    assert r.final_balance == exp["summary"]["final_balance"]
    assert r.closed == exp["summary"]["closed"]
    assert r.losscut_hit == exp["summary"]["losscut_hit"]
    assert len(r.series.ts) == exp["ticks_applied"]
    assert _series_sha256(r.series) == exp["series_sha256"]
