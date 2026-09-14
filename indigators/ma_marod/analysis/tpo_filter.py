"""戦略4: TPO 集中度（検定済み data_prep で生成）を戦略1'/3 のフィルタとして A/B 検定。

集中度は Step3 で「翌日 RV へ負の増分情報」確定済み（高集中→翌日静穏）。
フィルタ仮説（新規・A/B）: 前営業日の集中度の高低で当日シグナルの成績が変わるか。
分割は直近 250 日ローリング中央値（因果）。
"""
from pathlib import Path as _P
_ROOT = _P(__file__).resolve().parents[3]   # リポジトリ根（…/app）

import sys
sys.path.insert(0, str(_ROOT))
sys.path.insert(0, str(_ROOT / "indigators/market_profile/analysis"))
import numpy as np
import pandas as pd
from mp_stats.data_prep import load_m1, build_session_data, build_daily_features, PRIMARY

m1 = load_m1(str(_ROOT / "data/marketdata/jp225_m1.csv"))
sd = build_session_data(m1)
f = build_daily_features(sd, variants=(PRIMARY,), primary=PRIMARY)
conc = f.conc[PRIMARY.key].astype(float)
days = pd.to_datetime(f.day, unit="s")   # セッション日（epoch 秒）
cs = pd.Series(conc, index=days.normalize())
med = cs.shift(1).rolling(250, min_periods=100).median()   # 因果ローリング中央値
hi_flag = (cs.shift(1) > med)          # 前営業日の集中度が高いか（当日朝に既知＝因果）
print(f"集中度系列: {len(cs)} 日  高集中フラグ率={hi_flag.mean():.2f}")

# ---- 戦略側（strategies_all.py の定義を再利用） ----
import importlib.util
from pathlib import Path
_MM = Path(str(_ROOT / "indigators/ma_marod/src"))
spec = importlib.util.spec_from_file_location("_mm_r3", _MM / "__init__.py",
                                              submodule_search_locations=[str(_MM)])
mm = importlib.util.module_from_spec(spec); sys.modules["_mm_r3"] = mm
spec.loader.exec_module(mm)

d = pd.read_csv(str(_ROOT / "data/marketdata/rollups/jp225_m1_1D.csv"))
d["ts"] = pd.to_datetime(d["date"]).dt.normalize()
o, cl = d["open"].to_numpy(), d["close"].to_numpy()
n = len(d)
SPREAD = 10.0

def flag_for(ts):
    v = hi_flag.get(ts, None)
    return None if v is None or (isinstance(v, float) and np.isnan(v)) else bool(v)

def ab(entries, label):
    """entries: list of (t_signal, ret)。前営業日集中度 高/低 で分割し平均差を検定。"""
    hi, lo_ = [], []
    for t, r in entries:
        fl = flag_for(d["ts"].iloc[t])
        if fl is True:
            hi.append(r)
        elif fl is False:
            lo_.append(r)
    hi, lo_ = np.array(hi), np.array(lo_)
    if len(hi) < 10 or len(lo_) < 10:
        print(f"{label}: 標本不足 hi={len(hi)} lo={len(lo_)}"); return
    diff = hi.mean() - lo_.mean()
    se = np.sqrt(hi.var(ddof=1)/len(hi) + lo_.var(ddof=1)/len(lo_))
    print(f"{label}: 高集中後 n={len(hi)} 平均={hi.mean()*100:+.3f}% / 低集中後 n={len(lo_)} "
          f"平均={lo_.mean()*100:+.3f}%  差={diff*100:+.3f}% (t≈{diff/se:.2f})")

# 戦略1': SMA押し目買い（毎バー 1/5 トランシェ・5日保有・素の資金比 0.2）
v_sma = mm.ma_marod_series(d, ma_type="sma", length=50)
s = pd.Series(v_sma).shift(1)
lo_b = s.rolling(500, min_periods=2).quantile(0.05).to_numpy()
ent1 = [(t, 0.2 * ((cl[t+5]-SPREAD)/(o[t+1]+SPREAD) - 1)) for t in range(1, n-6)
        if np.isfinite(v_sma[t]) and np.isfinite(lo_b[t]) and v_sma[t] <= lo_b[t]]
ab(ent1, "戦略1' SMA押し目買い")

# 戦略3: 上抜け順張り 20日保有（ema50 既定・0.66倍）
v_e = mm.ma_marod_series(d)
se_ = pd.Series(v_e).shift(1)
hi_b = se_.rolling(500, min_periods=2).quantile(0.95).to_numpy()
ent3 = []
for t in range(1, n-21):
    if (np.isfinite(v_e[t]) and np.isfinite(hi_b[t]) and np.isfinite(v_e[t-1])
            and np.isfinite(hi_b[t-1]) and v_e[t] > hi_b[t] and v_e[t-1] <= hi_b[t-1]):
        ent3.append((t, 0.66 * ((cl[t+20]-SPREAD)/(o[t+1]+SPREAD) - 1)))
ab(ent3, "戦略3 上抜け順張り20日")
