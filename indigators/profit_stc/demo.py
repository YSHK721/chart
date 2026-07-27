"""デモ: 合成 OHLC に PRO!fitSTC を当て matplotlib PNG を生成する。

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
# ISSUE-174: 兄弟パッケージ（moving_averages / mql_builtins / profit_system）の解決点は
#   entry point が持つ（src/core.py 側の sys.path 改変は撤去済み）。
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # = indigators/


def main() -> None:
    from src.plot import plot_stc  # 実行時にのみ matplotlib を import

    rng = np.random.default_rng(11)
    n = 300
    # トレンド転換を含む合成価格（上昇→レンジ→下降）でオシレーターの起伏を見せる。
    t = np.linspace(0, 9, n)
    trend = 1.1000 + 0.012 * np.sin(t * 0.6) + 0.0003 * t
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
    out = plot_stc(
        df,
        out_path=str(Path(__file__).parent / "profit_stc_demo.png"),
        period=70,
        title="PRO!fitSTC (demo / synthetic OHLC)",
    )
    print("saved:", out)


if __name__ == "__main__":
    main()
