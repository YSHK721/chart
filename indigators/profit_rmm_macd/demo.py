"""デモ: 合成 OHLCV に PRO!fitRMMMACD を当て matplotlib PNG を生成する。

実行: python demo.py
matplotlib 未導入環境ではテストに影響しない設計（plot は __init__ から除外、本 demo
は実行時にのみ src.plot を import する）。RMMMACD は level_count 算出に iMFI（出来高
加重）を含むため volume を必須とするので、合成データにも volume 列を含める。

層名/責務:
    出力アダプタのデモエントリ。計算は成果物層、描画は plot 層へ委譲する。
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


def main() -> None:
    from src.plot import plot_rmmmacd  # 実行時にのみ matplotlib を import

    rng = np.random.default_rng(13)
    n = 300
    # トレンド転換を含む合成価格（上昇→レンジ→下降）で MACD の起伏を見せる。
    t = np.linspace(0, 9, n)
    trend = 1.1000 + 0.012 * np.sin(t * 0.6) + 0.0003 * t
    close = trend + rng.normal(0, 0.0006, n)
    high = np.maximum(close, np.roll(close, 1)) + np.abs(rng.normal(0, 0.0008, n))
    low = np.minimum(close, np.roll(close, 1)) - np.abs(rng.normal(0, 0.0008, n))
    # 合成 volume（正値・MT4 チャート出来高相当）。
    volume = np.abs(rng.normal(1000.0, 250.0, n)) + 100.0
    df = pd.DataFrame(
        {
            "time": pd.date_range("2024-01-01", periods=n, freq="h"),
            "open": np.roll(close, 1),
            "high": high,
            "low": low,
            "close": close,
            "volume": volume,
        }
    )
    out = plot_rmmmacd(
        df,
        out_path=str(Path(__file__).parent / "profit_rmm_macd_demo.png"),
        osc_period=6,
        ma_period=6,
        fast=4,
        slow=8,
        signal=4,
        title="PRO!fitRMMMACD (demo / synthetic OHLCV)",
    )
    print("saved:", out)


if __name__ == "__main__":
    main()
