"""block_extrema_log — 全バーを 1/2 ずつ再帰分割したブロックの高値・安値を標準出力へ書く。

出力（ブロックごとに 2 行・ヘッダなし）::

    bar,yyyy/mm/ddThh:mm:ss,high
    bar,yyyy/mm/ddThh:mm:ss,low

bar はブロックの実バー数。段は全バー数 → 1/2 → 1/4 … → 1 バー。端数はブロック長を固定し
余りを最後のブロックにする。時刻はデータの時刻系のまま（変換しない）。

使い方::

    lightweight-charts-python-main/.venv/bin/python -m tools.block_extrema_log \\
        --ref jp225_mt5 --tf 1h > out.csv
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tools.block_extrema.extrema import block_extrema  # noqa: E402
from tools.block_extrema.report import write_log  # noqa: E402
from tools.block_extrema.source import FrameBarSource, load_frame  # noqa: E402


def main(argv: "list[str] | None" = None) -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--ref", required=True, help="データセット（例 jp225_mt5）")
    ap.add_argument("--tf", required=True, help="時間足（1m/5m/15m/30m/1h/4h/1D/1W/1M）")
    args = ap.parse_args(argv)
    source = FrameBarSource(load_frame(args.ref, args.tf))
    write_log(block_extrema(source), source, sys.stdout)


if __name__ == "__main__":
    main()
