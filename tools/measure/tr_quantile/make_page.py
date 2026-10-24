"""値幅の分位による閾値の公開レポート（図は実データと results/ から描く）。

実行: lightweight-charts-python-main/.venv/bin/python -m tools.measure.tr_quantile.make_page <出力 html>
前提: run.py を実行済み（results/rates.csv・verdict.csv）。
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

from tools.measure.page_toc import inject_toc
from tools.measure.tr_quantile import tq as Q
from tools.measure.tr_quantile.run import load

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
RES = HERE / "results"
TF_JA = {"15m": "15 分足", "1h": "1 時間足", "4h": "4 時間足", "1D": "日足"}
M_JA = {"A": "A 固定", "B": "B ATR で割る", "C": "C 荒れ方の区分", "D": "D 区分 × 向き"}


def fig_hist():
    """1 時間足（IS）の真の値幅 ÷ 終値の分布と、80%・95% の分位。"""
    y, _, _, _ = load("IS", "1h")
    q80, q95 = np.quantile(y, [0.8, 0.95])
    hi = np.quantile(y, 0.995)
    bins = np.linspace(0, hi, 61)
    h, _ = np.histogram(y[y <= hi], bins=bins)
    X0, X1, Y0, Y1 = 60, 620, 24, 220
    sx = lambda v: X0 + v / hi * (X1 - X0)
    sy = lambda c: Y1 - c / h.max() * (Y1 - Y0)
    parts = []
    for i, c in enumerate(h):
        cls = "hb hot" if bins[i] >= q95 else ("hb warm" if bins[i] >= q80 else "hb")
        parts.append(f'<rect x="{sx(bins[i]):.1f}" y="{sy(c):.1f}" width="{sx(bins[i + 1]) - sx(bins[i]) - 0.6:.1f}" height="{Y1 - sy(c):.1f}" class="{cls}"/>')
    for k, (v, name) in enumerate(((q80, "80% の分位"), (q95, "95% の分位"))):
        parts.append(f'<line x1="{sx(v):.1f}" x2="{sx(v):.1f}" y1="{Y0}" y2="{Y1}" class="qline"/>')
        parts.append(f'<text x="{sx(v) + 4:.1f}" y="{Y0 + 12 + k * 16}" class="lb">{name} {v:.2f}%</text>')
    for v in np.linspace(0, hi, 6):
        parts.append(f'<text x="{sx(v):.1f}" y="{Y1 + 16}" text-anchor="middle" class="tk">{v:.2f}%</text>')
    parts.append(f'<text x="{(X0 + X1) / 2}" y="{Y1 + 32}" text-anchor="middle" class="tk">次の足の真の値幅 ÷ 終値</text>')
    return (f'<svg viewBox="0 0 640 260" role="img" aria-label="1 時間足の値幅の分布と 80%・95% の分位">'
            + "".join(parts) + "</svg>"), q80, q95


def worked_example(q80: float):
    """MT5 1 時間足の実際の 2 本（起点の足と次の足）で、測る値の計算を数値で示す。

    週明けで窓が開いた最新の場面（次の足の真の値幅が高値 − 安値より大きい）を選ぶ。
    """
    d = pd.read_csv(ROOT / "data/marketdata/rollups/jp225_mt5/jp225_mt5_1h.csv",
                    usecols=["date", "open", "high", "low", "close"], parse_dates=["date"]).iloc[:-1].reset_index(drop=True)
    o, h, l, c = (d[k].to_numpy(float) for k in ("open", "high", "low", "close"))
    tr = Q.true_range(h, l, c)
    gap_min = d.date.diff().dt.total_seconds().to_numpy() / 60
    cand = np.flatnonzero((tr > (h - l) * 1.5) & (gap_min > 600))
    j = int(cand[-1])      # 次の足（週明け）
    t = j - 1              # 起点の足（週末の最後の足）
    c1, c2, c3 = h[j] - l[j], abs(h[j] - c[t]), abs(l[j] - c[t])
    y = tr[j] / c[t] * 100
    return dict(t_date=f"{d.date[t]:%Y-%m-%d %H:%M}", j_date=f"{d.date[j]:%Y-%m-%d %H:%M}", c_t=c[t],
                o=o[j], h=h[j], l=l[j], c=c[j], c1=c1, c2=c2, c3=c3, tr=tr[j], y=y, q80=q80,
                over=y > q80, hl_pct=c1 / c[t] * 100)


def points_vs_pct(tf="1h", q=0.8):
    """閾値をポイントのまま決めた場合と、終値に対する割合で決めた場合の、照合する期間での超過率。"""
    from tools.measure.tr_quantile.run import PERIODS
    out = {}
    for period in ("IS", "OOS"):
        path, start, end = PERIODS[period]
        d = pd.read_csv(ROOT / path.format(tf=tf), usecols=["date", "high", "low", "close"], parse_dates=["date"]).iloc[:-1]
        d = d[(d.date >= start) & (d.date < end)].reset_index(drop=True)
        h, l, c = (d[k].to_numpy(float) for k in ("high", "low", "close"))
        tr = Q.true_range(h, l, c)
        nxt = np.concatenate([tr[1:], [np.nan]])
        ok = np.isfinite(nxt) & (np.arange(len(c)) > 0)
        out[period] = (nxt[ok], nxt[ok] / c[ok] * 100, c[ok].mean())
    (pi, yi, ci), (po, yo, co) = out["IS"], out["OOS"]
    qp, qy = np.quantile(pi, q), np.quantile(yi, q)
    return dict(ci=ci, co=co, qp=qp, qy=qy, rp=(po > qp).mean() * 100, ry=(yo > qy).mean() * 100)


def fig_series():
    """MT5 1 時間足の 2024-07〜08（荒れた時期を含む）: 値幅と、A（固定）・C（区分）の閾値。"""
    yi, vi, ri, _ = load("IS", "1h")
    edges = np.quantile(vi, [0.2, 0.4, 0.6, 0.8])
    pa = Q.fit("A", yi, vi, ri, 0.8, edges)
    pc = Q.fit("C", yi, vi, ri, 0.8, edges)
    d = pd.read_csv(ROOT / "data/marketdata/rollups/jp225_mt5/jp225_mt5_1h.csv",
                    usecols=["date", "high", "low", "close"], parse_dates=["date"]).iloc[:-1]
    d = d[(d.date >= "2020-05-01")].reset_index(drop=True)
    y, v, r, ok = Q.features(d.high.to_numpy(float), d.low.to_numpy(float), d.close.to_numpy(float))
    dates = d.date[ok].reset_index(drop=True)
    m = (dates >= "2024-07-15") & (dates < "2024-08-20")
    yy, vv, rr, dd = y[m.to_numpy()], v[m.to_numpy()], r[m.to_numpy()], dates[m].reset_index(drop=True)
    ta = Q.threshold("A", pa, vv, rr, edges)
    tc = Q.threshold("C", pc, vv, rr, edges)
    n = len(yy)
    hi = np.quantile(yy, 0.995) * 1.1
    X0, X1, Y0, Y1 = 60, 620, 20, 230
    sx = lambda i: X0 + i / (n - 1) * (X1 - X0)
    sy = lambda val: Y1 - min(val, hi) / hi * (Y1 - Y0)
    parts = []
    for g in np.linspace(0, hi, 5)[:-1]:
        parts.append(f'<line x1="{X0}" x2="{X1}" y1="{sy(g):.1f}" y2="{sy(g):.1f}" class="grid"/>')
        parts.append(f'<text x="{X0 - 6}" y="{sy(g) + 4:.1f}" text-anchor="end" class="tk">{g:.1f}%</text>')
    bw = max((X1 - X0) / n * 0.8, 0.8)
    for i in range(n):
        cls = "sb over" if yy[i] > ta[i] else "sb"
        parts.append(f'<rect x="{sx(i) - bw / 2:.1f}" y="{sy(yy[i]):.1f}" width="{bw:.1f}" height="{Y1 - sy(yy[i]):.1f}" class="{cls}"/>')
    parts.append('<path d="M' + " L".join(f"{sx(i):.1f},{sy(t):.1f}" for i, t in enumerate(ta)) + '" class="th-a"/>')
    parts.append('<path d="M' + " L".join(f"{sx(i):.1f},{sy(t):.1f}" for i, t in enumerate(tc)) + '" class="th-c"/>')
    for i in (0, n // 3, 2 * n // 3, n - 1):
        parts.append(f'<text x="{sx(i):.1f}" y="{Y1 + 16}" text-anchor="middle" class="tk">{dd[i]:%m-%d}</text>')
    over_a = float((yy > ta).mean() * 100)
    over_c = float((yy > tc).mean() * 100)
    return (f'<svg viewBox="0 0 640 260" role="img" aria-label="2024 年 7〜8 月の 1 時間足の値幅と 2 つの閾値">'
            + "".join(parts) + "</svg>"), over_a, over_c


def fig_buckets(t, tf="1h", q=0.8):
    """区分ごとの超過率（4 つの方法）と目標の帯。"""
    g = t[(t.tf == tf) & (t.q == q)]
    groups = ["全体", "区分1", "区分2", "区分3", "区分4", "区分5"]
    labels = ["全体", "区分 1（静か）", "区分 2", "区分 3", "区分 4", "区分 5（荒れ）"]
    target = (1 - q) * 100
    vmax = max(55.0, float(g.rate.max()) + 5)
    X0, X1 = 150, 630
    sx = lambda v: X0 + v / vmax * (X1 - X0)
    rh = 46
    H = 30 + rh * len(groups) + 40
    parts = [f'<rect x="{sx(target - 2):.1f}" y="20" width="{sx(target + 2) - sx(target - 2):.1f}" height="{H - 60}" class="band"/>',
             f'<line x1="{sx(target):.1f}" x2="{sx(target):.1f}" y1="20" y2="{H - 40}" class="tgt"/>']
    for v in range(0, int(vmax) + 1, 10):
        parts.append(f'<line x1="{sx(v):.1f}" x2="{sx(v):.1f}" y1="20" y2="{H - 40}" class="grid"/>')
        parts.append(f'<text x="{sx(v):.1f}" y="{H - 24}" text-anchor="middle" class="tk">{v}%</text>')
    parts.append(f'<text x="{(X0 + X1) / 2}" y="{H - 6}" text-anchor="middle" class="tk">閾値を超えた割合（目標 {target:.0f}%・帯は ±2 ポイント）</text>')
    offs = {"A": -13, "B": -4, "C": 5, "D": 14}
    gg = g.set_index(["method", "group"])
    for j, (grp, lab) in enumerate(zip(groups, labels)):
        y = 30 + j * rh + rh / 2
        n = int(gg.loc[("A", grp), "n"])
        parts.append(f'<text x="{X0 - 10}" y="{y - 2:.1f}" text-anchor="end" class="lb">{lab}</text>')
        parts.append(f'<text x="{X0 - 10}" y="{y + 13:.1f}" text-anchor="end" class="tk">{n:,} 本</text>')
        for m in Q.METHODS:
            v = gg.loc[(m, grp), "rate"]
            parts.append(f'<circle cx="{sx(v):.1f}" cy="{y + offs[m]:.1f}" r="5" class="m-{m}"/>')
    return f'<svg viewBox="0 0 680 {H}" role="img" aria-label="{TF_JA[tf]}・目標 {target:.0f}% の区分ごとの超過率">' + "".join(parts) + "</svg>"


def fig_tol(rates):
    """100 本の格子で、目標・合格の幅・C の全体・静かな段・荒れた段の超えた本数を見せる。"""
    panels = [("目標", 20, "20 本"), ("合格の幅（下限）", 18, "18 本"), ("合格の幅（上限）", 22, "22 本"),
              ("C・全体", rates["all"], f"{rates['all']:.1f} 本"), ("C・静かな段", rates["q"], f"{rates['q']:.1f} 本"),
              ("C・荒れた段", rates["h"], f"{rates['h']:.1f} 本")]
    cell, gap, pw = 11, 2, 10 * 13
    W = 20 + len(panels) * (pw + 24)
    parts = []
    for k, (title, n, lab) in enumerate(panels):
        x0 = 20 + k * (pw + 24)
        filled = int(round(n))
        for i in range(100):
            r, c = divmod(i, 10)
            cls = "g-on" if i < filled else "g-off"
            parts.append(f'<rect x="{x0 + c * (cell + gap)}" y="{40 + r * (cell + gap)}" width="{cell}" height="{cell}" rx="2" class="{cls}"/>')
        ok = 18 <= n <= 22
        parts.append(f'<text x="{x0 + pw / 2:.1f}" y="18" text-anchor="middle" class="lb strong">{title}</text>')
        parts.append(f'<text x="{x0 + pw / 2:.1f}" y="34" text-anchor="middle" class="tk">{lab}{"" if k < 3 else ("・幅の中" if ok else "・幅の外")}</text>')
    return f'<svg viewBox="0 0 {W} 180" role="img" aria-label="100 本のうち閾値を超えた本数の比較">' + "".join(parts) + "</svg>"


def fig_verdict(v):
    rows = [(tf, q) for tf in ("15m", "1h", "4h", "1D") for q in (0.8, 0.95)]
    X0, cw, rh, Y0 = 170, 110, 36, 40
    W = X0 + cw * 4 + 10
    H = Y0 + rh * len(rows) + 10
    vv = v.set_index(["tf", "q", "method"])
    best = v.loc[v.groupby(["tf", "q"]).max_abs_err.idxmin()].set_index(["tf", "q"]).method
    parts = []
    for j, m in enumerate(Q.METHODS):
        parts.append(f'<text x="{X0 + j * cw + cw / 2:.1f}" y="26" text-anchor="middle" class="lb strong">{M_JA[m]}</text>')
    for i, (tf, q) in enumerate(rows):
        y = Y0 + i * rh
        parts.append(f'<text x="{X0 - 10}" y="{y + rh / 2 + 4:.1f}" text-anchor="end" class="lb">{TF_JA[tf]}・目標 {(1 - q) * 100:.0f}%</text>')
        for j, m in enumerate(Q.METHODS):
            e = vv.loc[(tf, q, m), "max_abs_err"]
            cls = "c-best" if best.loc[(tf, q)] == m else ("c-bad" if e > 8 else "c-mid")
            parts.append(f'<rect x="{X0 + j * cw + 3}" y="{y + 3}" width="{cw - 6}" height="{rh - 6}" rx="4" class="{cls}"/>')
            parts.append(f'<text x="{X0 + j * cw + cw / 2:.1f}" y="{y + rh / 2 + 4:.1f}" text-anchor="middle" class="cell">{e:.1f}</text>')
    return f'<svg viewBox="0 0 {W} {H}" role="img" aria-label="方法ごとの最大の外れ（ポイント）">' + "".join(parts) + "</svg>"


def main(out_path: str):
    t = pd.read_csv(RES / "rates.csv")
    v = pd.read_csv(RES / "verdict.csv")
    hist_svg, q80, q95 = fig_hist()
    ser_svg, over_a, over_c = fig_series()
    ex = worked_example(q80)
    pv = points_vs_pct()
    best = max(("候補 1", ex["c1"]), ("候補 2", ex["c2"]), ("候補 3", ex["c3"]), key=lambda z: z[1])[0]
    ex_rows = (f'<tr><td>起点の足の終値（{ex["t_date"]} UTC）</td><td class="n">{ex["c_t"]:,.1f}</td><td>前の終値・割る数</td></tr>'
               f'<tr><td>次の足（{ex["j_date"]} UTC）の始値・高値・安値・終値</td><td class="n">{ex["o"]:,.1f}・{ex["h"]:,.1f}・{ex["l"]:,.1f}・{ex["c"]:,.1f}</td><td>週明けで窓が開いた足</td></tr>'
               f'<tr><td>候補 1：高値 − 安値</td><td class="n">{ex["h"]:,.1f} − {ex["l"]:,.1f} ＝ {ex["c1"]:,.1f}</td><td></td></tr>'
               f'<tr><td>候補 2：|高値 − 前の終値|</td><td class="n">|{ex["h"]:,.1f} − {ex["c_t"]:,.1f}| ＝ {ex["c2"]:,.1f}</td><td></td></tr>'
               f'<tr><td>候補 3：|安値 − 前の終値|</td><td class="n">|{ex["l"]:,.1f} − {ex["c_t"]:,.1f}| ＝ {ex["c3"]:,.1f}</td><td></td></tr>'
               f'<tr class="hl"><td>真の値幅（3 つのうち一番大きい＝{best}）</td><td class="n">{ex["tr"]:,.1f} ポイント</td><td></td></tr>'
               f'<tr class="hl"><td>測る値 ＝ 真の値幅 ÷ 起点の足の終値</td><td class="n">{ex["tr"]:,.1f} ÷ {ex["c_t"]:,.1f} ＝ {ex["y"]:.2f}%</td><td>80% の分位 {q80:.2f}% を{"超えた" if ex["over"] else "超えていない"}</td></tr>')
    g1 = t[(t.tf == "1h") & (t.q == 0.8)].set_index(["method", "group"]).rate
    tpl = (HERE / "page_template.html").read_text(encoding="utf-8")
    page = (tpl.replace("{{FIG_HIST}}", hist_svg).replace("{{Q80}}", f"{q80:.2f}").replace("{{Q95}}", f"{q95:.2f}")
            .replace("{{EX_ROWS}}", ex_rows).replace("{{EX_HL_PCT}}", f"{ex['hl_pct']:.2f}")
            .replace("{{EX_Y}}", f"{ex['y']:.2f}").replace("{{EX_TR}}", f"{ex['tr']:,.0f}")
            .replace("{{PV_CI}}", f"{pv['ci']:,.0f}").replace("{{PV_CO}}", f"{pv['co']:,.0f}")
            .replace("{{PV_RATIO}}", f"{pv['co'] / pv['ci']:.1f}")
            .replace("{{PV_QP}}", f"{pv['qp']:.0f}").replace("{{PV_QY}}", f"{pv['qy']:.3f}")
            .replace("{{PV_RP}}", f"{pv['rp']:.1f}").replace("{{PV_RY}}", f"{pv['ry']:.1f}")
            .replace("{{PV_X}}", f"{pv['rp'] / 20:.1f}")
            .replace("{{FIG_SERIES}}", ser_svg).replace("{{OVER_A}}", f"{over_a:.0f}").replace("{{OVER_C}}", f"{over_c:.0f}")
            .replace("{{FIG_B20}}", fig_buckets(t, "1h", 0.8)).replace("{{FIG_B5}}", fig_buckets(t, "1h", 0.95))
            .replace("{{FIG_VERDICT}}", fig_verdict(v))
            .replace("{{FIG_TOL}}", fig_tol({"all": g1[("C", "全体")], "q": g1[("C", "区分1")], "h": g1[("C", "区分5")]}))
            .replace("{{C_ALL_N}}", f"{g1[('C', '全体')]:.1f}").replace("{{C1_N}}", f"{g1[('C', '区分1')]:.1f}")
            .replace("{{C5_N}}", f"{g1[('C', '区分5')]:.1f}")
            .replace("{{A5}}", f"{g1[('A', '区分5')]:.1f}").replace("{{C5}}", f"{g1[('C', '区分5')]:.1f}")
            .replace("{{C1}}", f"{g1[('C', '区分1')]:.1f}").replace("{{A1}}", f"{g1[('A', '区分1')]:.1f}")
            .replace("{{N_PASS}}", str(int(v["pass"].sum()))).replace("{{N_ALL}}", str(len(v))))
    Path(out_path).write_text(inject_toc(page), encoding="utf-8")
    print("wrote", out_path, len(page), "A超過", round(over_a, 1), "C超過", round(over_c, 1))


if __name__ == "__main__":
    main(sys.argv[1])
