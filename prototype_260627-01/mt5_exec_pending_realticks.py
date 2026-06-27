#!/usr/bin/env python3
"""実エンジン × ペンディング戦略 × 実ティック含む4モード の足内約定差検証（使い捨て試作・cycle b-3）。

問い: real_ticks（実ティックの足内パス）は、合成OHLCモード（open/4点/多数合成）と
      ペンディング約定でどれだけ違うか。合成は実ティックの微振動を欠くため、約定機会を
      過小/過大評価しないか。

データ: JP225 2018-06 実ティック（read-only）から、MT5タブ形式 M1 CSV と hive parquet
        tick-store を一時生成（scratch・非コミット）。全モード同一の原データで比較する。
戦略: MA_Slope_Pending_EA（限指値・pending_lifecycle=True→全モード every-tick 経路）。
既存 simulator は無改変（run_backtest を呼ぶのみ）。
"""
from __future__ import annotations

import glob
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, "/workspaces/app-intrabar-tick")
from simulator.main import run_backtest  # noqa: E402

TICK_GLOB = "/workspaces/app/data/marketdata/ticks/2018/06/*/JP225_ticks.parquet"
SCRATCH = Path("/tmp/claude-0/-workspaces-app/58deaa1c-87d8-472f-9a81-69d58552aee9"
               "/scratchpad/mt5pending")
SYMBOL = "JP225"
POINT = 0.1
N_DAYS = 8
MODES = ["open_only", "ohlc_expand", "every_tick", "real_ticks"]


def prep() -> tuple[Path, Path]:
    files = sorted(glob.glob(TICK_GLOB))[:N_DAYS]
    df = pd.concat([pd.read_parquet(f) for f in files], ignore_index=True)
    df["mid"] = (df["bidPrice"] + df["askPrice"]) / 2.0
    df["spr"] = (df["askPrice"] - df["bidPrice"]) / POINT     # スプレッド[points]
    df["ts"] = df["timestamp"].dt.tz_localize(None)
    df["minute"] = df["ts"].dt.floor("min")

    # hive parquet tick-store（real_ticks 用・列= timestamp/bid/ask/last/volume）
    store = SCRATCH / "ticks"
    tick = pd.DataFrame({"timestamp": df["ts"], "bid": df["bidPrice"], "ask": df["askPrice"],
                         "last": df["mid"], "volume": (df["bidVolume"] + df["askVolume"]).astype(float)})
    for (y, m, d), g in tick.groupby([tick["timestamp"].dt.year, tick["timestamp"].dt.month,
                                      tick["timestamp"].dt.day]):
        part = store / SYMBOL / f"year={y:04d}" / f"month={m:02d}" / f"day={d:02d}" / "part.parquet"
        part.parent.mkdir(parents=True, exist_ok=True)
        g.to_parquet(part, index=False)

    # MT5タブ形式 M1 CSV（<DATE> <TIME> <OPEN> <HIGH> <LOW> <CLOSE> <TICKVOL> <VOL> <SPREAD>）
    agg = df.groupby("minute").agg(
        o=("mid", "first"), h=("mid", "max"), l=("mid", "min"), c=("mid", "last"),
        n=("mid", "count"), spr=("spr", "mean")).reset_index()
    csv = SCRATCH / "jp225_m1_mt5.csv"
    lines = ["<DATE>\t<TIME>\t<OPEN>\t<HIGH>\t<LOW>\t<CLOSE>\t<TICKVOL>\t<VOL>\t<SPREAD>"]
    for r in agg.itertuples(index=False):
        ts = pd.Timestamp(r.minute)
        lines.append(f"{ts.strftime('%Y.%m.%d')}\t{ts.strftime('%H:%M:%S')}\t"
                     f"{r.o:.1f}\t{r.h:.1f}\t{r.l:.1f}\t{r.c:.1f}\t{int(r.n)}\t0\t{int(round(r.spr))}")
    csv.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return csv, store


def meta(csv: Path, mode: str, store: Path) -> dict:
    m = dict(
        data_path=csv, symbol=SYMBOL, period="M1", ea_name="MA_Slope_Pending_EA",
        initial_deposit=100_000_000.0, contract_size=10.0, leverage=10.0,
        volume_min=0.01, volume_max=100.0, volume_step=0.01,
        stops_level=0, digits=1, point_size=POINT,
        ma_period=20, ma_method="ema", lot_size=0.1,
        stop_loss_points=0, take_profit_points=0,
        slope_shift=1, slope_min_points=1.0,
        entry_offset_points=50.0, entry_type="limit",
        config_overrides={"tick_model": mode, "pending_lifecycle": True,
                          "entry_price_basis": "current_open"},
    )
    if mode == "real_ticks":
        m["tick_store_root"] = str(store)
    return m


def reasons(trades) -> str:
    out: dict = {}
    for t in trades:
        out[t.exit_reason] = out.get(t.exit_reason, 0) + 1
    return " ".join(f"{k}:{v}" for k, v in sorted(out.items()))


def main() -> None:
    print("== 実エンジン × ペンディング × 実ティック含む4モード 足内約定差 (JP225 2018-06) ==")
    SCRATCH.mkdir(parents=True, exist_ok=True)
    csv, store = prep()
    n_bars = len(csv.read_text().splitlines()) - 1
    print(f"検証: 先頭{N_DAYS}日  M1バー: {n_bars:,}  戦略: MA_Slope_Pending_EA ema(20) "
          f"limit off=50pt / pending_lifecycle=True\n")
    print(f"{'モード':<12}{'トレード':>8}{'損益':>13}{'勝':>5}{'負':>6}  決済理由")
    rows = []
    for mode in MODES:
        try:
            code, res = run_backtest(**meta(csv, mode, store))
        except Exception as e:
            print(f"{mode:<12}  ERROR: {type(e).__name__}: {e}")
            continue
        if code != 0 or res is None:
            print(f"{mode:<12}  exit={code} result=None")
            continue
        st = res.stats
        wins = sum(1 for t in res.trades if t.pnl() > 0)
        losses = sum(1 for t in res.trades if t.pnl() <= 0)
        print(f"{mode:<12}{st.trades:>8,}{st.profit:>13,.1f}{wins:>5}{losses:>6}  {reasons(res.trades)}")
        rows.append((mode, st.trades, round(st.profit, 1)))
    if rows:
        synth = {p for m, t, p in rows if m != "real_ticks"}
        real = next((p for m, t, p in rows if m == "real_ticks"), None)
        print(f"\n[判定] real_ticks 損益={real}  合成モード損益={sorted(synth)}")
        if real is not None and real not in synth:
            print("       → real_ticks は合成モードと約定結果が異なる＝合成は実ティックの"
                  "微振動を欠き、ペンディング約定を取りこぼす/誤評価する。実ティック優先の根拠。")


if __name__ == "__main__":
    main()
