"""デモ: 合成価格に btlm バンドを当て matplotlib PNG を生成する。

R/rpy2 が無い環境でも実行できるよう、既定では numpy 参照実装 OlsBtlmFitter を使う。
忠実なベイズ木構造線形モデルで描く場合は TgpBtlmFitter（要 R + tgp + rpy2）へ差し替える。

実行: python demo.py
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))

from src import OlsBtlmFitter  # noqa: E402
from src.plot import plot_btlm  # noqa: E402


def main() -> None:
    rng = np.random.default_rng(7)
    n = 160
    # トレンド + うねり + ノイズの合成 Open 系列。
    t = np.linspace(0, 6, n)
    open_ = 1.1000 + 0.004 * np.sin(t) + 0.0006 * t + rng.normal(0, 0.0008, n)
    df = pd.DataFrame(
        {
            "time": pd.date_range("2024-01-01", periods=n, freq="h"),
            "open": open_,
            "high": open_ + 0.0003,
            "low": open_ - 0.0003,
            "close": open_ + rng.normal(0, 0.0004, n),
        }
    )
    out = plot_btlm(
        df, OlsBtlmFitter(), out_path=str(Path(__file__).parent / "tgp_btlm_demo.png"),
        maxbars=100, q_low=0.05, q_high=0.95,
        title="!!R-tgp.BTLM-Ind (demo / OLS reference fitter)",
    )
    print("saved:", out)


if __name__ == "__main__":
    main()
