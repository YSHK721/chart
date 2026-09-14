"""多時間足検証: ma_marod 下方バンド・ナンピン戦略（各段 0.66 倍・ミニ1枚相当）。

パイプライン: marod=実 core（ma_marod_series）／正常バンド=pandas rolling（等価性を 1D で
サーバ出力と突合検証）／イベント分位=production ロジック同等（episode・K 窓のみ・_all 省略）。
コスト: スプレッド 10 円/枚 往復（想定元本比 ≈0.015%/段）を控除するケースも併記。
"""
from pathlib import Path as _P
_ROOT = _P(__file__).resolve().parents[3]   # リポジトリ根（…/app）

import importlib.util, json, sys, urllib.request
from pathlib import Path
import numpy as np
import pandas as pd

sys.path.insert(0, str(_ROOT))
_MM = Path(str(_ROOT / "indigators/ma_marod/src"))
spec = importlib.util.spec_from_file_location("_mm_research", _MM / "__init__.py",
                                              submodule_search_locations=[str(_MM)])
mm = importlib.util.module_from_spec(spec); sys.modules["_mm_research"] = mm
spec.loader.exec_module(mm)

Q_LOW, Q_HIGH, WINDOW, Q_OUT, K, MIN_EV = 0.05, 0.95, 500, 0.99, 50, 5

def bands_fast(v):
    s = pd.Series(v).shift(1)
    lo = s.rolling(WINDOW, min_periods=2).quantile(Q_LOW).to_numpy()
    hi = s.rolling(WINDOW, min_periods=2).quantile(Q_HIGH).to_numpy()
    return lo, hi

def evq_fast(v, lo, hi):
    """production（episode・K窓）同等。_all は戦略未使用のため省略。"""
    n = v.size
    med_lo = np.full(n, np.nan); ext_lo = np.full(n, np.nan)
    med_hi = np.full(n, np.nan); ext_hi = np.full(n, np.nan)
    up, dn, run_up, run_dn = [], [], [], []
    for t in range(n):
        if len(up) >= MIN_EV:
            a = np.asarray(up[-K:]); med_hi[t] = np.median(a); ext_hi[t] = np.quantile(a, Q_OUT)
        if len(dn) >= MIN_EV:
            a = np.asarray(dn[-K:]); med_lo[t] = np.median(a); ext_lo[t] = np.quantile(a, 1 - Q_OUT)
        finite = np.isfinite(v[t])
        is_up = finite and np.isfinite(hi[t]) and v[t] > hi[t]
        is_dn = (not is_up) and finite and np.isfinite(lo[t]) and v[t] < lo[t]
        if not is_up and run_up:
            up.append(max(run_up)); run_up = []
        if not is_dn and run_dn:
            dn.append(min(run_dn)); run_dn = []
        if is_up:
            run_up.append(float(v[t]))
        elif is_dn:
            run_dn.append(float(v[t]))
    return med_lo, ext_lo

