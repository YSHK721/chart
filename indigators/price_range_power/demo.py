"""デモ: 合成 OHLC に価格帯別ブルベアレシオを当て matplotlib PNG を生成する。

外部データ・R 等に依存せず実行できる。価格帯プロファイル（度数 + ブル/ベア勢力）を
PNG に書き出す。

実行: python demo.py
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))

from src import build_price_range_power  # noqa: E402
from src.plot import plot_price_range_power  # noqa: E402


def main() -> None:
    rng = np.random.default_rng(7)
    n = 600
    # レンジ内を往復する合成価格（支持/抵抗が偏るようにうねりを与える）。
    t = np.linspace(0, 18, n)
    base = 110.0 + 1.5 * np.sin(t) + 0.6 * np.sin(t * 0.37) + rng.normal(0, 0.25, n)
    o = base
    c = base + rng.normal(0, 0.25, n)
    h = np.maximum(o, c) + rng.uniform(0.0, 0.6, n)
    low = np.minimum(o, c) - rng.uniform(0.0, 0.6, n)
    df = pd.DataFrame({"open": o, "high": h, "low": low, "close": c})

    res = build_price_range_power(df, interval=0.1)
    print(f"bands: {len(res)}  columns: {len(res.columns)}")
    print("上位 total（ブル/ベア勢力が集中する価格帯）:")
    print(res["total"].sort_values(ascending=False).head(5).to_string())

    out = plot_price_range_power(
        df,
        out_path=str(Path(__file__).parent / "price_range_power_demo.png"),
        interval=0.1,
        title="PriceRangePower (demo / synthetic OHLC)",
    )
    print("saved:", out)


if __name__ == "__main__":
    main()
