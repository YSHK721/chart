"""デモ: 合成 OHLC に PRO!fitHLBand を当て matplotlib PNG を生成する。

実行: python demo.py
matplotlib 未導入環境ではテストに影響しない設計（plot は __init__ から除外、本 demo
は実行時にのみ src.plot を import する）。

層名/責務:
    出力アダプタのデモエントリ。計算は成果物層、描画は plot 層へ委譲する。
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))


def main() -> None:
    from src.plot import plot_hlband  # 実行時にのみ matplotlib を import

    rng = np.random.default_rng(7)
    n = 300
    # 上昇→レンジ→下降の合成価格で hl_range の起伏と σ 帯を見せる。
    t = np.linspace(0, 9, n)
    trend = 1.1000 + 0.012 * np.sin(t * 0.6) + 0.0003 * t
    close = trend + rng.normal(0, 0.0006, n)
    high = np.maximum(close, np.roll(close, 1)) + np.abs(rng.normal(0, 0.0009, n))
    low = np.minimum(close, np.roll(close, 1)) - np.abs(rng.normal(0, 0.0009, n))
    df = pd.DataFrame(
        {
            "time": pd.date_range("2024-01-01", periods=n, freq="h"),
            "open": np.roll(close, 1),
            "high": high,
            "low": low,
            "close": close,
        }
    )
    out = plot_hlband(
        df,
        out_path=str(Path(__file__).parent / "profit_hlband_demo.png"),
        title="PRO!fitHLBand (demo / synthetic OHLC)",
    )
    print("saved:", out)


if __name__ == "__main__":
    main()
