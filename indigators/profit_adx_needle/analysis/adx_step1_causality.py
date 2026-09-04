"""Step 1: profit_adx_needle の因果性（repaint しないこと）を実測で確定する。

有意性検証の前提条件。指標が未来を参照していれば、以降の予測力測定は全て無効。

手法: 系列長 n の全期間計算と、prefix[0:k] のみで計算した末尾値を突き合わせる
（＝実運用で足 k が確定した瞬間に見える値と、後から見た値が一致するか）。
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(_ROOT))
sys.path.insert(0, str(_ROOT / "indigators"))

from profit_adx_needle.src.core import (  # noqa: E402
    DEFAULT_PERIOD,
    DEFAULT_WINDOW,
    compute_adx,
    compute_adx_needle,
    compute_level_count,
)


def load_tf(tf: str) -> pd.DataFrame:
    p = _ROOT / "data/marketdata/rollups" / f"jp225_m1_{tf}.csv"
    df = pd.read_csv(p, parse_dates=["date"])
    df = df.dropna(subset=["open", "high", "low", "close"]).reset_index(drop=True)
    return df


def main() -> None:
    df = load_tf("1D")
    h, l, c = df["high"].to_numpy(), df["low"].to_numpy(), df["close"].to_numpy()
    n = len(df)
    print(f"[data] 1D bars={n} {df['date'].iloc[0].date()} .. {df['date'].iloc[-1].date()}")

    full = compute_adx_needle(h, l, c, DEFAULT_PERIOD, window=DEFAULT_WINDOW)
    print(f"[param] period={DEFAULT_PERIOD} window={DEFAULT_WINDOW}")

    # --- 1-A: ADX 本体の因果性 -------------------------------------------------
    ks = list(range(n - 300, n, 7))
    adx_dev = []
    lvl_dev = []
    ndl_dev = []
    for k in ks:
        pref = compute_adx_needle(h[:k], l[:k], c[:k], DEFAULT_PERIOD, window=DEFAULT_WINDOW)
        adx_dev.append(abs(pref.adx[-1] - full.adx[k - 1]))
        lvl_dev.append(abs(pref.level_count[-1] - full.level_count[k - 1]))
        ndl_dev.append(abs(pref.needle[-1] - full.needle[k - 1]))
    adx_dev = np.array(adx_dev)
    lvl_dev = np.array(lvl_dev)
    ndl_dev = np.array(ndl_dev)

    print("\n=== 1-A: prefix 再計算 vs 全期間（末尾値の一致）· n_test=%d ===" % len(ks))
    for name, d in (("adx", adx_dev), ("level_count", lvl_dev), ("needle", ndl_dev)):
        print(f"  {name:12s} max|diff|={d.max():.3e}  mean|diff|={d.mean():.3e}  "
              f"非一致数(>1e-9)={int((d > 1e-9).sum())}/{len(d)}")

    # --- 1-B: needle のクランプ境界が全期間統計に依存するか -------------------
    print("\n=== 1-B: クランプ境界（compute_sigma_levels）の look-ahead ===")
    print(f"  全期間 upper={full.upper_clamp:.5f} lower={full.lower_clamp:.5f}")
    for k in (int(n * 0.5), int(n * 0.75), n):
        pref = compute_adx_needle(h[:k], l[:k], c[:k], DEFAULT_PERIOD, window=DEFAULT_WINDOW)
        print(f"  prefix k={k:5d}  upper={pref.upper_clamp:.5f} lower={pref.lower_clamp:.5f}")
    lc = full.level_count
    fin = np.isfinite(lc)
    clipped = fin & ((lc > full.upper_clamp) | (lc < full.lower_clamp))
    print(f"  クランプが実際に効いた本数: {int(clipped.sum())}/{int(fin.sum())} "
          f"({100.0 * clipped.sum() / max(1, fin.sum()):.3f}%)")

    # --- 1-C: warm-up / 有効本数 ----------------------------------------------
    print("\n=== 1-C: 有効本数 ===")
    print(f"  level_count 有効={int(np.isfinite(lc).sum())} / {n}  "
          f"先頭 NaN={int(np.argmax(np.isfinite(lc)))}")

    # --- 1-D: level_count と adx の関係（設計どおり 7×z か） -------------------
    print("\n=== 1-D: level_count ≒ 7×(ADX の因果 z) の確認 ===")
    lc_only = compute_level_count(h, l, c, DEFAULT_PERIOD, window=DEFAULT_WINDOW)
    adx = compute_adx(h, l, c, DEFAULT_PERIOD)
    W = DEFAULT_WINDOW
    s = pd.Series(adx)
    zr = (s - s.rolling(W).mean()) / s.rolling(W).std(ddof=0)
    z7 = (7.0 * zr).to_numpy()
    m = np.isfinite(lc_only) & np.isfinite(z7)
    print(f"  corr(level_count, 7z)={np.corrcoef(lc_only[m], z7[m])[0,1]:.10f}  "
          f"max|diff|={np.abs(lc_only[m] - z7[m]).max():.3e}")
    print(f"  ⇒ needle は ADX(6) の因果 z スコアの単調変換（方向性を持たない量）")


if __name__ == "__main__":
    main()
