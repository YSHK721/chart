"""デモ: 合成 OHLCV に PRO!fitRMM を当て matplotlib PNG を生成する。

実行: python demo.py
出力: profit_rmm_demo.png（別ウィンドウ風 [-10,10] のレベルカウント・ヒストグラム）。

matplotlib 依存はこの demo / src.plot に閉じており、テスト（Fake チャート）には影響しない。
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

from src.plot import plot_rmm  # noqa: E402


def main() -> None:
    rng = np.random.default_rng(7)
    n = 200
    # トレンド転換を含む合成価格（上昇→レンジ→下降）でヒストグラムの起伏を見せる。
    t = np.linspace(0, 9, n)
    trend = 1.1000 + 0.010 * np.sin(t * 0.7) + 0.0004 * t
    close = trend + rng.normal(0, 0.0006, n)
    high = np.maximum(close, np.roll(close, 1)) + np.abs(rng.normal(0, 0.0008, n))
    low = np.minimum(close, np.roll(close, 1)) - np.abs(rng.normal(0, 0.0008, n))
    volume = rng.integers(100, 2000, n).astype(float)  # RMM は iMFI のため volume 必須
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
    out = plot_rmm(
        df,
        out_path=str(Path(__file__).parent / "profit_rmm_demo.png"),
        osc_period=6,
        ma_period=6,
        title="PRO!fitRMM (demo / synthetic OHLCV)",
    )
    print("saved:", out)


if __name__ == "__main__":
    main()
