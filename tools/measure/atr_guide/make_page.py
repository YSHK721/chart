"""値幅（ATR）の解説ページ。図はすべて実データから描く。

実行: lightweight-charts-python-main/.venv/bin/python -m tools.measure.atr_guide.make_page <出力 html>
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

from indigators.btlm_trail.src.core import window_end_scalar
from tools.measure.page_toc import inject_toc
from tools.measure.reach_prob import reach as R
from tools.measure.tr_beta.beta import gain_interval

HERE = Path(__file__).resolve().parent
BETA_TESTS = HERE.parent / "tr_beta/results/tests.csv"
BETA_ATTRIB = HERE.parent / "tr_beta/results/attrib.csv"
ROOT = HERE.parents[2]
MT5 = "data/marketdata/rollups/jp225_mt5/jp225_mt5_{tf}.csv"
DUKA = "data/marketdata/rollups/jp225_tick_bid/jp225_tick_bid_{tf}.csv"


def load(path: str, tf: str) -> pd.DataFrame:
    d = pd.read_csv(ROOT / path.format(tf=tf), usecols=["date", "open", "high", "low", "close"],
                    parse_dates=["date"]).iloc[:-1]
    if tf == "1D":
        d = d[d.date.dt.dayofweek < 5]
    return d.reset_index(drop=True)


def true_range(d: pd.DataFrame) -> np.ndarray:
    prev = d.close.shift(1).to_numpy()
    h, l = d.high.to_numpy(), d.low.to_numpy()
    return np.nanmax(np.vstack([h - l, np.abs(h - prev), np.abs(l - prev)]), axis=0)


def wilder(tr: np.ndarray, n: int) -> np.ndarray:
    """ワイルダーの平滑: 最初は n 本の単純平均、以後 (前の値 × (n−1) + 今の値) ÷ n。"""
    out = np.full(len(tr), np.nan)
    out[n - 1] = tr[:n].mean()
    for i in range(n, len(tr)):
        out[i] = (out[i - 1] * (n - 1) + tr[i]) / n
    return out


# ---------- 図 1: 真の値幅の 3 つの候補（実際の窓のある足） ----------
def fig_tr():
    d = load(MT5, "1h")
    tr = true_range(d)
    gap = np.abs(d.open - d.close.shift(1)).to_numpy()
    hl = (d.high - d.low).to_numpy()
    # 窓が値幅の主役になっている足（真の値幅が高値−安値の 2 倍以上）で、最近のもの
    cand = np.flatnonzero((tr > 2 * hl) & (gap > 0) & (np.arange(len(d)) > 5))
    i = int(cand[-1])
    sl = d.iloc[i - 3:i + 3].reset_index(drop=True)
    k = 3
    pc = sl.close[k - 1]
    o, h, l, c = sl.open[k], sl.high[k], sl.low[k], sl.close[k]
    vals = np.concatenate([sl.high, sl.low])
    lo, hi = vals.min(), vals.max()
    pad = (hi - lo) * 0.12
    lo, hi = lo - pad, hi + pad
    X0, X1, Y0, Y1 = 80, 360, 20, 260
    n = len(sl)
    sx = lambda j: X0 + (j + 0.5) / n * (X1 - X0)
    sy = lambda v: Y1 - (v - lo) / (hi - lo) * (Y1 - Y0)
    w = (X1 - X0) / n * 0.5
    parts = []
    for j in range(n):
        cls = "up" if sl.close[j] >= sl.open[j] else "dn"
        op = "" if j in (k - 1, k) else ' opacity="0.35"'
        parts.append(f'<line x1="{sx(j):.1f}" x2="{sx(j):.1f}" y1="{sy(sl.high[j]):.1f}" y2="{sy(sl.low[j]):.1f}" class="wick"{op}/>')
        top, bot = sy(max(sl.open[j], sl.close[j])), sy(min(sl.open[j], sl.close[j]))
        parts.append(f'<rect x="{sx(j) - w / 2:.1f}" y="{top:.1f}" width="{w:.1f}" height="{max(bot - top, 1.5):.1f}" class="{cls}"{op}/>')
    parts.append(f'<line x1="{sx(k - 1):.1f}" x2="{X1 + 20}" y1="{sy(pc):.1f}" y2="{sy(pc):.1f}" class="guide"/>')
    parts.append(f'<text x="{sx(k - 1) - 10:.1f}" y="{sy(pc) + 4:.1f}" text-anchor="end" class="lb">前の終値</text>')
    # 3 つの候補を右側に縦の棒で
    xs = [X1 + 50, X1 + 130, X1 + 210]
    cands = [("候補 1", h, l, h - l), ("候補 2", h, pc, abs(h - pc)), ("候補 3", l, pc, abs(l - pc))]
    best = int(np.argmax([x[3] for x in cands]))
    for j, (name, a, b, v) in enumerate(cands):
        cls = "bar-best" if j == best else "bar"
        parts.append(f'<line x1="{xs[j]}" x2="{xs[j]}" y1="{sy(max(a, b)):.1f}" y2="{sy(min(a, b)):.1f}" class="{cls}"/>')
        parts.append(f'<text x="{xs[j]}" y="{Y1 + 18}" text-anchor="middle" class="lb{" strong" if j == best else ""}">{name}</text>')
        parts.append(f'<text x="{xs[j]}" y="{Y1 + 34}" text-anchor="middle" class="tk">{v:,.1f}</text>')
    parts.append(f'<text x="{xs[best]}" y="{sy(max(cands[best][1], cands[best][2])) - 8:.1f}" text-anchor="middle" class="lb acc">真の値幅</text>')
    cap = (f"MT5 の 1 時間足 {sl.date[k]:%Y-%m-%d %H:%M}（UTC）。前の足の終値 {pc:,.1f} から始値 {o:,.1f} へ窓が開きました。"
           f"高値 − 安値は {h - l:,.1f} ですが、窓を含めると {tr[i]:,.1f} 動いています。")
    return (f'<svg viewBox="0 0 640 310" role="img" aria-label="真の値幅の 3 つの候補と、一番大きいものを選ぶ様子">'
            + "".join(parts) + "</svg>"), cap


# ---------- 図 2: 平均の取り方（単純平均 20・単純平均 14・ワイルダー 14） ----------
def fig_smooth():
    d = load(MT5, "1D")
    tr = true_range(d)
    s20 = pd.Series(tr).rolling(20).mean().to_numpy()
    s14 = pd.Series(tr).rolling(14).mean().to_numpy()
    w14 = wilder(np.nan_to_num(tr, nan=tr[1]), 14)
    m = 160
    dd, t, a, b, c = d.date.iloc[-m:].reset_index(drop=True), tr[-m:], s20[-m:], s14[-m:], w14[-m:]
    X0, X1, Y0, Y1 = 70, 620, 20, 240
    hi = np.nanmax(t) * 1.05
    sx = lambda j: X0 + j / (m - 1) * (X1 - X0)
    sy = lambda v: Y1 - v / hi * (Y1 - Y0)
    parts = []
    for v in np.linspace(0, hi, 5)[:-1]:
        parts.append(f'<line x1="{X0}" x2="{X1}" y1="{sy(v):.1f}" y2="{sy(v):.1f}" class="grid"/>')
        parts.append(f'<text x="{X0 - 6}" y="{sy(v) + 4:.1f}" text-anchor="end" class="tk">{v:,.0f}</text>')
    bw = (X1 - X0) / m * 0.7
    for j in range(m):
        parts.append(f'<rect x="{sx(j) - bw / 2:.1f}" y="{sy(t[j]):.1f}" width="{bw:.1f}" height="{Y1 - sy(t[j]):.1f}" class="trbar"/>')
    for arr, cls in ((b, "l-s14"), (c, "l-w14"), (a, "l-s20")):
        parts.append('<path d="M' + " L".join(f"{sx(j):.1f},{sy(v):.1f}" for j, v in enumerate(arr)) + f'" class="{cls}"/>')
    for j in (0, m // 2, m - 1):
        parts.append(f'<text x="{sx(j):.1f}" y="{Y1 + 16}" text-anchor="middle" class="tk">{dd[j]:%Y-%m-%d}</text>')
    parts.append(f'<text x="{X0}" y="12" class="tk">ポイント（円）</text>')
    return f'<svg viewBox="0 0 640 270" role="img" aria-label="日足の真の値幅と 3 通りの平均">' + "".join(parts) + "</svg>"


# ---------- 図 3: 長い歴史（日足・ポイントと価格比） ----------
def fig_history():
    d = load(DUKA, "1D")
    d = d[d.date >= "2013-01-01"].reset_index(drop=True)
    a = R.atr(d.high.to_numpy(), d.low.to_numpy(), d.close.to_numpy())
    pct = a / d.close.to_numpy() * 100
    m = d.assign(a=a, p=pct).set_index("date").resample("ME").last().dropna()
    out = []
    for col, unit, cls, label in (("a", "ポイント", "l-pt", "ATR20（ポイント）"), ("p", "%", "l-pct", "ATR20 ÷ 終値（%）")):
        X0, X1, Y0, Y1 = 70, 620, 24, 180
        v = m[col].to_numpy()
        hi = v.max() * 1.08
        n = len(v)
        sx = lambda j: X0 + j / (n - 1) * (X1 - X0)
        sy = lambda x: Y1 - x / hi * (Y1 - Y0)
        parts = [f'<text x="{X0}" y="14" class="lb strong">{label}</text>']
        for g in np.linspace(0, hi, 4)[:-1]:
            parts.append(f'<line x1="{X0}" x2="{X1}" y1="{sy(g):.1f}" y2="{sy(g):.1f}" class="grid"/>')
            fmt = f"{g:,.0f}" if unit == "ポイント" else f"{g:.1f}"
            parts.append(f'<text x="{X0 - 6}" y="{sy(g) + 4:.1f}" text-anchor="end" class="tk">{fmt}</text>')
        parts.append('<path d="M' + " L".join(f"{sx(j):.1f},{sy(x):.1f}" for j, x in enumerate(v)) + f'" class="{cls}"/>')
        years = m.index.year
        for j in range(n):
            if j == 0 or years[j] != years[j - 1]:
                if years[j] % 2 == 1:
                    parts.append(f'<text x="{sx(j):.1f}" y="{Y1 + 16}" text-anchor="middle" class="tk">{years[j]}</text>')
        out.append(f'<svg viewBox="0 0 640 200" role="img" aria-label="{label}の推移">' + "".join(parts) + "</svg>")
    return "".join(out)


# ---------- 図 4: 時間足ごとの大きさ ----------
def fig_tf():
    rows = []
    for tf, mins in (("5m", 5), ("15m", 15), ("30m", 30), ("1h", 60), ("4h", 240), ("1D", 1440)):
        d = load(MT5, tf)
        a = R.atr(d.high.to_numpy(), d.low.to_numpy(), d.close.to_numpy())
        rows.append((tf, mins, float(np.nanmedian(a / d.close.to_numpy() * 100))))
    X0, X1 = 110, 600
    hi = max(r[2] for r in rows) * 1.15
    sx = lambda v: X0 + v / hi * (X1 - X0)
    rh = 34
    H = 20 + rh * len(rows) + 20
    names = {"5m": "5 分足", "15m": "15 分足", "30m": "30 分足", "1h": "1 時間足", "4h": "4 時間足", "1D": "日足"}
    parts = []
    base = rows[3][2]
    for i, (tf, mins, v) in enumerate(rows):
        y = 20 + i * rh
        parts.append(f'<text x="{X0 - 10}" y="{y + 18}" text-anchor="end" class="lb">{names[tf]}</text>')
        parts.append(f'<rect x="{X0}" y="{y + 5}" width="{sx(v) - X0:.1f}" height="18" class="tfbar"/>')
        sq = base * np.sqrt(mins / 60)
        parts.append(f'<line x1="{sx(sq):.1f}" x2="{sx(sq):.1f}" y1="{y + 1}" y2="{y + 27}" class="sqrt"/>')
        parts.append(f'<text x="{sx(v) + 6:.1f}" y="{y + 18}" class="lb">{v:.3f}%</text>')
    return (f'<svg viewBox="0 0 680 {H}" role="img" aria-label="時間足ごとの ATR20 の大きさ（価格比の中央値）">'
            + "".join(parts) + "</svg>"), rows


# ---------- 図 5: 時間帯ごとの値幅 ----------
def fig_hour():
    d = load(MT5, "1h")
    tr = true_range(d) / d.close.to_numpy() * 100
    jst = (d.date + pd.Timedelta(hours=9)).dt.hour
    g = pd.Series(tr).groupby(jst.to_numpy()).median()
    X0, X1, Y0, Y1 = 60, 620, 24, 210
    hi = g.max() * 1.15
    bw = (X1 - X0) / 24
    sy = lambda v: Y1 - v / hi * (Y1 - Y0)
    parts = []
    for v in np.linspace(0, hi, 4)[:-1]:
        parts.append(f'<line x1="{X0}" x2="{X1}" y1="{sy(v):.1f}" y2="{sy(v):.1f}" class="grid"/>')
        parts.append(f'<text x="{X0 - 6}" y="{sy(v) + 4:.1f}" text-anchor="end" class="tk">{v:.2f}</text>')
    for hr in range(24):
        if hr not in g.index:
            continue
        v = g[hr]
        cls = "hbar hot" if v >= g.quantile(0.8) else "hbar"
        parts.append(f'<rect x="{X0 + hr * bw + 2:.1f}" y="{sy(v):.1f}" width="{bw - 4:.1f}" height="{Y1 - sy(v):.1f}" class="{cls}"/>')
        if hr % 3 == 0:
            parts.append(f'<text x="{X0 + hr * bw + bw / 2:.1f}" y="{Y1 + 16}" text-anchor="middle" class="tk">{hr}時</text>')
    parts.append(f'<text x="{X0}" y="14" class="tk">真の値幅 ÷ 終値（%・中央値）　横軸＝日本時間</text>')
    return f'<svg viewBox="0 0 640 236" role="img" aria-label="日本時間の時間帯ごとの 1 時間足の値幅">' + "".join(parts) + "</svg>", g


# ---------- 図 6: 本数を増やすと値幅はどう広がるか ----------
def fig_scale():
    d = load(MT5, "1h")
    h, l, c = d.high.to_numpy(), d.low.to_numpy(), d.close.to_numpy()
    a = R.atr(h, l, c)
    ts = [1, 2, 3, 5, 8, 12, 16, 20]
    ratio = []
    for t in ts:
        mx = R.forward_extreme(h, t, "max")
        mn = R.forward_extreme(l, t, "min")
        ok = np.isfinite(mx) & np.isfinite(a)
        ratio.append(float(np.nanmedian((mx[ok] - mn[ok]) / a[ok])))
    X0, X1, Y0, Y1 = 60, 600, 20, 220
    hi = max(max(ratio), ratio[0] * np.sqrt(20), 20 ** 0.5) * 1.1
    sx = lambda t: X0 + (t - 1) / 19 * (X1 - X0)
    sy = lambda v: Y1 - v / hi * (Y1 - Y0)
    parts = []
    for v in range(0, int(hi) + 1):
        parts.append(f'<line x1="{X0}" x2="{X1}" y1="{sy(v):.1f}" y2="{sy(v):.1f}" class="grid"/>')
        parts.append(f'<text x="{X0 - 6}" y="{sy(v) + 4:.1f}" text-anchor="end" class="tk">{v}</text>')
    for t in ts:
        parts.append(f'<text x="{sx(t):.1f}" y="{Y1 + 16}" text-anchor="middle" class="tk">{t}</text>')
    parts.append(f'<text x="{(X0 + X1) / 2}" y="{Y1 + 32}" text-anchor="middle" class="tk">先の何本か（1 時間足）</text>')
    lin = [ratio[0] * t for t in np.linspace(1, 20, 60)]
    sq = [ratio[0] * np.sqrt(t) for t in np.linspace(1, 20, 60)]
    parts.append('<path d="M' + " L".join(f"{sx(t):.1f},{sy(v):.1f}" for t, v in zip(np.linspace(1, 20, 60), sq) if v <= hi) + '" class="l-sq"/>')
    parts.append('<path d="M' + " L".join(f"{sx(t):.1f},{sy(v):.1f}" for t, v in zip(np.linspace(1, 20, 60), lin) if v <= hi) + '" class="l-lin"/>')
    parts.append('<path d="M' + " L".join(f"{sx(t):.1f},{sy(v):.1f}" for t, v in zip(ts, ratio)) + '" class="l-real"/>')
    for t, v in zip(ts, ratio):
        parts.append(f'<circle cx="{sx(t):.1f}" cy="{sy(v):.1f}" r="4" class="m-real"/>')
    parts.append(f'<text x="{X0 + 4}" y="14" class="tk">先の T 本の値幅（最高値 − 最安値）÷ ATR20（中央値）</text>')
    return f'<svg viewBox="0 0 640 260" role="img" aria-label="先の本数と値幅の広がり。実測は平方根の曲線に近い">' + "".join(parts) + "</svg>", dict(zip(ts, ratio))


def gap_stats():
    """窓の効き目: 真の値幅 ÷（高値 − 安値）の平均比と、窓で値幅が増えた足の割合（MT5）。"""
    out = {}
    for tf in ("15m", "1h", "1D"):
        d = load(MT5, tf)
        tr = true_range(d)
        hl = (d.high - d.low).to_numpy()
        ok = np.isfinite(tr)
        out[tf] = (tr[ok].mean() / hl[ok].mean(), (tr[ok] > hl[ok] + 1e-9).mean() * 100)
    d = load(MT5, "1D")
    tr = true_range(d)
    hl = (d.high - d.low).to_numpy()
    mon = (d.date.dt.dayofweek == 0).to_numpy()
    out["mon"] = np.nanmean(tr[mon]) / np.nanmean(hl[mon])
    out["other"] = np.nanmean(tr[~mon]) / np.nanmean(hl[~mon])
    return out


def hold_stats():
    """日足で T 日持つときの値幅と、買いの逆行幅（ATR20 の倍率・MT5）。ATR3 との比較も返す。"""
    d = load(MT5, "1D")
    h, l, c = (d[k].to_numpy(float) for k in ("high", "low", "close"))
    a20 = R.atr(h, l, c)
    a3 = pd.Series(true_range(d)).rolling(3).mean().to_numpy()
    rows = []
    for t in (1, 3, 5, 10):
        mx, mn = R.forward_extreme(h, t, "max"), R.forward_extreme(l, t, "min")
        ok = np.isfinite(mx) & np.isfinite(a20)
        rng = (mx[ok] - mn[ok]) / a20[ok]
        adv = (c[ok] - mn[ok]) / a20[ok]
        rows.append((t, np.median(rng), np.median(adv), np.quantile(adv, 0.8), np.quantile(adv, 0.95)))
    mx, mn = R.forward_extreme(h, 3, "max"), R.forward_extreme(l, 3, "min")
    f = mx - mn
    ok = np.isfinite(f) & np.isfinite(a20) & np.isfinite(a3)
    err = {}
    for name, a in (("a3", a3), ("a20", a20)):
        k = np.median(f[ok] / a[ok])
        err[name] = (np.corrcoef(a[ok], f[ok])[0, 1], np.mean(np.abs(f[ok] - k * a[ok])))
    q = np.quantile(a3[ok] / a20[ok], [0.1, 0.9])
    return rows, err, q, f"{d.date.iloc[0]:%Y-%m}"


def break_stats():
    """1 時間足で、窓の分（真の値幅 − 高値安値）が取引の休止（60 分超の空白）の直後に集まる度合い（MT5）。"""
    d = load(MT5, "1h")
    tr = true_range(d)
    hl = (d.high - d.low).to_numpy()
    ex = tr - hl
    after = (d.date.diff().dt.total_seconds() / 60).to_numpy() > 60
    ok = np.isfinite(ex)
    rows = []
    for name, mask in (("取引が止まった後の足", ok & after), ("取引が続いている足", ok & ~after)):
        rows.append(dict(name=name, bars=int(mask.sum()), share_bars=mask.sum() / ok.sum() * 100,
                         total=ex[mask].sum(), share_gap=ex[mask].sum() / ex[ok].sum() * 100,
                         mean=ex[mask].mean(), pct=ex[mask].mean() / hl[mask].mean() * 100))
    return rows


def atr20_diff():
    """1 時間足で、ATR20 が高値安値の 20 本平均より何 % 大きいか（中央値・上位 1%。MT5）。"""
    d = load(MT5, "1h")
    tr = pd.Series(true_range(d))
    hl = (d.high - d.low).reset_index(drop=True)
    r = ((tr.rolling(20).mean() / hl.rolling(20).mean() - 1) * 100).dropna()
    return r.median(), r.quantile(0.99)


# ---------- 図: 単純平均とワイルダーの平滑の違い ----------
def sma(tr: np.ndarray, n: int) -> np.ndarray:
    """単純平均 n 本。"""
    return pd.Series(tr).rolling(n).mean().to_numpy()


def fig_impulse(n: int = 14, base: float = 100.0, spike: float = 1500.0):
    """計算例: 値幅 base が続く中に spike の足が 1 本入ったときの、2 つの平均の動き。"""
    lead, after = 60, 45
    tr = np.r_[np.full(lead, base), spike, np.full(after, base)]
    a, w = sma(tr, n)[lead - 4:], wilder(tr, n)[lead - 4:]
    X0, X1, Y0, Y1 = 50, 620, 24, 210
    top = max(np.nanmax(a), np.nanmax(w)) * 1.15
    sx = lambda j: X0 + j / (len(a) - 1) * (X1 - X0)
    sy = lambda v: Y1 - v / top * (Y1 - Y0)
    parts = []
    for v in (0, 100, 200):
        parts.append(f'<line x1="{X0}" x2="{X1}" y1="{sy(v):.1f}" y2="{sy(v):.1f}" class="grid"/>')
        parts.append(f'<text x="{X0 - 6}" y="{sy(v) + 4:.1f}" text-anchor="end" class="tk">{v}</text>')
    step = [f"{sx(0):.1f},{sy(a[0]):.1f}"]
    for j in range(1, len(a)):
        step += [f"{sx(j):.1f},{sy(a[j - 1]):.1f}", f"{sx(j):.1f},{sy(a[j]):.1f}"]
    parts.append('<path d="M' + " L".join(step) + '" class="l-s20"/>')
    parts.append('<path d="M' + " L".join(f"{sx(j):.1f},{sy(v):.1f}" for j, v in enumerate(w)) + '" class="l-w14"/>')
    parts.append(f'<line x1="{sx(4):.1f}" x2="{sx(4):.1f}" y1="{Y0}" y2="{Y1}" class="guide"/>')
    parts.append(f'<line x1="{sx(4 + n):.1f}" x2="{sx(4 + n):.1f}" y1="{Y0}" y2="{Y1}" class="guide"/>')
    parts.append(f'<text x="{sx(4) + 5:.1f}" y="{Y0 + 2}" class="lb">{spike:,.0f} の足が入る</text>')
    parts.append(f'<text x="{sx(4 + n) + 5:.1f}" y="{Y0 + 2}" class="lb">{n} 本後に抜ける</text>')
    parts.append(f'<text x="{sx(4 + n) + 5:.1f}" y="{sy(a[4 + n]) + 18:.1f}" class="lb acc">単純平均：一度に下がる</text>')
    parts.append(f'<text x="{sx(4 + n + 4):.1f}" y="{sy(w[4 + n + 4]) - 10:.1f}" class="lb blue">ワイルダーの平滑：少しずつ下がる</text>')
    for k in (0, 10, 20, 30, 40):
        parts.append(f'<text x="{sx(4 + k):.1f}" y="{Y1 + 16}" text-anchor="middle" class="tk">{k} 本後</text>')
    return f'<svg viewBox="0 0 640 236" role="img" aria-label="大きな足が 1 本入ったときの動き。単純平均は {n} 本後に一度に下がり、ワイルダーの平滑は少しずつ下がる">' + "".join(parts) + "</svg>"


def fig_exit(n: int = 20):
    """実例: 単純平均 n 本が 1 本で最も大きく下がった日の前後（MT5 日足）。"""
    d = load(MT5, "1D")
    tr = true_range(d)
    a, w = sma(tr, n), wilder(tr, n)
    i = int(np.nanargmin(np.diff(a))) + 1
    lo, hi_i = i - n - 12, min(i + 15, len(tr) - 1)
    idx = np.arange(lo, hi_i + 1)
    X0, X1, Y0, Y1 = 60, 620, 26, 230
    top = tr[idx].max() * 1.05
    sx = lambda j: X0 + (j - lo) / (hi_i - lo) * (X1 - X0)
    sy = lambda v: Y1 - v / top * (Y1 - Y0)
    parts = []
    for v in np.linspace(0, top, 5)[:-1]:
        parts.append(f'<line x1="{X0}" x2="{X1}" y1="{sy(v):.1f}" y2="{sy(v):.1f}" class="grid"/>')
        parts.append(f'<text x="{X0 - 6}" y="{sy(v) + 4:.1f}" text-anchor="end" class="tk">{v:,.0f}</text>')
    bw = (X1 - X0) / len(idx) * 0.7
    for j in idx:
        parts.append(f'<rect x="{sx(j) - bw / 2:.1f}" y="{sy(tr[j]):.1f}" width="{bw:.1f}" height="{Y1 - sy(tr[j]):.1f}" class="trbar"/>')
    for arr, cls in ((w, "l-w14"), (a, "l-s20")):
        parts.append('<path d="M' + " L".join(f"{sx(j):.1f},{sy(arr[j]):.1f}" for j in idx) + f'" class="{cls}"/>')
    out, gone = i - n, i
    for j in (out, gone):
        parts.append(f'<line x1="{sx(j):.1f}" x2="{sx(j):.1f}" y1="{Y0}" y2="{Y1}" class="guide"/>')
    parts.append(f'<text x="{sx(out) + 8:.1f}" y="{Y0 + 2}" class="lb">{d.date[out]:%Y-%m-%d}：値幅 {tr[out]:,.0f}</text>')
    parts.append(f'<text x="{sx(gone) + 5:.1f}" y="{Y0 + 2}" class="lb">{d.date[gone]:%Y-%m-%d}：{n} 本前が抜ける</text>')
    for j in (lo, out, gone, hi_i):
        parts.append(f'<text x="{sx(j):.1f}" y="{Y1 + 16}" text-anchor="middle" class="tk">{d.date[j]:%m-%d}</text>')
    parts.append(f'<text x="{X0}" y="12" class="tk">ポイント（円）</text>')
    svg = f'<svg viewBox="0 0 640 256" role="img" aria-label="大きな足が抜けた日に単純平均 {n} だけが急に下がる実例">' + "".join(parts) + "</svg>"
    return svg, dict(n=n, date=f"{d.date[gone]:%Y-%m-%d}", tr=tr[gone], out_date=f"{d.date[out]:%Y-%m-%d}", out_tr=tr[out],
                     a0=a[i - 1], a1=a[i], a_pct=(a[i] / a[i - 1] - 1) * 100,
                     w0=w[i - 1], w1=w[i], w_pct=(w[i] / w[i - 1] - 1) * 100)


SMOOTHERS = (("単純平均", sma), ("ワイルダーの平滑", wilder))


def smooth_rows(tr: np.ndarray, ns=(14, 20), smoothers=SMOOTHERS, start: int = 100):
    """方法と母数ごとに、次の 1 本の外れ（period_stats と同じ定義）と、1 本ごとの変化率の平均（動き）を返す。

    smoothers: 平均の計算（観測の境界・計算量検定の注入点）。
    """
    f = np.concatenate([tr[1:], [np.nan]])
    rows = []
    for n in ns:
        for name, avg in smoothers:
            a = avg(tr, n)
            ok = np.isfinite(a) & np.isfinite(f) & (np.arange(len(a)) >= start)
            k = np.median(f[ok] / a[ok])
            err = np.mean(np.abs(f[ok] - k * a[ok])) / np.mean(f[ok]) * 100
            move = np.mean(np.abs(a[start + 1:] / a[start:-1] - 1)) * 100
            rows.append((name, n, err, move))
    return rows


FOLLOW_METHODS = (("単純平均 14", lambda t: sma(t, 14)), ("ワイルダーの平滑 14", lambda t: wilder(t, 14)),
                  ("単純平均 27", lambda t: sma(t, 27)))


def bars_to_catch_up(avg, share: float, lead: int = 200, after: int = 200) -> int:
    """値幅の水準が 100 から 200 へ変わったあと、変化の share まで追いつくのに要る本数。"""
    a = avg(np.r_[np.full(lead, 100.0), np.full(after, 200.0)])[lead:]
    return int(np.argmax(a >= 100.0 + 100.0 * share)) + 1


def follow_rows(trs, methods=FOLLOW_METHODS, start: int = 100):
    """方法ごとに、半分・9 割まで追いつく本数と、系列ごとの直近 1 本の真の値幅との相関を返す。

    methods: 平均の計算（観測の境界・計算量検定の注入点）。
    """
    rows = []
    for name, avg in methods:
        corr = [np.corrcoef(a[start:], tr[start:])[0, 1] for tr in trs for a in [avg(tr)]]
        rows.append((name, bars_to_catch_up(avg, 0.5), bars_to_catch_up(avg, 0.9), corr))
    return rows


# ---------- 真の値幅に btlm_trail の計算を当てはめる ----------
TREND_START = 200
LAG_RANGE = (-20, 80)
CENTERS = (41, 21)
GRID_NS = (3, 4, 5, 6, 8, 10, 14, 20, 25, 30, 50, 100, 200)


def ols_end(tr: np.ndarray, n: int, first: int = 0, window=window_end_scalar) -> np.ndarray:
    """btlm_trail の計算: first 以降の足で、直近 n 本へ直線を当てはめた右端の値。window は計算量検定の注入点。"""
    out = np.full(len(tr), np.nan)
    for t in range(max(first, n - 1), len(tr)):
        out[t] = window(tr[t - n + 1:t + 1])[0]
    return out


TREND_METHODS = (("ワイルダーの平滑 14", lambda t: wilder(t, 14)), ("単純平均 25", lambda t: sma(t, 25)),
                 ("btlm_trail の計算 25", lambda t: ols_end(t, 25, TREND_START)),
                 ("btlm_trail の計算 100", lambda t: ols_end(t, 100, TREND_START)))
OLS_NAME = "btlm_trail の計算"
GRID_METHODS = tuple((f"{label} {n}", (lambda t, f=f, n=n: f(t, n))) for n in GRID_NS for label, f in
                     (("単純平均", sma), ("ワイルダーの平滑", wilder), (OLS_NAME, lambda t, n: ols_end(t, n, TREND_START))))


def next_errors(tr: np.ndarray, a: np.ndarray, start: int = TREND_START) -> tuple[float, float]:
    """次の 1 本と次の 20 本の平均の外れ（period_stats と同じ定義）。"""
    s = pd.Series(tr)
    out = []
    for f in (s.shift(-1).to_numpy(), s.rolling(20).mean().shift(-20).to_numpy()):
        ok = (np.arange(len(a)) >= start) & np.isfinite(a) & np.isfinite(f) & (a > 0)
        k = np.median(f[ok] / a[ok])
        out.append(np.mean(np.abs(f[ok] - k * a[ok])) / np.mean(f[ok]) * 100)
    return out[0], out[1]


def lag_to_center(tr: np.ndarray, a: np.ndarray, start: int = TREND_START, center: int = 41) -> int:
    """前後 center 本の平均と線の相関が最も高くなるずれ（本）。負は線が先に動くことを表す。"""
    line = pd.Series(a).iloc[start:]
    smooth = pd.Series(tr).rolling(center, center=True).mean()
    lags = range(LAG_RANGE[0], LAG_RANGE[1] + 1)
    corr = [line.corr(smooth.shift(k).iloc[start:]) for k in lags]
    return lags[int(np.nanargmax(corr))]


def trend_rows(tr: np.ndarray, methods=TREND_METHODS, start: int = TREND_START):
    """方法ごとの（名前・遅れの組・動き・次の 1 本の外れ・次の 20 本の外れ）。methods は計算量検定の注入点。"""
    rows = []
    for name, avg in methods:
        a = avg(tr)
        move = np.nanmean(np.abs(np.diff(a[start:]))) / np.nanmean(a[start:]) * 100
        rows.append((name, tuple(lag_to_center(tr, a, start, c) for c in CENTERS), move, *next_errors(tr, a, start)))
    return rows


def best_rows(series, methods=GRID_METHODS, ols_name: str = OLS_NAME):
    """系列ごとに、次の 1 本・次の 20 本の外れが最小の方法と、btlm_trail の計算の中の最小を返す。"""
    rows = []
    for label, tr in series:
        errs = {name: next_errors(tr, avg(tr)) for name, avg in methods}
        row = dict(label=label)
        for i, key in enumerate(("one", "twenty")):
            best = min(errs, key=lambda k: errs[k][i])
            ols = min((k for k in errs if k.startswith(ols_name)), key=lambda k: errs[k][i])
            row.update({f"best_{key}": best, f"err_{key}": errs[best][i], f"ols_{key}": ols, f"ols_err_{key}": errs[ols][i]})
        rows.append(row)
    return rows


def ols_wins(rows, ols_name: str = OLS_NAME) -> int:
    """btlm_trail の計算が最小になった回数（系列 × 見積もり先）。"""
    return sum(r[f"best_{k}"].startswith(ols_name) for r in rows for k in ("one", "twenty"))


SKILL_NS = (14, 25, 100)
SKILL_AVG = (("S", sma), ("W", wilder), ("O", lambda t, n: ols_end(t, n, TREND_START)))
SKILL_COUNT = 1999
SKILL_SEED = 20261031
HORIZONS = ("one", "twenty")


def series_frames():
    """比べる 4 系列（名前・真の値幅・再標本の単位の四半期）。"""
    frames = []
    for label, path, tf, start, end in (("MT5 1 時間足", MT5, "1h", None, None), ("MT5 日足", MT5, "1D", None, None),
                                        ("Dukascopy 1 時間足", DUKA, "1h", "2014-01-01", "2020-05-01"),
                                        ("Dukascopy 日足", DUKA, "1D", "2014-01-01", "2020-05-01")):
        d = load(path, tf)
        if start:
            d = d[(d.date >= start) & (d.date < end)].reset_index(drop=True)
        frames.append((label, true_range(d), pd.factorize(d.date.dt.to_period("Q"))[0]))
    return frames


def _gain(y: np.ndarray, base: np.ndarray, pred: np.ndarray) -> float:
    """base から pred への外れの改善（%ポイント）。"""
    return float((np.abs(y - base).sum() - np.abs(y - pred).sum()) / y.sum() * 100)


def skill_table(frames, rng, count: int = SKILL_COUNT, ns=SKILL_NS, avg=SKILL_AVG, start: int = TREND_START) -> dict:
    """線の見積もりの力と差を（改善・幅の下・幅の上）で返す。

    キーは（系列・見積もり先・"S14" など）が一定値との比較、（系列・見積もり先・"W25-O25" など）が線どうしの差。
    avg は線の計算（計算量検定の注入点）。線は系列ごとに 1 度だけ作る。
    """
    out = {}
    for label, tr, block in frames:
        lines = {f"{k}{n}": f(tr, n) for n in ns for k, f in avg}
        s = pd.Series(tr)
        for horizon, f in (("one", s.shift(-1).to_numpy()), ("twenty", s.rolling(20).mean().shift(-20).to_numpy())):
            ok = np.isfinite(f) & (np.arange(len(tr)) >= start)
            for a in lines.values():
                ok &= np.isfinite(a) & (a > 0)
            y, week = f[ok], pd.factorize(block[ok])[0]
            const = np.full(ok.sum(), np.median(y))
            pred = {k: np.median(y / a[ok]) * a[ok] for k, a in lines.items()}
            for k in lines:
                out[label, horizon, k] = (_gain(y, const, pred[k]), *gain_interval(y, const, pred[k], week, count, rng))
                n = k[1:]
                if k[0] == "O":
                    for other in ("S", "W"):
                        out[label, horizon, f"{other}{n}-O{n}"] = (_gain(y, pred[k], pred[other + n]),
                                                                   *gain_interval(y, pred[k], pred[other + n], week, count, rng))
                if k[0] == "W":
                    out[label, horizon, f"W{n}-S{n}"] = (_gain(y, pred["S" + n], pred[k]),
                                                         *gain_interval(y, pred["S" + n], pred[k], week, count, rng))
    return out


def excludes_zero(v) -> bool:
    """幅が 0 を含まないか。"""
    return v[1] > 0 or v[2] < 0


HORIZON_LABEL = {"one": "次の 1 本", "twenty": "次の 20 本の平均"}


def fig_skill(table: dict, labels, n: int = 25):
    """一定値で見積もるより外れがどれだけ小さいか（点と 95% の幅）。"""
    methods = (("S", "単純平均", "amber-m"), ("W", "ワイルダーの平滑", "blue-m"), ("O", "btlm_trail の計算", "violet-m"))
    vals = [table[l, h, f"{k}{n}"] for l in labels for h in HORIZONS for k, _, _ in methods]
    lo, hi = min(v[1] for v in vals) - 2, max(v[2] for v in vals) + 2
    X0, X1 = 150, 620
    sx = lambda v: X0 + (v - lo) / (hi - lo) * (X1 - X0)
    parts = []
    y = 20
    for h in HORIZONS:
        parts.append(f'<text x="14" y="{y + 4}" class="lb strong">{HORIZON_LABEL[h]}</text>')
        y += 18
        for l in labels:
            parts.append(f'<text x="{X0 - 12}" y="{y + 16}" text-anchor="end" class="tk">{l}</text>')
            for k, _, cls in methods:
                v = table[l, h, f"{k}{n}"]
                parts.append(f'<line x1="{sx(v[1]):.1f}" x2="{sx(v[2]):.1f}" y1="{y}" y2="{y}" class="whisker {cls}"/>')
                parts.append(f'<circle cx="{sx(v[0]):.1f}" cy="{y}" r="4" class="{cls}"/>')
                y += 13
            y += 7
        y += 10
    parts.insert(0, f'<line x1="{sx(0):.1f}" x2="{sx(0):.1f}" y1="8" y2="{y - 16}" class="guide"/>')
    for v in range(int(np.ceil(lo / 10)) * 10, int(hi) + 1, 10):
        parts.append(f'<text x="{sx(v):.1f}" y="{y - 2}" text-anchor="middle" class="tk">{v:+d}</text>')
    return f'<svg viewBox="0 0 640 {y + 6}" role="img" aria-label="3 つの線と一定値の比較。点が 0 の線より右なら一定値より外れが小さい">' + "".join(parts) + "</svg>"


def fig_diff(table: dict, labels):
    """ワイルダーの平滑 − btlm_trail の計算の差のます目（色は幅が 0 を含まない組）。"""
    cols = [(h, n) for h in HORIZONS for n in (25, 100)]
    X0, W, H = 150, 118, 34
    y0 = 46
    parts = []
    for i, h in enumerate(HORIZONS):
        parts.append(f'<text x="{X0 + W * (2 * i + 1)}" y="16" text-anchor="middle" class="lb strong">{HORIZON_LABEL[h]}</text>')
    for j, (h, n) in enumerate(cols):
        parts.append(f'<text x="{X0 + W * j + W / 2}" y="36" text-anchor="middle" class="tk">n = {n}</text>')
    for i, l in enumerate(labels):
        y = y0 + H * i
        parts.append(f'<text x="{X0 - 10}" y="{y + 21}" text-anchor="end" class="tk">{l}</text>')
        for j, (h, n) in enumerate(cols):
            v = table[l, h, f"W{n}-O{n}"]
            cls = ("cell-w" if v[0] > 0 else "cell-o") if excludes_zero(v) else "cell-n"
            parts.append(f'<rect x="{X0 + W * j + 2}" y="{y + 2}" width="{W - 4}" height="{H - 4}" rx="3" class="{cls}"/>')
            parts.append(f'<text x="{X0 + W * j + W / 2}" y="{y + 21}" text-anchor="middle" class="lb{" strong" if excludes_zero(v) else ""}">{v[0]:+.2f}</text>')
    return f'<svg viewBox="0 0 640 {y0 + H * len(labels) + 8}" role="img" aria-label="ワイルダーの平滑と btlm_trail の計算の差。短い本数ではワイルダーの平滑、1 時間足の長い本数では btlm_trail の計算が上回る">' + "".join(parts) + "</svg>"


def skill_values(table: dict, labels) -> dict:
    """本文に入れる数（差し込み名 → 文字）。"""
    methods = ("S25", "W25", "O25")
    count = lambda h: sum(excludes_zero(table[l, h, k]) and table[l, h, k][0] > 0 for l in labels for k in methods)
    w_s = [table[l, h, "W14-S14"] for l in labels for h in HORIZONS]
    return {"SKILL_ONE": str(count("one")), "SKILL_TWENTY": str(count("twenty")),
            "W_OVER_S": str(sum(excludes_zero(v) and v[0] > 0 for v in w_s)), "W_S_ALL": str(len(w_s))}


def fig_ols_fit(n: int = 25, last: str = "2024-08-09"):
    """last までの n 本の真の値幅に当てはめた直線と、その右端の値・単純平均（MT5 日足）。"""
    full = load(MT5, "1D")
    keep = (full.date <= last).to_numpy()
    d = full[keep].iloc[-n:].reset_index(drop=True)
    z = true_range(full)[keep][-n:]
    end, _, beta, _ = window_end_scalar(z)
    line = end - beta * np.arange(n - 1, -1, -1)
    X0, X1, Y0, Y1 = 60, 560, 22, 220
    top = max(z.max(), line.max()) * 1.08
    sx = lambda j: X0 + (j + 0.5) / n * (X1 - X0)
    sy = lambda v: Y1 - v / top * (Y1 - Y0)
    parts = []
    for v in np.linspace(0, top, 4)[:-1]:
        parts.append(f'<line x1="{X0}" x2="{X1}" y1="{sy(v):.1f}" y2="{sy(v):.1f}" class="grid"/>')
        parts.append(f'<text x="{X0 - 6}" y="{sy(v) + 4:.1f}" text-anchor="end" class="tk">{v:,.0f}</text>')
    bw = (X1 - X0) / n * 0.7
    for j in range(n):
        parts.append(f'<rect x="{sx(j) - bw / 2:.1f}" y="{sy(z[j]):.1f}" width="{bw:.1f}" height="{Y1 - sy(z[j]):.1f}" class="trbar"/>')
    parts.append(f'<line x1="{sx(0):.1f}" x2="{sx(n - 1):.1f}" y1="{sy(z.mean()):.1f}" y2="{sy(z.mean()):.1f}" class="l-s20"/>')
    parts.append(f'<line x1="{sx(0):.1f}" x2="{sx(n - 1):.1f}" y1="{sy(line[0]):.1f}" y2="{sy(line[-1]):.1f}" class="l-w14"/>')
    parts.append(f'<circle cx="{sx(n - 1):.1f}" cy="{sy(end):.1f}" r="5" class="m-real"/>')
    parts.append(f'<text x="{sx(n - 1) + 10:.1f}" y="{sy(end) + 4:.1f}" class="lb blue">右端 {end:,.0f}</text>')
    parts.append(f'<text x="{sx(n - 1) + 10:.1f}" y="{sy(z.mean()) + 4:.1f}" class="lb acc">平均 {z.mean():,.0f}</text>')
    parts.append(f'<text x="{X0}" y="12" class="tk">ポイント（円）　{d.date.iloc[0]:%Y-%m-%d} 〜 {d.date.iloc[-1]:%Y-%m-%d}</text>')
    svg = f'<svg viewBox="0 0 640 240" role="img" aria-label="真の値幅の棒に当てはめた直線の右端と単純平均">' + "".join(parts) + "</svg>"
    return svg, dict(end=end, beta=beta, mean=z.mean())


def fig_tr_lines(start: str = "2024-07-01", end: str = "2024-10-15"):
    """荒れた時期の真の値幅と、ワイルダーの平滑 14・btlm_trail の計算 25（MT5 日足）。"""
    d = load(MT5, "1D")
    tr = true_range(d)
    idx = np.flatnonzero((d.date >= start) & (d.date < end))
    w, o = wilder(tr, 14), ols_end(tr, 25, int(idx[0]))
    X0, X1, Y0, Y1 = 60, 620, 16, 230
    top = tr[idx].max() * 1.05
    sx = lambda j: X0 + (j - idx[0]) / (idx[-1] - idx[0]) * (X1 - X0)
    sy = lambda v: Y1 - v / top * (Y1 - Y0)
    parts = []
    for v in np.linspace(0, top, 5)[:-1]:
        parts.append(f'<line x1="{X0}" x2="{X1}" y1="{sy(v):.1f}" y2="{sy(v):.1f}" class="grid"/>')
        parts.append(f'<text x="{X0 - 6}" y="{sy(v) + 4:.1f}" text-anchor="end" class="tk">{v:,.0f}</text>')
    bw = (X1 - X0) / len(idx) * 0.7
    for j in idx:
        parts.append(f'<rect x="{sx(j) - bw / 2:.1f}" y="{sy(tr[j]):.1f}" width="{bw:.1f}" height="{Y1 - sy(tr[j]):.1f}" class="trbar"/>')
    for arr, cls in ((w, "l-w14"), (o, "l-s20")):
        parts.append('<path d="M' + " L".join(f"{sx(j):.1f},{sy(arr[j]):.1f}" for j in idx) + f'" class="{cls}"/>')
    for j in (idx[0], idx[len(idx) // 2], idx[-1]):
        parts.append(f'<text x="{sx(j):.1f}" y="{Y1 + 16}" text-anchor="middle" class="tk">{d.date[j]:%m-%d}</text>')
    return f'<svg viewBox="0 0 640 252" role="img" aria-label="荒れた時期の真の値幅と 2 本の線。btlm_trail の計算のほうが大きく上下する">' + "".join(parts) + "</svg>"


HOURS_LABEL = {"oos_hours": "OOS の係数", "is_hours": "IS の係数"}


def fig_attrib(df: pd.DataFrame):
    """1 時間足で、足すものごとの外れの改善と週単位の 95% の幅（OOS）。時間帯の補正の 2 通りを並べる。"""
    X0, X1 = 200, 590
    keys = ("gain_beta", "gain_long", "gain_incr")
    lo = min(df[f"{k}_lo"].min() for k in keys) * 1.1
    hi = max(df[f"{k}_hi"].max() for k in keys) * 1.1
    sx = lambda v: X0 + (v - lo) / (hi - lo) * (X1 - X0)
    parts = []
    y = 34
    for _, r in df.iterrows():
        parts.append(f'<text x="14" y="{y + 26}" class="lb strong">n = {int(r.n)}</text>')
        parts.append(f'<text x="14" y="{y + 42}" class="tk">{HOURS_LABEL[r.hours]}</text>')
        for label, key, cls in (("β を足す", "gain_beta", "blue-m"), (f"{int(r.n)} 本の平均を足す", "gain_long", "amber-m"),
                                ("平均にさらに β を足す", "gain_incr", "violet-m")):
            parts.append(f'<text x="{X0 - 10}" y="{y + 4}" text-anchor="end" class="tk">{label}</text>')
            parts.append(f'<line x1="{sx(r[key + "_lo"]):.1f}" x2="{sx(r[key + "_hi"]):.1f}" y1="{y}" y2="{y}" class="whisker {cls}"/>')
            parts.append(f'<circle cx="{sx(r[key]):.1f}" cy="{y}" r="5" class="{cls}"/>')
            parts.append(f'<text x="{sx(r[key + "_hi"]) + 6:.1f}" y="{y + 4}" class="tk">{r[key]:+.3f}</text>')
            y += 22
        y += 14
    parts.insert(0, f'<line x1="{sx(0):.1f}" x2="{sx(0):.1f}" y1="20" y2="{y - 18}" class="guide"/>')
    parts.append(f'<text x="{sx(0):.1f}" y="14" text-anchor="middle" class="tk">改善なし 0</text>')
    return f'<svg viewBox="0 0 640 {y}" role="img" aria-label="平均にさらに β を足した改善の幅は、どの条件でも 0 を含む">' + "".join(parts) + "</svg>"


def p_text(p: float) -> str:
    """p の表示。"""
    return "0.001 未満" if p < 0.001 else f"{p:.3f}"


def attrib_values(df: pd.DataFrame) -> dict:
    """§4 の文に入れる値（テンプレートの差し込み名 → 文字）。"""
    rng = lambda lo, hi: f"{lo:+.2f}〜{hi:+.2f}"
    out = {}
    for tag, hours in (("O", "oos_hours"), ("I", "is_hours")):
        for n in (50, 100):
            r = df[(df.n == n) & (df.hours == hours)].iloc[0]
            k = f"{tag}{n}"
            out.update({f"INCR_{k}": rng(r.gain_incr_lo, r.gain_incr_hi), f"BETA_{k}": rng(r.gain_beta_lo, r.gain_beta_hi),
                        f"LONG_{k}": rng(r.gain_long_lo, r.gain_long_hi), f"DIFF_{k}": rng(r.long_minus_beta_lo, r.long_minus_beta_hi),
                        f"BP_{k}": p_text(r.b_p_oos), f"LP_{k}": p_text(r.l_p_oos),
                        f"COEF_{k}": f"{r.coef_oos_alone:.3f} → {r.coef_oos_with_long:.3f}"})
            if tag == "O":
                out.update({f"CORR_{n}": f"{r['corr']:.2f}", f"COEF_IS_{n}": f"{r.coef_is_alone:.3f} → {r.coef_is_with_long:.3f}",
                            f"BP_IS_{n}": p_text(r.b_p_is)})
    return out


def beta_rows(df: pd.DataFrame):
    """時間足と n の組ごとに、IS と OOS の結果を 1 行にまとめる。"""
    rows = []
    for (tf, n), g in df.groupby(["tf", "n"], sort=False):
        i, o = g[g.period == "IS"].iloc[0], g[g.period == "OOS"].iloc[0]
        direction = "同じ向き" if o.H > o.H_null else "逆向き"
        rows.append(dict(tf="1 時間足" if tf == "1h" else "日足", n=int(n), h_is=i.H * 100, h_oos=o.H * 100,
                         h_null=o.H_null * 100, h_pass=bool(o.H_pass), direction=direction, d_oos=o.D,
                         d_pass=bool(o.D_pass), gain=o.err_gain, usable=bool(o.usable)))
    return rows


def fig_hit(rows):
    """β の符号の当たり方（一致率）。偶然の 50% と比べる。"""
    X0, X1 = 110, 500
    lo, hi = 30.0, 65.0
    sx = lambda v: X0 + (v - lo) / (hi - lo) * (X1 - X0)
    top, step = 28, 34
    bottom = top + step * len(rows)
    parts = []
    for v in (30, 40, 50, 60):
        cls = "guide" if v == 50 else "grid"
        parts.append(f'<line x1="{sx(v):.1f}" x2="{sx(v):.1f}" y1="{top - 6}" y2="{bottom - 10}" class="{cls}"/>')
        parts.append(f'<text x="{sx(v):.1f}" y="{bottom + 6}" text-anchor="middle" class="tk">{v}%</text>')
    parts.append(f'<text x="{sx(50):.1f}" y="14" text-anchor="middle" class="tk">偶然 50%</text>')
    for j, r in enumerate(rows):
        y = top + step * j + 8
        parts.append(f'<text x="{X0 - 10}" y="{y + 4}" text-anchor="end" class="lb">{r["tf"]} n={r["n"]}</text>')
        parts.append(f'<circle cx="{sx(r["h_is"]):.1f}" cy="{y}" r="5" class="m-is"/>')
        parts.append(f'<circle cx="{sx(r["h_oos"]):.1f}" cy="{y}" r="5" class="m-real"/>')
        label = r["direction"] if r["h_pass"] else "通過せず"
        cls = "lb" if r["h_pass"] else "tk"
        parts.append(f'<text x="{X1 + 12}" y="{y + 4}" class="{cls}">{label}</text>')
    return f'<svg viewBox="0 0 640 {bottom + 14}" role="img" aria-label="時間足と本数ごとの一致率と偶然の 50 パーセントの比較">' + "".join(parts) + "</svg>"


def fig_gain(rows, need: float = 0.5):
    """検定 2 を通過した組の外れの改善と、実用の基準。"""
    passed = [r for r in rows if r["d_pass"]]
    X0, X1 = 110, 620
    hi = need * 1.2
    sx = lambda v: X0 + v / hi * (X1 - X0)
    top, step = 30, 40
    bottom = top + step * len(passed)
    parts = []
    for j, r in enumerate(passed):
        y = top + step * j
        parts.append(f'<text x="{X0 - 10}" y="{y + 15}" text-anchor="end" class="lb">{r["tf"]} n={r["n"]}</text>')
        parts.append(f'<rect x="{X0}" y="{y}" width="{sx(r["gain"]) - X0:.1f}" height="22" class="tfbar"/>')
        parts.append(f'<text x="{sx(r["gain"]) + 6:.1f}" y="{y + 15}" class="lb">{r["gain"]:.2f}%ポイント</text>')
    parts.append(f'<line x1="{sx(need):.1f}" x2="{sx(need):.1f}" y1="{top - 8}" y2="{bottom}" class="guide"/>')
    parts.append(f'<text x="{sx(need):.1f}" y="14" text-anchor="middle" class="lb acc">実用の基準 {need}%ポイント</text>')
    parts.append(f'<line x1="{X0}" x2="{X0}" y1="{top - 8}" y2="{bottom}" class="grid"/>')
    return f'<svg viewBox="0 0 640 {bottom + 10}" role="img" aria-label="外れの改善は実用の基準に届かない">' + "".join(parts) + "</svg>"


PERIODS_N = (3, 5, 10, 14, 20, 40, 100)


def period_stats():
    """母数 n ごとに、次の値幅を n 本の単純平均で見積もったときの外れ（平均の外れ ÷ 値幅の平均・%）。

    見積もり = k × ATR(n)。k は（実際 ÷ ATR）の中央値。最初の 100 本は除く（どの n でも同じ起点にそろえる）。
    """
    cases = [("MT5 日足・次の 1 日", MT5, "1D", 1, None, None), ("MT5 日足・次の 5 日", MT5, "1D", 5, None, None),
             ("MT5 1 時間足・次の 5 本", MT5, "1h", 5, None, None),
             ("Dukascopy 日足 2014〜2020・次の 5 日", DUKA, "1D", 5, "2014-01-01", "2020-05-01")]
    rows = []
    for label, path, tf, t, start, end in cases:
        d = load(path, tf)
        if start:
            d = d[(d.date >= start) & (d.date < end)].reset_index(drop=True)
        tr = true_range(d)
        if t == 1:
            f = np.concatenate([tr[1:], [np.nan]])
        else:
            f = R.forward_extreme(d.high.to_numpy(), t, "max") - R.forward_extreme(d.low.to_numpy(), t, "min")
        errs = []
        for n in PERIODS_N:
            a = pd.Series(tr).rolling(n).mean().to_numpy()
            ok = np.isfinite(a) & np.isfinite(f) & (np.arange(len(a)) >= 100)
            k = np.median(f[ok] / a[ok])
            errs.append(np.mean(np.abs(f[ok] - k * a[ok])) / np.mean(f[ok]) * 100)
        rows.append((label, errs))
    return rows


def main(out_path: str):
    tr_svg, tr_cap = fig_tr()
    tf_svg, tf_rows = fig_tf()
    hour_svg, hours = fig_hour()
    sc_svg, sc = fig_scale()
    d1 = load(MT5, "1D")
    a1 = R.atr(d1.high.to_numpy(), d1.low.to_numpy(), d1.close.to_numpy())
    last = dict(date=f"{d1.date.iloc[-1]:%Y-%m-%d}", atr=a1[-1], close=d1.close.iloc[-1])
    top_hours = "・".join(f"{h}時" for h in hours.sort_values(ascending=False).index[:3])
    low_hours = "・".join(f"{h}時" for h in hours.sort_values().index[:3])
    tf_map = {tf: v for tf, _, v in tf_rows}
    gs = gap_stats()
    prow = period_stats()
    period_head = "".join(f"<th>{n}</th>" for n in PERIODS_N)
    period_rows = "".join(
        f"<tr><td>{label}</td>" + "".join(
            f'<td class="n{" best" if e == min(errs) else ""}">{e:.1f}%</td>' for e in errs) + "</tr>"
        for label, errs in prow)
    exit_svg, ex = fig_exit()
    frames = series_frames()
    trs = {"1h": frames[0][1], "1D": frames[1][1]}
    sr = {tf: smooth_rows(tr) for tf, tr in trs.items()}
    follow_tbl = "".join(
        f'<tr><td>{name}</td><td class="n">{half} 本</td><td class="n">{most} 本</td>'
        f'<td class="n">{c[0]:.3f}</td><td class="n">{c[1]:.3f}</td></tr>'
        for name, half, most, c in follow_rows([trs["1h"], trs["1D"]]))
    smooth_tbl = "".join(
        f'<tr><td>{a[0]} {a[1]}</td><td class="n">{a[2]:.1f}%</td><td class="n">{a[3]:.1f}%</td>'
        f'<td class="n">{b[2]:.1f}%</td><td class="n">{b[3]:.1f}%</td></tr>'
        for a, b in zip(sr["1h"], sr["1D"]))
    fit_svg, fit = fig_ols_fit()
    trend = trend_rows(trs["1h"])
    trend_tbl = "".join(
        f'<tr><td>{name}</td><td class="n">{lag[0]} 本</td><td class="n">{lag[1]} 本</td><td class="n">{mv:.2f}%</td><td class="n">{e1:.1f}%</td><td class="n">{e20:.1f}%</td></tr>'
        for name, lag, mv, e1, e20 in trend)
    move_ratio = trend[2][2] / trend[1][2]
    best = best_rows([(label, tr) for label, tr, _ in frames])
    labels = [label for label, _, _ in frames]
    skill = skill_table(frames, np.random.default_rng(SKILL_SEED))
    best_tbl = "".join(
        f'<tr><td>{r["label"]}</td><td>{r["best_one"]}（{r["err_one"]:.1f}%）</td><td>{r["best_twenty"]}（{r["err_twenty"]:.1f}%）</td>'
        f'<td class="n">{r["ols_err_one"]:.1f}%</td><td class="n">{r["ols_err_twenty"]:.1f}%</td></tr>'
        for r in best)
    beta = beta_rows(pd.read_csv(BETA_TESTS))
    beta_tbl = "".join(
        f'<tr><td style="white-space:nowrap">{r["tf"]}</td><td class="n">{r["n"]}</td><td class="n">{r["h_oos"]:.1f}%</td>'
        f'<td>{r["direction"] if r["h_pass"] else "通過せず"}</td><td>{"通過" if r["d_pass"] else "通過せず"}</td>'
        f'<td class="n">{(np.exp(r["d_oos"]) - 1) * 100:+.1f}%</td><td class="n">{r["gain"]:+.2f}</td></tr>'
        for r in beta)
    passed = [r for r in beta if r["d_pass"]]
    attrib = pd.read_csv(BETA_ATTRIB)
    weights = {name: avg(np.r_[np.full(300, 1000.0), 1100.0])[-1] - 1000.0 for name, avg in (("sma", lambda t: sma(t, 100)), ("ols", lambda t: ols_end(t, 100, len(t) - 1)))}
    bs = break_stats()
    a20_med, a20_p99 = atr20_diff()
    hrows, herr, hq, hstart = hold_stats()
    hold_rows = "".join(
        f'<tr><td>{t} 日</td><td class="n">{r:.2f} 倍</td><td class="n">{m:.2f} 倍</td><td class="n">{p80:.2f} 倍</td><td class="n">{p95:.2f} 倍</td></tr>'
        for t, r, m, p80, p95 in hrows)
    h3 = {t: (r, p95) for t, r, _, _, p95 in hrows}
    brk_rows = "".join(
        f'<tr><td>{r["name"]}</td><td class="n">{r["bars"]:,} 本（{r["share_bars"]:.1f}%）</td>'
        f'<td class="n">{r["total"]:,.0f} ポイント（{r["share_gap"]:.0f}%）</td>'
        f'<td class="n">{r["mean"]:.2f} ポイント</td><td class="n">+{r["pct"]:.1f}%</td></tr>'
        for r in bs)
    gap_rows = "".join(
        f'<tr><td>{name}</td><td class="n">+{(gs[tf][0] - 1) * 100:.1f}%</td><td class="n">{gs[tf][1]:.1f}%</td></tr>'
        for tf, name in (("15m", "15 分足"), ("1h", "1 時間足"), ("1D", "日足")))
    tpl = (HERE / "page_template.html").read_text(encoding="utf-8")
    page = (tpl.replace("{{FIG_TR}}", tr_svg).replace("{{TR_CAP}}", tr_cap)
            .replace("{{FIG_SMOOTH}}", fig_smooth()).replace("{{FIG_HISTORY}}", fig_history())
            .replace("{{FIG_TF}}", tf_svg).replace("{{FIG_HOUR}}", hour_svg).replace("{{FIG_SCALE}}", sc_svg)
            .replace("{{LAST_DATE}}", last["date"]).replace("{{LAST_ATR}}", f"{last['atr']:,.0f}")
            .replace("{{LAST_CLOSE}}", f"{last['close']:,.0f}").replace("{{LAST_PCT}}", f"{last['atr'] / last['close'] * 100:.2f}")
            .replace("{{TF_1H}}", f"{tf_map['1h']:.3f}").replace("{{TF_1D}}", f"{tf_map['1D']:.2f}")
            .replace("{{TF_5M}}", f"{tf_map['5m']:.3f}")
            .replace("{{GAP_ROWS}}", gap_rows)
            .replace("{{GAP_MON}}", f"{(gs['mon'] - 1) * 100:.1f}").replace("{{GAP_OTHER}}", f"{(gs['other'] - 1) * 100:.1f}")
            .replace("{{USE_SIZE}}", f"{10000 / last['atr']:.1f}").replace("{{USE_SIZE2}}", f"{10000 / (2 * last['atr']):.1f}")
            .replace("{{USE_STOP3}}", f"{last['atr'] * h3[3][1]:,.0f}").replace("{{USE_P95_3}}", f"{h3[3][1]:.2f}")
            .replace("{{USE_COST}}", f"{50 / last['atr'] * 100:.1f}").replace("{{USE_COST_Q}}", f"{50 / (last['atr'] / 3) * 100:.0f}")
            .replace("{{USE_DEV}}", f"{3000 / last['atr']:.1f}")
            .replace("{{HOLD_ROWS}}", hold_rows).replace("{{HOLD_START}}", hstart)
            .replace("{{H3_P95}}", f"{h3[3][1]:.1f}").replace("{{H3_R}}", f"{h3[3][0] / h3[1][0]:.1f}")
            .replace("{{H5_R}}", f"{h3[5][0] / h3[1][0]:.1f}")
            .replace("{{A3_LO}}", f"{hq[0]:.2f}").replace("{{A3_HI}}", f"{hq[1]:.2f}")
            .replace("{{A3_CORR}}", f"{herr['a3'][0]:.3f}").replace("{{A20_CORR}}", f"{herr['a20'][0]:.3f}")
            .replace("{{A3_ERR}}", f"{herr['a3'][1]:,.0f}").replace("{{A20_ERR}}", f"{herr['a20'][1]:,.0f}")
            .replace("{{FIG_OLS_FIT}}", fit_svg).replace("{{FIG_TR_LINES}}", fig_tr_lines())
            .replace("{{FIT_END}}", f"{fit['end']:,.0f}").replace("{{FIT_MEAN}}", f"{fit['mean']:,.0f}").replace("{{FIT_BETA}}", f"{fit['beta']:+.1f}")
            .replace("{{W_SMA}}", f"{weights['sma']:.2f}").replace("{{W_OLS}}", f"{weights['ols']:.2f}")
            .replace("{{TREND_ROWS}}", trend_tbl).replace("{{BEST_ROWS}}", best_tbl).replace("{{OLS_WINS}}", str(ols_wins(best))).replace("{{MOVE_RATIO}}", f"{move_ratio:.1f}")
            .replace("{{FIG_ATTRIB}}", fig_attrib(attrib))
            .replace("{{FIG_SKILL}}", fig_skill(skill, labels)).replace("{{FIG_DIFF}}", fig_diff(skill, labels))
            .replace("{{FIG_HIT}}", fig_hit(beta)).replace("{{FIG_GAIN}}", fig_gain(beta)).replace("{{BETA_ROWS}}", beta_tbl)
            .replace("{{GAIN_LIST}}", "・".join(f"{r['gain']:.2f}" for r in passed))
            .replace("{{SMALL_LIST}}", "〜".join(f"{abs(np.exp(r['d_oos']) - 1) * 100:.0f}" for r in passed))
            .replace("{{FIG_IMPULSE}}", fig_impulse()).replace("{{FIG_EXIT}}", exit_svg).replace("{{SMOOTH_ROWS}}", smooth_tbl).replace("{{FOLLOW_ROWS}}", follow_tbl)
            .replace("{{EX_DATE}}", ex["date"]).replace("{{EX_TR}}", f"{ex['tr']:,.0f}")
            .replace("{{EX_OUT_DATE}}", ex["out_date"]).replace("{{EX_OUT_TR}}", f"{ex['out_tr']:,.0f}")
            .replace("{{EX_A0}}", f"{ex['a0']:,.0f}").replace("{{EX_A1}}", f"{ex['a1']:,.0f}").replace("{{EX_A_PCT}}", f"{-ex['a_pct']:.1f}")
            .replace("{{EX_W0}}", f"{ex['w0']:,.0f}").replace("{{EX_W1}}", f"{ex['w1']:,.0f}").replace("{{EX_W_PCT}}", f"{-ex['w_pct']:.1f}")
            .replace("{{BRK_ROWS}}", brk_rows).replace("{{BRK_PLAIN}}", f"{bs[1]['pct']:.1f}")
            .replace("{{A20_MED}}", f"{a20_med:.1f}").replace("{{A20_P99}}", f"{a20_p99:.1f}")
            .replace("{{BRK_BARS}}", f"{bs[0]['share_bars']:.1f}").replace("{{BRK_GAP}}", f"{bs[0]['share_gap']:.0f}")
            .replace("{{PERIOD_HEAD}}", period_head).replace("{{PERIOD_ROWS}}", period_rows)
            .replace("{{TOP_HOURS}}", top_hours).replace("{{LOW_HOURS}}", low_hours)
            .replace("{{SC5}}", f"{sc[5]:.1f}").replace("{{SC20}}", f"{sc[20]:.1f}").replace("{{SC1}}", f"{sc[1]:.2f}"))
    for key, value in {**attrib_values(attrib), **skill_values(skill, labels)}.items():
        page = page.replace("{{" + key + "}}", value)
    Path(out_path).write_text(inject_toc(page), encoding="utf-8")
    print("wrote", out_path, len(page), "| 1h 比", {k: round(v, 2) for k, v in sc.items()},
          "| tf", {k: round(v, 3) for k, v in tf_map.items()}, "| hours top", top_hours, "low", low_hours)


if __name__ == "__main__":
    main(sys.argv[1])
