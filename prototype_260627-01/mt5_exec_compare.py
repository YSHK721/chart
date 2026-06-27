#!/usr/bin/env python3
"""実 simulator エンジン × MT5ティックモデル4種 の「約定込み」検証（使い捨て試作・A案 cycle b）。

目的: 実 simulator の戦略・執行（fill / SL・TP / reverse）をそのまま使い、ティックモデリング
      モードだけ替えて同一データ・同一戦略の約定結果（トレード数・損益・決済理由）がどう変わるかを
      数値比較する。執行は再実装せず実エンジン（run_backtest）を駆動する＝最も忠実。

データ: 実ティック JP225 2018-06（read-only）から M1 CSV と hive parquet tick-store を一時生成
        （scratch へ・コミットしない）。real_ticks はこの実ティックを使う。
既存 simulator は無改変（import して run_backtest を呼ぶのみ）。
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
               "/scratchpad/mt5exec")
SYMBOL = "JP225"
N_DAYS = 5          # 検証日数（先頭から）。runtime と約定数のバランス
MODES = ["open_only", "ohlc_expand", "every_tick", "real_ticks"]


def prep_data() -> tuple[Path, Path]:
    files = sorted(glob.glob(TICK_GLOB))[:N_DAYS]
    df = pd.concat([pd.read_parquet(f) for f in files], ignore_index=True)
    df["mid"] = (df["bidPrice"] + df["askPrice"]) / 2.0
    df["minute"] = df["timestamp"].dt.floor("min")

    # --- hive parquet tick-store（<root>/<symbol>/year=/month=/day=/part.parquet） ---
    store = SCRATCH / "ticks"
    # 実ティックは UTC tz-aware。M1 CSV(bar.time) は naive のため、突合 TypeError 回避に
    #   tick timestamp も naive へ揃える（区間スライスを naive-vs-naive にする）。
    ts_naive = df["timestamp"].dt.tz_localize(None)
    tick = pd.DataFrame({
        "timestamp": ts_naive,
        "bid": df["bidPrice"], "ask": df["askPrice"],
        "last": df["mid"],
        "volume": (df["bidVolume"] + df["askVolume"]).astype(float),
    })
    for (y, m, d), g in tick.groupby([tick["timestamp"].dt.year,
                                      tick["timestamp"].dt.month,
                                      tick["timestamp"].dt.day]):
        part = store / SYMBOL / f"year={y:04d}" / f"month={m:02d}" / f"day={d:02d}" / "part.parquet"
        part.parent.mkdir(parents=True, exist_ok=True)
        g.drop(columns=[]).to_parquet(part, index=False)

    # --- M1 CSV（time,open,high,low,close,volume,spread）= 同じ実ティックから集計 ---
    agg = df.groupby("minute")["mid"].agg(["first", "max", "min", "last", "count"]).reset_index()
    csv = SCRATCH / "jp225_m1.csv"
    lines = ["time,open,high,low,close,volume,spread"]
    for r in agg.itertuples(index=False):
        t = pd.Timestamp(r.minute).strftime("%Y-%m-%dT%H:%M:%S")
        lines.append(f"{t},{r.first},{r.max},{r.min},{r.last},{float(r.count)},0")
    csv.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return csv, store


def meta(csv: Path, mode: str, store: Path) -> dict:
    m = dict(
        data_path=csv, symbol=SYMBOL, period="M1", ea_name="TC24051901",
        initial_deposit=100_000.0, contract_size=1.0,
        volume_min=0.01, volume_max=100.0, volume_step=0.01,
        stops_level=0, digits=1, point_size=1.0, leverage=100.0,
        ma_period=20, ma_method="sma", lot_size=1.0,
        stop_loss_points=30, take_profit_points=60,
        config_overrides={"tick_model": mode},
    )
    if mode == "real_ticks":
        m["tick_store_root"] = str(store)
    return m


def reasons(trades) -> dict:
    out: dict = {}
    for t in trades:
        out[t.exit_reason] = out.get(t.exit_reason, 0) + 1
    return out


def main() -> None:
    print("== 実エンジン × MT5ティックモデル4種 約定込み検証 (JP225 2018-06) ==")
    SCRATCH.mkdir(parents=True, exist_ok=True)
    csv, store = prep_data()
    n_bars = sum(1 for _ in csv.read_text().splitlines()) - 1
    print(f"検証: 先頭{N_DAYS}日  M1バー: {n_bars:,}  戦略: TC24051901 madiff(sma,20) "
          f"SL=200/TP=400pt\n")

    print(f"{'モード':<12}{'トレード':>8}{'損益':>12}{'勝':>5}{'負':>5}{'決済理由':>24}")
    base = None
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
        rs = reasons(res.trades)
        rs_str = " ".join(f"{k}:{v}" for k, v in sorted(rs.items()))
        print(f"{mode:<12}{st.trades:>8,}{st.profit:>12,.1f}{wins:>5}{losses:>5}   {rs_str}")
        if base is None:
            base = st.trades
    print("\n[読み方] 同一データ・同一戦略で、ティックモデリング精度のみが異なる。トレード数・"
          "損益・決済理由(tp/sl/reverse)の差＝モデリングが約定結果に与える影響＝検証時の選択依存。")


if __name__ == "__main__":
    main()
