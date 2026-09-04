#!/usr/bin/env python3
"""tick_m1_cli — 日別ティック parquet から M1 素材 CSV を作る CLI（合成点・ISSUE-479 M-2）。

使い方: ``python -m marketdata.tools.tick_m1_cli [START] [END] [SYMBOL] [REF]``
（旧: ``python -m marketdata.tick_m1``。素材化の権威モジュールに CLI を同居させないため移した。）

START 既定 ``2025-01-01``、END 既定は本日（UTC）。試作 prep_tick_rollup の CLI を踏襲する。

なぜ分けたか: CLI は引数解釈と進捗表示を担う**合成点**であり、素材化の規則
（:mod:`marketdata.tick_m1`）とも tick 木レイアウト（:mod:`marketdata.tick_tree`）とも
変更理由が違う。同居していた間、``sys`` と ``datetime`` は CLI のためだけに権威モジュールへ
持ち込まれていた。本モジュールはロジックを持たず、権威の関数を順に呼ぶだけである。
"""

from __future__ import annotations

import sys
from datetime import datetime, timezone
from typing import List, Optional

import pandas as pd

from marketdata import tick_m1, tick_tree


def main(argv: "Optional[List[str]]" = None) -> None:
    """CLI: ``python -m marketdata.tools.tick_m1_cli [START] [END] [SYMBOL] [REF]``。"""
    args = sys.argv[1:] if argv is None else list(argv)
    start = args[0] if len(args) > 0 else "2025-01-01"
    end = args[1] if len(args) > 1 else datetime.now(timezone.utc).strftime("%Y-%m-%d")
    symbol = args[2] if len(args) > 2 else tick_tree._DEFAULT_SYMBOL
    ref = args[3] if len(args) > 3 else tick_m1._DEFAULT_REF

    files = tick_tree.day_parquet_files(start, end, symbol=symbol)
    print(f"範囲 {start}..{end}  symbol={symbol}  ティック日数: {len(files)}", flush=True)
    if not files:
        print(f"ティック parquet が見つかりません（{tick_tree.tick_root()}）", flush=True)
        return
    out_path = tick_m1.build_m1_from_ticks(start, end, symbol=symbol, ref=ref)
    m1 = pd.read_csv(out_path)
    if len(m1):
        print(
            f"M1バー: {len(m1):,}  ({m1['date'].iloc[0]} .. {m1['date'].iloc[-1]})  -> {out_path}",
            flush=True,
        )
    else:
        print(f"M1バー: 0  -> {out_path}", flush=True)


if __name__ == "__main__":
    main()
