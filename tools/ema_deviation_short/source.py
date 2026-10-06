"""MT5 エクスポート CSV（タブ区切り・<DATE> <TIME> <OPEN> … <SPREAD>）を `BarSource` にする。"""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd


class FrameBarSource:
    def __init__(self, frame: pd.DataFrame) -> None:
        self._frame = frame

    def __len__(self) -> int:
        return len(self._frame)

    def opens(self) -> np.ndarray:
        return self._frame["open"].to_numpy(float)

    def highs(self) -> np.ndarray:
        return self._frame["high"].to_numpy(float)

    def closes(self) -> np.ndarray:
        return self._frame["close"].to_numpy(float)

    def spreads(self) -> np.ndarray:
        return self._frame["spread"].to_numpy(float)

    def date(self, index: int) -> str:
        return str(self._frame["date"].iloc[index])


def load_mt5_csv(path: "str | Path") -> pd.DataFrame:
    frame = pd.read_csv(path, sep="\t")
    frame.columns = [c.strip("<>").lower() for c in frame.columns]
    return frame
