"""デモ: 合成 OHLCV に PRO!fit_Oscillator を当て matplotlib PNG を生成する。

実行: python demo.py
（matplotlib 未導入環境ではテストに影響しない。本ファイルはテスト非依存。）
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))

from src.plot import plot_oscillator  # noqa: E402


def main() -> None:
    rng = np.random.default_rng(7)
    n = 300
    # トレンド転換を含む合成価格（上昇→レンジ→下降）でヒストグラムの起伏を見せる。
    t = np.linspace(0, 9, n)
    trend = 1.1000 + 0.010 * np.sin(t * 0.7) + 0.0004 * t
    close = trend + rng.normal(0, 0.0006, n)
    open_ = np.roll(close, 1)
    open_[0] = close[0]
    high = np.maximum(open_, close) + np.abs(rng.normal(0, 0.0008, n))
    low = np.minimum(open_, close) - np.abs(rng.normal(0, 0.0008, n))
    volume = np.abs(rng.normal(1000, 200, n))
    df = pd.DataFrame(
        {
            "time": pd.date_range("2024-01-01", periods=n, freq="h"),
            "open": open_,
            "high": high,
            "low": low,
            "close": close,
            "volume": volume,
        }
    )
    out = plot_oscillator(
        df,
        out_path=str(Path(__file__).parent / "profit_oscillator_demo.png"),
        period_a=6,
        period_b=60,
        title="PRO!fit_Oscillator (demo / synthetic OHLCV)",
    )
    print("saved:", out)


if __name__ == "__main__":
    main()
