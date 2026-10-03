"""バー系列の読み込み（全期間）。

1m は ``marketdata.dataset.load_atom_window``（全期間・外れ値補正済み）を使う。
上位足はロールアップ CSV を全件読み、1m と同じ補正 ``dataset._clamp_outlier_bars``
（補正の唯一の定義）を通す。``dataset.load_dataframe`` / ``rollup_store.read`` は末尾だけを
返すので使わない。
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from marketdata import dataset, rollup_store
from marketdata.ohlc_csv_loader import load_ohlc_csv

ATOM_TIMEFRAME = "1m"
_ALL_TIME = (0, 2 ** 62)


class FrameBarSource:
    """時刻 index と high/low 列を持つ DataFrame を ``tools.block_extrema.extrema.BarSource`` として読む。"""

    def __init__(self, frame: pd.DataFrame) -> None:
        self._frame = frame

    def __len__(self) -> int:
        return len(self._frame)

    def highs(self) -> np.ndarray:
        return self._frame["high"].to_numpy()

    def lows(self) -> np.ndarray:
        return self._frame["low"].to_numpy()

    def times(self, indices: np.ndarray) -> np.ndarray:
        return self._frame.index.values[indices]


def load_frame(ref: str, timeframe: str) -> pd.DataFrame:
    """ref と時間足の全期間バーを返す。"""
    if timeframe == ATOM_TIMEFRAME:
        return dataset.load_atom_window(ref, *_ALL_TIME)
    frame = load_ohlc_csv(rollup_store.path(ref, timeframe), time_column="date")
    return dataset._clamp_outlier_bars(frame, ref)
