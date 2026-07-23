"""戦略候補 1〜3 の一括検証（NI225・実データ・費用込み併記）。

1. SMA乖離率 押し目買い（実測記録の再現: ma_type=sma w=50・q5割れ→翌寄買い・5日保有）
   ＋ ナンピン版（q5/med/ext 各段0.66・q5回復決済）
2. オーバーナイト効果（15:00JST買い→翌9:00JST売り, 1h バーで判定）
3. 上側ブレイク順張りロング（q95 上抜け→翌寄買い・h 日保有）
費用: スプレッド 10 円/往復（≈0.015%）。サイズは資金の 0.66 倍（ミニ1枚相当）で統一。
"""
from pathlib import Path as _P
_ROOT = _P(__file__).resolve().parents[3]   # リポジトリ根（…/app）

import importlib.util, sys
from pathlib import Path
import numpy as np
import pandas as pd

sys.path.insert(0, str(_ROOT))
_MM = Path(str(_ROOT / "indigators/ma_marod/src"))
spec = importlib.util.spec_from_file_location("_mm_r2", _MM / "__init__.py",
                                              submodule_search_locations=[str(_MM)])
mm = importlib.util.module_from_spec(spec); sys.modules["_mm_r2"] = mm
spec.loader.exec_module(mm)

W = 0.66            # 資金比サイズ（ミニ1枚相当）
SPREAD = 10.0       # 円/往復

def load(tf):
    df = pd.read_csv(str(_ROOT / f"data/marketdata/rollups/jp225_m1_{tf}.csv"))
    df["ts"] = pd.to_datetime(df["date"])
    df["tsec"] = df["ts"].astype("datetime64[s]").astype("int64")
    return df

def bands_fast(v, window=500, ql=0.05, qh=0.95):
    s = pd.Series(v).shift(1)
    return (s.rolling(window, min_periods=2).quantile(ql).to_numpy(),
            s.rolling(window, min_periods=2).quantile(qh).to_numpy())

def perf(r, tsec, label, since=None):
    r = np.asarray(r, dtype=float)
    if r.size == 0:
        print(f"{label:<34} トレードなし"); return
    eq = np.cumprod(1 + r)
    curve = np.concatenate([[1.0], eq]); peak = np.maximum.accumulate(curve)
    mdd = ((curve - peak) / peak).min() * 100
    yrs = (tsec[-1] - (since if since else tsec[0])) / (365.25 * 86400)
    cagr = (eq[-1] ** (1 / yrs) - 1) * 100
    wr = (r > 0).mean() * 100
    print(f"{label:<34} n={r.size:>5}（月{r.size/yrs/12*12/12:>4.1f}回) 勝率={wr:>5.1f}% "
          f"平均={r.mean()*100:+.3f}% CAGR={cagr:>6.2f}% 最大DD={mdd:>6.1f}%")

# ===== 戦略1: SMA乖離率 押し目買い（sma w=50・5日保有） =====
d = load("1D")
v_sma = mm.ma_marod_series(d, ma_type="sma", length=50)
lo, hi = bands_fast(v_sma)
o, cl, tsec = d["open"].to_numpy(), d["close"].to_numpy(), d["tsec"].to_numpy()
n = len(d)
def xdn(vv, line, t):
    return (np.isfinite(vv[t]) and np.isfinite(line[t]) and np.isfinite(vv[t-1])
            and np.isfinite(line[t-1]) and vv[t] <= line[t] and vv[t-1] > line[t-1])
def xup(vv, line, t):
    return (np.isfinite(vv[t]) and np.isfinite(line[t]) and np.isfinite(vv[t-1])
            and np.isfinite(line[t-1]) and vv[t] > line[t] and vv[t-1] <= line[t-1])
for hold in (5,):
    r = []
    for t in range(1, n - hold - 1):
        if xdn(v_sma, lo, t):
            entry = o[t + 1]
            r.append(W * ((cl[t + hold] - SPREAD) / (entry + SPREAD) - 1))
    perf(r, tsec, f"1) SMA押し目買い 1D 5日保有")
# 2024以降
r = [W * ((cl[t + 5] - SPREAD) / (o[t + 1] + SPREAD) - 1)
     for t in range(1, n - 6) if tsec[t] >= 1704067200 and xdn(v_sma, lo, t)]
