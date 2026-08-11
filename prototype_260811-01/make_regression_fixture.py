"""make_regression_fixture — 口座状態エンジンの移設回帰ゲート用 fixture を生成する（Phase 2）。

生成物（simulator/tests/fixtures/account_engine/ へ出力・追跡対象）:
    jp225_ticks_20260806_0000_0110.csv   実 tick 断片（2026-08-06 00:00–01:10 UTC・ts_ms,bid,ask）
    expected_gate.json                   3 ゲートシナリオのエンジン出力固定値
        - events / summary（全量）
        - series_sha256（全系列 JSON の SHA-256＝byte 一致の圧縮表現）

ゲートシナリオ（断片内で決定論的に完結する 3 種）:
    G1 単一ロング 25u・E=172,000・stop/tp なし → ロスカット（00:53）
    G2 単一ロング 20u・E=172,000・stop=65,100 → 損切り（01:01）
    G3 難平ロング 6+8+10u（成行/65,300/65,000）・E=172,000 → 部分約定のまま断片終端

再生成: MARKETDATA_DATA_DIR=... <venv python> make_regression_fixture.py
（過去 UTC 日の tick は再取得＝保存に完全一致（実測済み）のため、いつ再生成しても同一になる）
"""

from __future__ import annotations

import csv
import hashlib
import json
import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_REPO = _HERE.parent
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

from account_engine import AccountConfig, AccountEngine, EntryOrder, OrderPlan  # noqa: E402
from run_scenario import iter_ticks  # noqa: E402

FIXTURE_DIR = _REPO / "simulator" / "tests" / "fixtures" / "account_engine"
CSV_NAME = "jp225_ticks_20260806_0000_0110.csv"
T0 = 1785974400000            # 2026-08-06T00:00:00Z (ms)
T1 = T0 + 70 * 60 * 1000      # +70 分


def gate_scenarios() -> dict[str, tuple[OrderPlan, AccountConfig]]:
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


def series_sha256(series) -> str:
    payload = json.dumps({
        "ts": series.ts, "bid": series.bid, "ask": series.ask,
        "balance": series.balance, "equity": series.equity,
        "required_margin": series.required_margin, "margin_ratio": series.margin_ratio,
        "open_units": series.open_units,
    }, ensure_ascii=False)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def main() -> None:
    FIXTURE_DIR.mkdir(parents=True, exist_ok=True)
    ticks = [(ts, bid, ask) for ts, bid, ask in iter_ticks("2026-08-06", "2026-08-06")
             if T0 <= ts < T1]
    csv_path = FIXTURE_DIR / CSV_NAME
    with csv_path.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["ts_ms", "bid", "ask"])
        for row in ticks:
            w.writerow([row[0], repr(row[1]), repr(row[2])])   # repr = 浮動小数を桁落ちなく往復
    print(f"tick 断片 {len(ticks):,} 件 → {csv_path}")

    expected: dict[str, dict] = {}
    for name, (plan, cfg) in gate_scenarios().items():
        r = AccountEngine(plan, cfg).run(iter(ticks))
        expected[name] = {
            "events": [{"ts": e.ts, "kind": e.kind, "price": e.price, "units": e.units,
                        "pnl": e.pnl, "note": e.note} for e in r.events],
            "summary": {"final_balance": r.final_balance, "closed": r.closed,
                        "losscut_hit": r.losscut_hit},
            "ticks_applied": len(r.series.ts),
            "series_sha256": series_sha256(r.series),
        }
        print(f"  {name}: events={len(r.events)} final=¥{r.final_balance:,.2f} "
              f"sha={expected[name]['series_sha256'][:12]}…")
    (FIXTURE_DIR / "expected_gate.json").write_text(
        json.dumps(expected, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"期待値 → {FIXTURE_DIR / 'expected_gate.json'}")


if __name__ == "__main__":
    main()
