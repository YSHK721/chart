"""M6. 形成中バーのバイアスを 4h 以上でも測る（ISSUE-454 の未測定 2）。

M4 は API 上限（1m 50,000 本＝52 日）に縛られ 5m/15m/1h までだった。1m の実体
`data/marketdata/jp225_tick_m1.csv` は 2012 年以降 3,228 万行あるので、これを直接
読んで 4h / 1D / 1W / 1M の部分足を再構成する（読むだけ・無改変）。

測るもの（M4 と同一の定義）:
  a. 部分足を「確定足の分布」へ当てた分位 p の中央値（現状＝バイアスの大きさ）
  b. 確定足だけの基準
  c. 部分足を「同じ経過割合の部分足の分布」へ当てた分位（是正後）
"""
import os
import sys

import numpy as np
import pandas as pd

ROOT = os.environ.get("ISSUE449_ROOT", "/workspaces/app")
sys.path.insert(0, ROOT)  # repo 根を解決（同ディレクトリの他 probe と同一様式）。

from marketdata import keep_last  # noqa: E402  (sys.path 設定後に解決する)

CSV = os.path.join(ROOT, "data/marketdata/jp225_tick_m1.csv")
WINDOW = 500
FRACS = [0.1, 0.2, 0.3, 0.5, 0.7, 0.9, 1.0]
# 足の長さ（秒）と、その足を構成する 1m 本数
PERIOD = {"4h": 4 * 3600, "1D": 24 * 3600}
CAL = ["1W", "1M"]   # 週・月は暦で束ねる


def load():
    """date と volume だけを読む（volume＝当該 1 分に到来した tick 数）。"""
    it = pd.read_csv(CSV, usecols=["date", "volume"], parse_dates=["date"],
                     chunksize=2_000_000)
    fr = [c for c in it]
    df = pd.concat(fr, ignore_index=True)
    df = df.dropna(subset=["volume"])
    # 実体 CSV は同一履歴が 8 回連結されている（ISSUE-455）。時刻で一意化し、
    # 最後の出現（最新の取り込み）を採る。読むだけで CSV は変更しない。
    before = len(df)
    # keep-last の規則は marketdata.keep_last（唯一の実体・ISSUE-479 F-6）へ委譲する。
    df = keep_last.dedupe_column_keep_last(df, "date").sort_values("date")
    df = df.reset_index(drop=True)
    print(f"重複除去: {before:,} -> {len(df):,} 本")
    return df


def causal_pct_against(ref, probe, window_n):
    n = probe.size
    out = np.full(n, np.nan)
    for t in range(n):
        w = ref[max(0, t - window_n):t]
        w = w[np.isfinite(w)]
        if w.size >= 2 and np.isfinite(probe[t]):
            out[t] = float(np.count_nonzero(w < probe[t])) / w.size
    return out


def report(name, groups):
    """groups: 各足の 1m tick 数の配列（時刻順）のリスト。"""
    groups = [g for g in groups if g.size >= 2]
    full = np.array([g.sum() for g in groups], dtype=float)
    partial = {}
    for f in FRACS:
        partial[f] = np.array(
            [g[:max(1, int(round(g.size * f)))].sum() for g in groups], dtype=float)

    a = f"{name:5s} 現状 "
    c = f"{name:5s} 是正 "
    for f in FRACS:
        p = causal_pct_against(full, partial[f], WINDOW)
        p = p[np.isfinite(p)]
        a += f" {np.median(p):8.3f}" if p.size else f" {'—':>8s}"
        q = causal_pct_against(partial[f], partial[f], WINDOW)
        q = q[np.isfinite(q)]
        c += f" {np.median(q):8.3f}" if q.size else f" {'—':>8s}"
    b = causal_pct_against(full, full, WINDOW)
    b = b[np.isfinite(b)]
    print(a + f"   足 {len(groups)}")
    print(c + f"   確定足のみの基準 {np.median(b):.3f}" if b.size else c)
    print()


print("1m CSV を読み込み中…")
df = load()
print(f"1m {len(df):,} 本  {df['date'].iloc[0]} 〜 {df['date'].iloc[-1]}\n")
ts = df["date"].values.astype("datetime64[s]").astype(np.int64)
vol = df["volume"].to_numpy(dtype=float)

print(f"{'足':5s} {'':4s} " + " ".join(f"{'f='+format(f,'.1f'):>8s}" for f in FRACS))
print("-" * 84)

for tf, per in PERIOD.items():
    key = ts // per
    idx = np.flatnonzero(np.diff(key)) + 1
    groups = np.split(vol, idx)
    n_expect = per // 60
    groups = [g for g in groups if g.size >= n_expect * 0.5]   # 欠損の多い足は除く
    report(tf, groups)

d = pd.DatetimeIndex(df["date"])
for tf in CAL:
    key = (d.to_period("W") if tf == "1W" else d.to_period("M")).astype(str).values
    chg = np.flatnonzero(key[1:] != key[:-1]) + 1
    groups = np.split(vol, chg)
    groups = [g for g in groups if g.size >= 500]
    report(tf, groups)
