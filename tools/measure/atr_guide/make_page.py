"""値幅（ATR）の解説ページ。図はすべて実データから描く。

実行: lightweight-charts-python-main/.venv/bin/python -m tools.measure.atr_guide.make_page <出力 html>
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

from tools.measure.reach_prob import reach as R

HERE = Path(__file__).resolve().parent
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
            .replace("{{TOP_HOURS}}", top_hours).replace("{{LOW_HOURS}}", low_hours)
            .replace("{{SC5}}", f"{sc[5]:.1f}").replace("{{SC20}}", f"{sc[20]:.1f}").replace("{{SC1}}", f"{sc[1]:.2f}"))
    Path(out_path).write_text(page, encoding="utf-8")
    print("wrote", out_path, len(page), "| 1h 比", {k: round(v, 2) for k, v in sc.items()},
          "| tf", {k: round(v, 3) for k, v in tf_map.items()}, "| hours top", top_hours, "low", low_hours)


if __name__ == "__main__":
    main(sys.argv[1])