perf(np.array(r), tsec, "   同・2024年〜", since=1704067200)
# 4h 版
d4 = load("4h")
v4 = mm.ma_marod_series(d4, ma_type="sma", length=50)
lo4, hi4 = bands_fast(v4)
o4, cl4, t4 = d4["open"].to_numpy(), d4["close"].to_numpy(), d4["tsec"].to_numpy()
r = [W * ((cl4[t + 5] - SPREAD) / (o4[t + 1] + SPREAD) - 1)
     for t in range(1, len(d4) - 6) if xdn(v4, lo4, t)]
perf(np.array(r), t4, "1) SMA押し目買い 4h 5本保有")

# ===== 戦略2: オーバーナイト（15:00JST→翌9:00JST） =====
h1 = load("1h")
h1["hour_utc"] = h1["ts"].dt.hour
h1["d"] = h1["ts"].dt.normalize()
# 日中セッション: 00:00-05:00 UTC(9:00-15:00JST)。close(05:00バー)→翌日の open(00:00バー)
day_open = h1[h1["hour_utc"] == 0].set_index("d")["open"]
day_close = h1[h1["hour_utc"] == 5].set_index("d")["close"]
days = sorted(set(day_open.index) & set(day_close.index))
on_r, id_r, on_t = [], [], []
for i in range(len(days) - 1):
    d0, d1 = days[i], days[i + 1]
    if d1 in day_open.index and d0 in day_close.index:
        on_r.append(W * ((day_open[d1] - SPREAD) / (day_close[d0] + SPREAD) - 1))   # 夜間
        id_r.append(W * (day_close[d1] / day_open[d1] - 1))                          # 日中（参考・費用なし）
        on_t.append(int(pd.Timestamp(d1).timestamp()))
on_t = np.array(on_t)
perf(np.array(on_r), on_t, "2) オーバーナイト保有（費用込）")
perf(np.array(id_r), on_t, "   参考: 日中保有（費用なし）")
r24 = np.array(on_r)[on_t >= 1704067200]
perf(r24, on_t, "   同・2024年〜", since=1704067200)

# ===== 戦略3: 上側ブレイク順張りロング（既定 ema50・q95上抜け→翌寄買い） =====
v_e = mm.ma_marod_series(d)          # 既定 ema50
loE, hiE = bands_fast(v_e)
for hold in (5, 10, 20):
    r = [W * ((cl[t + hold] - SPREAD) / (o[t + 1] + SPREAD) - 1)
         for t in range(1, n - hold - 1) if xup(v_e, hiE, t)]
    perf(np.array(r), tsec, f"3) 上抜け順張り 1D {hold}日保有")

# ===== 戦略1 忠実再現: 毎バー・トランシェ方式（バンド割れの全バーで 1/5 ずつ・5日保有） =====
print("\n-- 戦略1 忠実再現（毎バー 1/5 トランシェ・重複保有・費用込） --")
for label, vv, band in (("SMA w=50", v_sma, lo),):
    r = []
    ts_list = []
    for t in range(1, n - 6):
        if np.isfinite(vv[t]) and np.isfinite(band[t]) and vv[t] <= band[t]:
            entry = o[t + 1]
            r.append(0.2 * ((cl[t + 5] - SPREAD) / (entry + SPREAD) - 1))  # 1/5 資金
            ts_list.append(tsec[t])
    r = np.array(r); ts_a = np.array(ts_list)
    perf(r, tsec, f"1') {label} 毎バー1/5")
    perf(r[ts_a >= 1704067200], tsec, "    同・2024年〜", since=1704067200)
    # 1回あたり素リターン（サイズ・費用調整前）＝過去記録の +60bp/回 と比較する値
    raw = [(cl[t + 5] / o[t + 1] - 1) for t in range(1, n - 6)
           if np.isfinite(vv[t]) and np.isfinite(band[t]) and vv[t] <= band[t]]
    raw = np.array(raw)
    print(f"    素リターン/回（費用前）: 平均={raw.mean()*10000:+.0f}bp（過去記録の再現目標 +60bp 前後）")
