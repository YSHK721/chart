"""ログ出力。各ブロックにつき ``bar,yyyy/mm/ddThh:mm:ss,high`` と同形式の low の 2 行。"""
from __future__ import annotations

from typing import Iterable, TextIO

import numpy as np
import pandas as pd

from tools.block_extrema.extrema import BarSource, LevelExtrema

TIME_FORMAT = "%Y/%m/%dT%H:%M:%S"


def write_log(levels: Iterable[LevelExtrema], source: BarSource, out: TextIO) -> None:
    """段の昇順・ブロックの時刻順に、高値行・安値行の順で書く。"""
    for level in levels:
        at = np.column_stack([level.high_at, level.low_at]).ravel()
        rows = pd.DataFrame({
            "bar": np.repeat(level.bars, 2),
            "time": pd.DatetimeIndex(source.times(at)).strftime(TIME_FORMAT),
            "price": np.column_stack([level.high, level.low]).ravel(),
        })
        rows.to_csv(out, header=False, index=False, lineterminator="\n")
