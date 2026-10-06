"""ema_deviation_short_backtest — 21EMA 上方乖離ショートを MT5 日足 CSV で検証し、保有日数ごとの成績を書く。

使い方::

    python3 -m tools.ema_deviation_short_backtest \\
        --csv simulator/tests/fixtures/mt5_bars/20200501_20260901/JP225_D1_20200501_20260901.csv \\
        --hold 1 3 5 10 20
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tools.ema_deviation_short.rules import simulate  # noqa: E402
from tools.ema_deviation_short.source import FrameBarSource, load_mt5_csv  # noqa: E402


def main(argv: "list[str] | None" = None) -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--csv", required=True)
    ap.add_argument("--hold", type=int, nargs="+", required=True, help="保有日数 N（複数可）")
    ap.add_argument("--period", type=int, default=21)
    ap.add_argument("--deviation", type=float, default=0.08)
    ap.add_argument("--point", type=float, default=0.1)
    ap.add_argument("--digits", type=int, default=1)
    ap.add_argument("--warmup", type=int, default=63, help="判定を始める足（EMA 初期値の影響を避ける）")
    args = ap.parse_args(argv)

    source = FrameBarSource(load_mt5_csv(args.csv))
    print(f"bars={len(source)} {source.date(0)}..{source.date(len(source) - 1)} warmup={args.warmup}")
    for hold in args.hold:
        result = simulate(
            source, period=args.period, deviation=args.deviation, hold_bars=hold,
            point=args.point, digits=args.digits, warmup=args.warmup,
        )
        pts = [t.points for t in result.trades]
        wins = sum(p > 0 for p in pts)
        print(f"\nN={hold} trades={len(pts)} wins={wins} total_pts={sum(pts):.1f}"
              + (f" open_entry={source.date(result.open_entry_index)}" if result.open_entry_index is not None else ""))
        for t in result.trades:
            print(f"  {source.date(t.entry_index)} sell {t.entry_price:.1f} -> "
                  f"{source.date(t.exit_index)} buy {t.exit_price:.1f}  {t.points:+.1f}pt {t.return_pct:+.2f}%")


if __name__ == "__main__":
    main()
