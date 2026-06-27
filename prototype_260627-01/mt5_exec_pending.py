#!/usr/bin/env python3
"""実エンジン × ペンディング戦略 × モデリングモードの「足内約定」差検証（使い捨て試作・cycle b-2）。

cycle b-1 で判明: 成行・終値reverse 戦略（TC24051901）は約定がモデリング非依存だった
  （加えて合成モードは market 注文では every-tick に結線されず bar-mode 固定＝cycle2 TODO）。
モデリングが約定を左右するのは「ペンディング注文の足内約定」。pending_lifecycle=True なら
  合成モード（open_only/ohlc_expand/every_tick）も every-tick 経路を通り、ティック生成方式の
  違いがペンディングのトリガ時刻・約定価格を変える。

ここでは実 MA_Slope_Pending_EA を、既存 JP225 2025-01 MT5 フィクスチャ（read-only）で
  3合成モード横断に実走し、約定結果（トレード数・損益・決済理由）の差を数値比較する。
  real_ticks は当該期間の実ティックが無いため対象外（2018-06 のみ存在）。
既存 simulator は無改変（run_backtest を呼ぶのみ）。
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, "/workspaces/app-intrabar-tick")
from simulator.main import run_backtest  # noqa: E402

FIXTURE = Path("/workspaces/app-intrabar-tick/simulator/tests/fixtures/mt5/"
               "ma_slope_jp225_202501/input/JP225_M1_202412230100_202501302359.csv")
MODES = ["open_only", "ohlc_expand", "every_tick"]


def meta(mode: str) -> dict:
    return dict(
        data_path=FIXTURE, symbol="JP225", period="M1",
        ea_name="MA_Slope_Pending_EA",
        initial_deposit=100_000_000.0, contract_size=10.0, leverage=10.0,
        volume_min=0.01, volume_max=100.0, volume_step=0.01,
        stops_level=0, digits=1, point_size=0.1,
        ma_period=20, ma_method="ema", lot_size=0.1,
        stop_loss_points=0, take_profit_points=0,
        slope_shift=1, slope_min_points=1.0,
        entry_offset_points=50.0, entry_type="limit",
        config_overrides={
            "tick_model": mode,
            "pending_lifecycle": True,        # 合成モードも every-tick 経路へ
            "entry_price_basis": "current_open",
        },
    )


def reasons(trades) -> str:
    out: dict = {}
    for t in trades:
        out[t.exit_reason] = out.get(t.exit_reason, 0) + 1
    return " ".join(f"{k}:{v}" for k, v in sorted(out.items()))


def main() -> None:
    print("== 実エンジン × ペンディング戦略 × モデリング3モード 足内約定検証 (JP225 2025-01) ==")
    print("戦略: MA_Slope_Pending_EA ema(20) limit offset=50pt / pending_lifecycle=True\n")
    print(f"{'モード':<12}{'トレード':>8}{'損益':>12}{'勝':>5}{'負':>5}  決済理由")
    rows = []
    for mode in MODES:
        try:
            code, res = run_backtest(**meta(mode))
        except Exception as e:
            print(f"{mode:<12}  ERROR: {type(e).__name__}: {e}")
            continue
        if code != 0 or res is None:
            print(f"{mode:<12}  exit={code} result=None")
            continue
        st = res.stats
        wins = sum(1 for t in res.trades if t.pnl() > 0)
        losses = sum(1 for t in res.trades if t.pnl() <= 0)
        print(f"{mode:<12}{st.trades:>8,}{st.profit:>12,.1f}{wins:>5}{losses:>5}  {reasons(res.trades)}")
        rows.append((mode, st.trades, st.profit))
    if len(rows) >= 2:
        diff = len({(t, round(p, 1)) for _, t, p in rows}) > 1
        print(f"\n[判定] モード間でトレード数/損益が{'異なる＝モデリングは約定に影響する' if diff else '同一'}。")
    print("[読み方] ペンディングは足内のどのティックでトリガされるかで約定が変わるため、")
    print("         ティック生成方式（open/4点OHLC/多数合成）の違いが約定結果に表れる。")


if __name__ == "__main__":
    main()