def simulate(df, label, spread=0.0, since=None):
    v = mm.ma_marod_series(df)
    lo, hi = bands_fast(v)
    med_lo, ext_lo = evq_fast(v, lo, hi)
    o = df["open"].to_numpy(); cl = df["close"].to_numpy()
    tsec = df["tsec"].to_numpy()
    n = len(df)
    def xdn(line, t):
        return (np.isfinite(v[t]) and np.isfinite(line[t]) and np.isfinite(v[t-1])
                and np.isfinite(line[t-1]) and v[t] <= line[t] and v[t-1] > line[t-1])
    def xup(line, t):
        return (np.isfinite(v[t]) and np.isfinite(line[t]) and np.isfinite(v[t-1])
                and np.isfinite(line[t-1]) and v[t] > line[t] and v[t-1] <= line[t-1])
    trades = []; st = None
    for t in range(1, n - 1):
        if since is not None and tsec[t] < since:
            continue
        if st is None:
            if xdn(lo, t):
                st = {"e": [(t + 1, o[t + 1])], "done": {"q5"}, "t0": t + 1}
        else:
            if xdn(med_lo, t) and "med" not in st["done"]:
                st["e"].append((t + 1, o[t + 1])); st["done"].add("med")
            if xdn(ext_lo, t) and "ext" not in st["done"]:
                st["e"].append((t + 1, o[t + 1])); st["done"].add("ext")
            if xup(lo, t) or (t - st["t0"]) >= 20:
                st["px"] = o[t + 1]; st["tx"] = t + 1; trades.append(st); st = None
    if st is not None:
        st["px"] = cl[-1]; st["tx"] = n - 1; trades.append(st)
    if not trades:
        print(f"{label:<8} トレードなし"); return
    W = 0.66
    r = np.array([sum(W * ((tr["px"] - spread) / (px + spread) - 1) for _t, px in tr["e"])
                  for tr in trades])
    eq = np.cumprod(1 + r)
    curve = np.concatenate([[1.0], eq]); peak = np.maximum.accumulate(curve)
    mdd = ((curve - peak) / peak).min() * 100
    t_from = since if since is not None else tsec[0]
    yrs = (tsec[-1] - t_from) / (365.25 * 86400)
    cagr = (eq[-1] ** (1 / yrs) - 1) * 100
    holds = np.mean([tr["tx"] - tr["t0"] for tr in trades])
    wr = (r > 0).mean() * 100
    print(f"{label:<8} n={len(trades):>4}（{len(trades)/yrs:>5.1f}/年） 平均保有={holds:>4.1f}本 "
          f"勝率={wr:>5.1f}% 平均/trade={r.mean()*100:+.3f}% CAGR={cagr:>6.2f}% 最大DD={mdd:>6.1f}%")
    return trades

# ---- 検証: サーバ 1D と一致するか（バンド・水準線・トレード数） ----
def fetch(url, body=None):
    req = urllib.request.Request(url, data=json.dumps(body).encode() if body else None,
                                 headers={"Content-Type": "application/json"} if body else {})
    with urllib.request.urlopen(req) as r:
        return json.load(r)

cd = fetch("http://127.0.0.1:8281/candles?datasetRef=jp225_tick&timeframe=1D")["candles"]
df_srv = pd.DataFrame(cd)
df_srv["tsec"] = df_srv["time"]
comp = fetch("http://127.0.0.1:8281/compute", {"indicatorId": "ma_marod", "variant": "default",
                                               "params": {}, "datasetRef": "jp225_tick", "timeframe": "1D"})
sm = {s["name"]: {d["time"]: d["value"] for d in s["data"]} for s in comp["series"] if s["kind"] == "line"}
v = mm.ma_marod_series(df_srv)
lo, hi = bands_fast(v)
med_lo, ext_lo = evq_fast(v, lo, hi)
times = df_srv["time"].tolist()
for name, local in (("ma_marod_q5", lo), ("ma_marod_q95", hi),
                    ("ma_marod_evq_med_lo", med_lo), ("ma_marod_evq_ext_lo", ext_lo)):
    srv = np.array([sm[name].get(t, np.nan) for t in times])
    m = np.isfinite(srv)
    ok_nan = (np.isfinite(local) == m).all()
    ok_val = np.allclose(local[m], srv[m], rtol=1e-9, atol=1e-9)
    print(f"検証 {name}: NaN位置一致={ok_nan} 値一致={ok_val}")
print("検証 1D トレード数（サーバ検証時は 61）:")
simulate(df_srv, "1D(srv)")

# ---- 全時間足（CSV・スプレッド 0 と 10円） ----
def load(tf):
    df = pd.read_csv(str(_ROOT / f"data/marketdata/rollups/jp225_m1_{tf}.csv"))
    df["tsec"] = pd.to_datetime(df["date"]).astype("datetime64[s]").astype("int64")
    return df

SINCE_2024 = int(pd.Timestamp("2024-01-01").timestamp())
for tf in ("1D", "4h", "1h", "30m", "15m", "5m"):
    df = load(tf)
    print(f"\n--- {tf}（{len(df)}本・{df['date'].iloc[0][:10]}〜{df['date'].iloc[-1][:10]}） ---")
    simulate(df, f"{tf} 生")
    simulate(df, f"{tf} 費用込", spread=10.0)
    simulate(df, f"{tf} 24年-", spread=10.0, since=SINCE_2024)
