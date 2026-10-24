"""水準に届く確率の報告ページ（図と表は実データと results/ から作る）。

実行: lightweight-charts-python-main/.venv/bin/python -m tools.measure.reach_prob.make_page <出力 html>
前提: run.py を実行済み（results/table.csv・verdict.csv）。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

from tools.measure.page_toc import inject_toc
from tools.measure.reach_prob import reach as R

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
RES = HERE / "results"
TF_JA = {"15m": "15 分足", "1h": "1 時間足", "4h": "4 時間足", "1D": "日足"}
DIR_JA = {"up": "上", "down": "下"}


def fig_example(n_before=30, t_ahead=5):
    """MT5 1 時間足の実際の場面: 起点の終値、ATR の 1・2 倍の水準、先の 5 本。"""
    d = pd.read_csv(ROOT / "data/marketdata/rollups/jp225_mt5/jp225_mt5_1h.csv",
                    usecols=["date", "open", "high", "low", "close"], parse_dates=["date"]).iloc[:-1].reset_index(drop=True)
    o, h, l, c = (d[k].to_numpy(float) for k in ("open", "high", "low", "close"))
    a = R.atr(h, l, c)
    # 先の 5 本で上の 1 倍に届き、2 倍には届かない最後の起点
    t0 = None
    for t in range(len(d) - t_ahead - 1, 100, -1):
        mx = h[t + 1:t + 1 + t_ahead].max()
        if c[t] + a[t] <= mx < c[t] + 2 * a[t]:
            t0 = t
            break
    sl = slice(t0 - n_before, t0 + t_ahead + 1)
    oo, hh, ll, cc = o[sl], h[sl], l[sl], c[sl]
    dates = d.date[sl].reset_index(drop=True)
    n = len(cc)
    k0 = n_before
    levels = [(m, c[t0] + m * a[t0]) for m in (2, 1, -1, -2)]
    vals = np.concatenate([hh, ll, [v for _, v in levels]])
    lo, hi = vals.min(), vals.max()
    pad = (hi - lo) * 0.06
    lo, hi = lo - pad, hi + pad
    X0, X1, Y0, Y1 = 70, 560, 20, 270
    sx = lambda i: X0 + (i + 0.5) / n * (X1 - X0)
    sy = lambda v: Y1 - (v - lo) / (hi - lo) * (Y1 - Y0)
    w = (X1 - X0) / n * 0.6
    parts = [f'<rect x="{sx(k0 + 0.5):.1f}" y="{Y0}" width="{sx(n - 0.5) - sx(k0 + 0.5):.1f}" height="{Y1 - Y0}" class="win"/>',
             f'<text x="{(sx(k0 + 0.5) + sx(n - 0.5)) / 2:.1f}" y="{Y0 + 14}" text-anchor="middle" class="tk">先の {t_ahead} 本</text>']
    for i in range(n):
        cls = "up" if cc[i] >= oo[i] else "dn"
        parts.append(f'<line x1="{sx(i):.1f}" x2="{sx(i):.1f}" y1="{sy(hh[i]):.1f}" y2="{sy(ll[i]):.1f}" class="wick"/>')
        top, bot = sy(max(oo[i], cc[i])), sy(min(oo[i], cc[i]))
        parts.append(f'<rect x="{sx(i) - w / 2:.1f}" y="{top:.1f}" width="{w:.1f}" height="{max(bot - top, 1):.1f}" class="{cls}"/>')
    for m, v in levels:
        parts.append(f'<line x1="{sx(k0):.1f}" x2="{X1}" y1="{sy(v):.1f}" y2="{sy(v):.1f}" class="lvl"/>')
        parts.append(f'<text x="{X1 + 6}" y="{sy(v) + 4:.1f}" class="lb">{"+" if m > 0 else "−"}{abs(m)} ATR</text>')
    parts.append(f'<circle cx="{sx(k0):.1f}" cy="{sy(c[t0]):.1f}" r="5" class="pt"/>')
    parts.append(f'<text x="{sx(k0):.1f}" y="{Y1 - 4}" text-anchor="middle" class="lb">起点</text>')
    parts.append(f'<line x1="{sx(k0):.1f}" x2="{sx(k0):.1f}" y1="{sy(c[t0]) + 6:.1f}" y2="{Y1 - 16}" class="grid"/>')
    j = k0 + 1 + int(np.argmax(hh[k0 + 1:] >= c[t0] + a[t0]))
    parts.append(f'<circle cx="{sx(j):.1f}" cy="{sy(c[t0] + a[t0]):.1f}" r="6" class="hit"/>')
    for i in (0, k0):
        parts.append(f'<text x="{sx(i):.1f}" y="{Y1 + 18}" text-anchor="middle" class="tk">{dates[i]:%m-%d %H時}</text>')
    caption = (f"起点 {dates[k0]:%Y-%m-%d %H:%M}（UTC）の終値 {c[t0]:,.1f}、ATR20 = {a[t0]:,.1f}。"
               f"先の {t_ahead} 本で +1 ATR（{c[t0] + a[t0]:,.1f}）には届き、+2 ATR には届きませんでした。")
    return (f'<svg viewBox="0 0 640 300" role="img" aria-label="1 時間足の起点と ATR の倍数の水準、先の 5 本">'
            + "".join(parts) + "</svg>"), caption


def fig_curves(t, tf="1h"):
    """x ごとの届く確率（表 = 線、照合 = 点）。T = 1・5・20、上と下の 2 枚。"""
    X0, X1, Y0, Y1 = 60, 300, 20, 220
    xs = np.array(R.XS)
    sx = lambda x: X0 + (np.log(x) - np.log(0.25)) / (np.log(6) - np.log(0.25)) * (X1 - X0)
    sy = lambda p: Y1 - p * (Y1 - Y0)
    out = []
    for direction in ("up", "down"):
        parts = []
        for p in (0, 0.25, 0.5, 0.75, 1.0):
            parts.append(f'<line x1="{X0}" x2="{X1}" y1="{sy(p):.1f}" y2="{sy(p):.1f}" class="grid"/>')
            parts.append(f'<text x="{X0 - 6}" y="{sy(p) + 4:.1f}" text-anchor="end" class="tk">{p * 100:.0f}%</text>')
        for x in (0.25, 0.5, 1, 2, 4, 6):
            parts.append(f'<text x="{sx(x):.1f}" y="{Y1 + 16}" text-anchor="middle" class="tk">{x:g}</text>')
        parts.append(f'<text x="{(X0 + X1) / 2}" y="{Y1 + 32}" text-anchor="middle" class="tk">距離（ATR の何倍）</text>')
        for T, cls in ((1, "t1"), (5, "t5"), (20, "t20")):
            g = t[(t.tf == tf) & (t["T"] == T) & (t.dir == direction)].sort_values("x")
            parts.append('<path d="M' + " L".join(f"{sx(x):.1f},{sy(p):.1f}" for x, p in zip(g.x, g.is_p))
                         + f'" class="c-{cls}"/>')
            for x, p in zip(g.x, g.oos_p):
                parts.append(f'<rect x="{sx(x) - 3.5:.1f}" y="{sy(p) - 3.5:.1f}" width="7" height="7" class="p-{cls}"/>')
            last = g.iloc[2]
            parts.append(f'<text x="{sx(last.x) + 8:.1f}" y="{sy(last.is_p) - 6:.1f}" class="lb {cls}">{T} 本</text>')
        out.append(f'<figure class="figure small"><figcaption>{DIR_JA[direction]}の水準</figcaption>'
                   f'<svg viewBox="0 0 320 260" role="img" aria-label="{TF_JA[tf]}・{DIR_JA[direction]}の水準に届く確率">'
                   + "".join(parts) + "</svg></figure>")
    return "".join(out)


def fig_verdict(v):
    """時間足 × T × 向きの合否（表の最大誤差）。"""
    tfs = ["15m", "1h", "4h", "1D"]
    cols = [(T, d) for T in (1, 5, 20) for d in ("up", "down")]
    vv = v.set_index(["tf", "T", "dir"])
    X0, cw, rh, Y0 = 90, 88, 40, 50
    W = X0 + cw * len(cols) + 10
    H = Y0 + rh * len(tfs) + 10
    parts = []
    for j, (T, d) in enumerate(cols):
        x = X0 + j * cw + cw / 2
        if d == "up":
            parts.append(f'<text x="{x + cw / 2:.1f}" y="18" text-anchor="middle" class="lb strong">{T} 本以内</text>')
        parts.append(f'<text x="{x:.1f}" y="38" text-anchor="middle" class="tk">{DIR_JA[d]}</text>')
    for i, tf in enumerate(tfs):
        y = Y0 + i * rh
        parts.append(f'<text x="{X0 - 10}" y="{y + rh / 2 + 4:.1f}" text-anchor="end" class="lb">{TF_JA[tf]}</text>')
        for j, (T, d) in enumerate(cols):
            r = vv.loc[(tf, T, d)]
            ok = bool(r.table_pass)
            x = X0 + j * cw
            parts.append(f'<rect x="{x + 3}" y="{y + 3}" width="{cw - 6}" height="{rh - 6}" rx="4" class="{"ok" if ok else "ng"}"/>')
            parts.append(f'<text x="{x + cw / 2:.1f}" y="{y + rh / 2 + 4:.1f}" text-anchor="middle" class="cell {"ok" if ok else "ng"}">'
                         f'{"合格" if ok else "不合格"} {r.table_max_err * 100:.1f}</text>')
    return f'<svg viewBox="0 0 {W} {H}" role="img" aria-label="時間足・地平・向きごとの照合の合否">' + "".join(parts) + "</svg>"


def fig_formula(t, tf="1h", T=5, direction="down"):
    """酔歩の式と実際（照合する期間）の比較。"""
    g = t[(t.tf == tf) & (t["T"] == T) & (t.dir == direction)].sort_values("x")
    X0, X1 = 150, 620
    rh = 26
    H = 30 + rh * len(g) + 40
    sx = lambda p: X0 + p * (X1 - X0)
    parts = []
    for p in (0, 0.25, 0.5, 0.75, 1.0):
        parts.append(f'<line x1="{sx(p):.1f}" x2="{sx(p):.1f}" y1="20" y2="{H - 40}" class="grid"/>')
        parts.append(f'<text x="{sx(p):.1f}" y="{H - 24}" text-anchor="middle" class="tk">{p * 100:.0f}%</text>')
    for i, r in enumerate(g.itertuples()):
        y = 30 + i * rh + rh / 2
        parts.append(f'<text x="{X0 - 10}" y="{y + 4:.1f}" text-anchor="end" class="lb">ATR の {r.x:g} 倍</text>')
        parts.append(f'<line x1="{sx(r.formula_oos):.1f}" x2="{sx(r.oos_p):.1f}" y1="{y:.1f}" y2="{y:.1f}" class="link"/>')
        parts.append(f'<circle cx="{sx(r.formula_oos):.1f}" cy="{y:.1f}" r="5" class="m-f"/>')
        parts.append(f'<rect x="{sx(r.oos_p) - 4.5:.1f}" y="{y - 4.5:.1f}" width="9" height="9" class="m-a"/>')
    parts.append(f'<text x="{(X0 + X1) / 2}" y="{H - 6}" text-anchor="middle" class="tk">届く確率</text>')
    return f'<svg viewBox="0 0 680 {H}" role="img" aria-label="酔歩の式と実際の届く確率">' + "".join(parts) + "</svg>"


def fig_drift(t):
    """日足・20 本以内: 表と照合のずれ（上は増え、下は減る）。"""
    g = t[(t.tf == "1D") & (t["T"] == 20)]
    X0, X1 = 150, 620
    vmin, vmax = -0.12, 0.12
    sx = lambda v: X0 + (v - vmin) / (vmax - vmin) * (X1 - X0)
    xs = list(R.XS)
    rh = 30
    H = 30 + rh * len(xs) + 44
    parts = []
    for v in (-0.10, -0.05, 0, 0.05, 0.10):
        parts.append(f'<line x1="{sx(v):.1f}" x2="{sx(v):.1f}" y1="20" y2="{H - 44}" class="{"zero" if v == 0 else "grid"}"/>')
        parts.append(f'<text x="{sx(v):.1f}" y="{H - 28}" text-anchor="middle" class="tk">{v * 100:+.0f}</text>')
    for v in (-0.03, 0.03):
        parts.append(f'<line x1="{sx(v):.1f}" x2="{sx(v):.1f}" y1="20" y2="{H - 44}" class="tol"/>')
    parts.append(f'<text x="{(X0 + X1) / 2}" y="{H - 8}" text-anchor="middle" class="tk">照合 − 表（ポイント）。破線の間が合格の幅 ±3</text>')
    gg = g.set_index(["dir", "x"])
    for i, x in enumerate(xs):
        y = 30 + i * rh + rh / 2
        parts.append(f'<text x="{X0 - 10}" y="{y + 4:.1f}" text-anchor="end" class="lb">ATR の {x:g} 倍</text>')
        u, d = gg.loc[("up", x), "diff"], gg.loc[("down", x), "diff"]
        parts.append(f'<circle cx="{sx(u):.1f}" cy="{y - 5:.1f}" r="5" class="m-up"/>')
        parts.append(f'<circle cx="{sx(d):.1f}" cy="{y + 5:.1f}" r="5" class="m-dn"/>')
    return f'<svg viewBox="0 0 680 {H}" role="img" aria-label="日足・20 本以内の表と照合のずれ">' + "".join(parts) + "</svg>"


def main(out_path: str):
    t = pd.read_csv(RES / "table.csv")
    v = pd.read_csv(RES / "verdict.csv")
    ex_svg, ex_cap = fig_example()
    data = {f"{r.tf}|{r.T}|{r.dir}": [] for r in v.itertuples()}
    for r in t.sort_values(["tf", "T", "dir", "x"]).itertuples():
        data[f"{r.tf}|{r.T}|{r.dir}"].append([r.x, round(r.is_p * 100, 1), round(r.oos_p * 100, 1), r.oos_n])
    passed = {f"{r.tf}|{r.T}|{r.dir}": bool(r.table_pass) for r in v.itertuples()}
    tpl = (HERE / "page_template.html").read_text(encoding="utf-8")
    page = (tpl.replace("{{FIG_EXAMPLE}}", ex_svg).replace("{{EX_CAP}}", ex_cap)
            .replace("{{FIG_CURVES}}", fig_curves(t))
            .replace("{{FIG_VERDICT}}", fig_verdict(v))
            .replace("{{FIG_FORMULA}}", fig_formula(t))
            .replace("{{FIG_DRIFT}}", fig_drift(t))
            .replace("{{N_PASS}}", str(int(v.table_pass.sum()))).replace("{{N_ALL}}", str(len(v)))
            .replace("{{N_FPASS}}", str(int(v.formula_pass.sum())))
            .replace("{{DATA}}", json.dumps({"rows": data, "pass": passed}, ensure_ascii=False)))
    Path(out_path).write_text(inject_toc(page), encoding="utf-8")
    print("wrote", out_path, len(page))


if __name__ == "__main__":
    main(sys.argv[1])
