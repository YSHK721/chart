"""デモ: 合成 OHLCV に PRO!fitOscillator を当て matplotlib PNG を生成する。

実行: python demo.py
出力: profit_oscillator2_demo.png（別ウィンドウ風 sub_min〜sub_max のレベルカウント・
      ヒストグラム 1 本 ＋ RCI 線 1 本 ＋ σ6 水準線）。

matplotlib 依存はこの demo / src.plot に閉じており、テスト（Fake チャート）には影響しない
（plot は __init__ から除外、本 demo は実行時にのみ src.plot を import する）。
PRO!fitOscillator は iMFI（compute_mfi）を内包し volume を必須とするため、合成データにも
volume 列を含める。
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
    from src.plot import plot_oscillator2  # 実行時にのみ matplotlib を import

    rng = np.random.default_rng(7)
    n = 300
    # トレンド転換を含む合成価格（上昇→レンジ→下降）でヒストグラム/RCI の起伏を見せる。
    t = np.linspace(0, 9, n)
    trend = 1.1000 + 0.012 * np.sin(t * 0.6) + 0.0004 * t
    close = trend + rng.normal(0, 0.0006, n)
    high = np.maximum(close, np.roll(close, 1)) + np.abs(rng.normal(0, 0.0008, n))
    low = np.minimum(close, np.roll(close, 1)) - np.abs(rng.normal(0, 0.0008, n))
    volume = np.abs(rng.normal(1000.0, 250.0, n)) + 100.0  # iMFI のため volume 必須
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
    out = plot_oscillator2(
        df,
        out_path=str(Path(__file__).parent / "profit_oscillator2_demo.png"),
        osc_period=6,
        stc_slow=6,
        ma_period=60,
        rci_period=12,
        direction=False,
        title="PRO!fitOscillator (demo / synthetic OHLCV)",
    )
    print("saved:", out)


if __name__ == "__main__":
    main()
