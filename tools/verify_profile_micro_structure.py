#!/usr/bin/env python3
"""tf 横断で profile の"分布の豊かさ"を最小価格単位で実測する（ミクロ退行の有無を実データで判定）。

背景:
    「短周期(1m)の profile は分布が成立せず退行する」という理論的主張は、**固定/粗ビンで測った場合の
    アーティファクト**でありうる。占有ビン数はビン幅に依存する相対量のため、短周期は価格幅が小さく
    粗ビンでは1〜2ビンに潰れて見えるが、それは分布が無い証拠にならない。

方法:
    銘柄の**最小価格単位**（実データの最小 mid 増分）でビニングし、各周期の
    「占有レベル数（distinct 価格）／tick 数／価格レンジ」を測る。粗ビンの交絡を排除して
    「その周期が実際に何本の異なる価格レベルに触れたか」の絶対量を得る。

安全性: ティック parquet を読むだけ（書き込み無し）。

実行:
    python tools/verify_profile_micro_structure.py [YYYY-MM-DD]   # 既定 2026-07-08
"""
from __future__ import annotations

import datetime as dt
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

from marketdata.paths import DATA_DIR  # noqa: E402
from marketdata.tick_m1 import day_parquet_path  # noqa: E402

TFS = {"1m": 60, "5m": 300, "15m": 900, "1h": 3600, "1D": 86400}


def analyze(day: dt.date) -> None:
    df = pd.read_parquet(
        day_parquet_path(day, data_dir=DATA_DIR), columns=["timestamp", "bidPrice", "askPrice"]
    )
    # ms 精度の datetime を ns 正規化して UNIX 秒へ。
    ts = (
        pd.to_datetime(df["timestamp"], utc=True).dt.tz_localize(None)
        .astype("datetime64[ns]").astype("int64") // 1_000_000_000
    ).to_numpy()
    mid = ((df["bidPrice"].astype(float) + df["askPrice"].astype(float)) / 2.0).to_numpy()
    print(f"day={day}  ticks={len(mid):,}")

    # 最小価格単位＝実データの最小正の mid 増分（distinct mid の最小ギャップ）。
    u = np.unique(mid)
    gaps = np.diff(u)
    gaps = gaps[gaps > 1e-9]
    unit = float(gaps.min()) if len(gaps) else 1.0
    print(f"distinct mid={len(u):,}  最小価格単位={unit:.6g}  (レンジ {u.min():.1f}..{u.max():.1f})")
    print("=" * 92)
    q = np.round(mid / unit).astype(np.int64)  # 最小単位で量子化＝占有レベルの単位。

    print(f"{'tf':>4} | {'active':>7} | {'tick中央':>9} | {'占有ﾚﾍﾞﾙ中央':>12} | "
          f"{'ﾚﾝｼﾞ中央':>9} | {'退行%(<=2)':>10} | {'構造%(>=5)':>10}")
    print("-" * 92)
    for tf, sec in TFS.items():
        period = (ts // sec) * sec
        g = pd.DataFrame({"period": period, "q": q, "mid": mid}).groupby("period")
        occ = g["q"].nunique().to_numpy()
        ntick = g.size().to_numpy()
        rng = (g["mid"].max() - g["mid"].min()).to_numpy()
        print(f"{tf:>4} | {len(occ):>7,} | {np.median(ntick):>9.0f} | {np.median(occ):>12.0f} | "
              f"{np.median(rng):>9.2f} | {100.0 * np.mean(occ <= 2):>9.1f}% | {100.0 * np.mean(occ >= 5):>9.1f}%")


def main() -> None:
    day = dt.datetime.strptime(sys.argv[1], "%Y-%m-%d").date() if len(sys.argv) > 1 else dt.date(2026, 7, 8)
    analyze(day)


if __name__ == "__main__":
    main()
