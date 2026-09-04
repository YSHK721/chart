"""デモ: 合成 OHLC に PRO!fit_Volatility を当て matplotlib PNG を生成する。

実行: python demo.py
（matplotlib 未導入環境ではテストに影響しない。本ファイルはテスト非依存。）
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
# ISSUE-174: 兄弟パッケージ（moving_averages / mql_builtins / profit_system）の解決点は
#   entry point が持つ（src/core.py 側の sys.path 改変は撤去済み）。
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # = indigators/

from src.plot import plot_volatility  # noqa: E402


def main() -> None:
    rng = np.random.default_rng(7)
    n = 200
    # トレンド転換を含む合成価格（上昇→レンジ→下降）でヒストグラムの起伏を見せる。
    t = np.linspace(0, 9, n)
    trend = 1.1000 + 0.010 * np.sin(t * 0.7) + 0.0004 * t
    close = trend + rng.normal(0, 0.0006, n)
    high = np.maximum(close, np.roll(close, 1)) + np.abs(rng.normal(0, 0.0008, n))
    low = np.minimum(close, np.roll(close, 1)) - np.abs(rng.normal(0, 0.0008, n))
    df = pd.DataFrame(
        {
            "time": pd.date_range("2024-01-01", periods=n, freq="h"),
            "open": np.roll(close, 1),
            "high": high,
            "low": low,
            "close": close,
        }
    )
    out = plot_volatility(
        df,
        out_path=str(Path(__file__).parent / "profit_volatility_demo.png"),
        period=6,
        title="PRO!fit_Volatility (demo / synthetic OHLC)",
    )
    print("saved:", out)


if __name__ == "__main__":
    main()
