"""run_account_scenario — 発注計画を実 tick へ適用し口座状態時系列 JSON を出力する CLI（ISSUE-369）。

使い方（リポジトリ直下から）:
    <venv python> simulator/tools/run_account_scenario.py --plan <plan.json> \
        --start 2026-08-06 --end 2026-08-08 --out out/long_stop.json [--sample 20]

    - tick は marketdata.tick_m1（tick tree レイアウトの単一権威）経由で読む。
      データ基点は marketdata.paths.DATA_DIR（環境変数 MARKETDATA_DATA_DIR で切替・
      worktree からは本チェックアウトの data/marketdata を指す）。
    - --sample N: 状態時系列を N tick ごとに間引いて出力する（イベント発生 tick と
      各 tick の維持率最小点は必ず残す）。イベント・集計は全 tick 適用の結果で不変。

発注計画 JSON の形式:
    {
      "direction": "long" | "short",
      "balance": 172000,
      "margin_rate": 0.10,            // 省略時 0.10
      "point_value": 1.0,             // 省略時 1.0
      "mark_price_mode": "mid",       // 省略時 "mid"（U1: "trade-side" で切替）
      "entries": [ {"units": 200, "price": null},        // null = 成行
                   {"units": 400, "price": 66200.0} ],   // 指値
      "stop_price": 65800.0,          // 省略可
      "tp_price": 67000.0             // 省略可
    }
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parents[2]   # simulator/tools/ → リポジトリ直下
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

import pandas as pd  # noqa: E402  (venv python で実行する・prototype_260811-01/README.md 参照)

from marketdata import tick_m1  # noqa: E402  tick tree レイアウトの単一権威（ISSUE-262）

from simulator.usecase.account_engine import (  # noqa: E402
    AccountConfig, AccountEngine, EntryOrder, OrderPlan,
)


def load_plan(path: Path) -> tuple[OrderPlan, AccountConfig]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    plan = OrderPlan(
        direction=raw["direction"],
        entries=[EntryOrder(units=float(e["units"]),
                            price=None if e.get("price") is None else float(e["price"]))
                 for e in raw["entries"]],
        stop_price=None if raw.get("stop_price") is None else float(raw["stop_price"]),
        tp_price=None if raw.get("tp_price") is None else float(raw["tp_price"]),
    )
    cfg = AccountConfig(
        balance=float(raw["balance"]),
        margin_rate=float(raw.get("margin_rate", 0.10)),
        losscut_ratio=float(raw.get("losscut_ratio", 1.00)),
        point_value=float(raw.get("point_value", 1.0)),
        margin_basis=raw.get("margin_basis", "entry"),  # 公式 §3(2) 既定（約定代金基準）
        mark_price_mode=raw.get("mark_price_mode", "mid"),
    )
    return plan, cfg


def iter_ticks(start: str, end: str):
    """[start, end]（両端含む・UTC 日）の tick を (ts_ms, bid, ask) で昇順に流す。"""
    files = tick_m1.day_parquet_files(start, end)
    if not files:
        raise SystemExit(f"tick parquet が見つからない: {start}..{end}（DATA_DIR を確認）")
    for f in files:
        df = pd.read_parquet(f, columns=["timestamp", "bidPrice", "askPrice"])
        # timestamp は datetime64[ms, UTC]（実測）。単位を ms に明示してから int64 化する
        #   （ns 前提の // 1_000_000 は桁落ちする・実測でイベント ts が潰れた）。
        ts = df["timestamp"].astype("datetime64[ms, UTC]").astype("int64").to_numpy()
        bid = df["bidPrice"].to_numpy()
        ask = df["askPrice"].to_numpy()
        for i in range(len(df)):
            yield int(ts[i]), float(bid[i]), float(ask[i])


def sample_series(series, events, n: int) -> list[int]:
    """出力へ残す index 集合（N ごと＋イベント tick＋維持率最小点＋末尾）。"""
    keep = set(range(0, len(series.ts), max(1, n)))
    keep.add(len(series.ts) - 1)
    ev_ts = {e.ts for e in events}
    ratios = series.margin_ratio
    min_i, min_v = None, None
    for i, t in enumerate(series.ts):
        if t in ev_ts:
            keep.add(i)
        r = ratios[i]
        if r is not None and (min_v is None or r < min_v):
            min_i, min_v = i, r
    if min_i is not None:
        keep.add(min_i)
    return sorted(i for i in keep if 0 <= i < len(series.ts))


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--plan", required=True, type=Path)
    ap.add_argument("--start", required=True, help="開始日（UTC・YYYY-MM-DD）")
    ap.add_argument("--end", required=True, help="終了日（UTC・両端含む）")
    ap.add_argument("--out", required=True, type=Path)
    ap.add_argument("--sample", type=int, default=1, help="状態時系列の間引き（N tick ごと・既定 1=全量）")
    args = ap.parse_args()

    plan, cfg = load_plan(args.plan)
    engine = AccountEngine(plan, cfg)
    result = engine.run(iter_ticks(args.start, args.end))

    s = result.series
    idx = sample_series(s, result.events, args.sample)
    out = {
        "meta": {
            "plan": json.loads(args.plan.read_text(encoding="utf-8")),
            "start": args.start, "end": args.end,
            "ticks_applied": len(s.ts),
            "ticks_emitted": len(idx),
            "sample": args.sample,
            "margin_basis": cfg.margin_basis,
            "mark_price_mode": cfg.mark_price_mode,
        },
        "series": {
            "ts": [s.ts[i] for i in idx],
            "bid": [s.bid[i] for i in idx],
            "ask": [s.ask[i] for i in idx],
            "balance": [s.balance[i] for i in idx],
            "equity": [s.equity[i] for i in idx],
            "required_margin": [s.required_margin[i] for i in idx],
            "margin_ratio": [s.margin_ratio[i] for i in idx],
            "open_units": [s.open_units[i] for i in idx],
        },
        "events": [
            {"ts": e.ts, "kind": e.kind, "price": e.price, "units": e.units,
             "pnl": e.pnl, "note": e.note}
            for e in result.events
        ],
        "summary": {
            "final_balance": result.final_balance,
            "closed": result.closed,
            "losscut_hit": result.losscut_hit,
        },
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(out, ensure_ascii=False), encoding="utf-8")
    print(f"適用 tick {len(s.ts):,} 件 / 出力 {len(idx):,} 点 / イベント {len(result.events)} 件 "
          f"/ 最終残高 ¥{result.final_balance:,.0f} / closed={result.closed} "
          f"/ losscut={result.losscut_hit} → {args.out}")


if __name__ == "__main__":
    main()
