"""1 分足 60EMA 安値・高値の線の報告ページ（図は実データと results/ から描く）。

実行: lightweight-charts-python-main/.venv/bin/python -m tools.measure.ema_hl.make_page <出力 html>
前提: run.py を実行済み（results/tests.csv・means.csv）。
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

from tools.measure.page_toc import inject_toc
from tools.measure.ema_hl import ema_hl as E

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
RES = HERE / "results"
MEAS_JA = {"pnl5": "5 本後の損益", "pnl15": "15 本後の損益", "pnl60": "60 本後の損益", "hold": "持ちこたえ"}
SIDE_JA = {"lower": "下の線（買い）", "upper": "上の線（売り）"}
SHIFT_JA = {-0.0010: "−0.10%", -0.0005: "−0.05%", 0.0: "本物", 0.0005: "+0.05%", 0.0010: "+0.10%"}


def snippet(n_before=70, n_after=40):
    """MT5 1 分足の最後の方から、下の線に触れた場面を 1 つ切り出す（線は系列の先頭から計算）。"""
    d = pd.read_csv(ROOT / "data/marketdata/jp225_mt5_m1.csv", usecols=["date", "open", "high", "low", "close"],
                    parse_dates=["date"]).iloc[:-1].reset_index(drop=True)
    o, h, l, c = (d[k].to_numpy(float) for k in ("open", "high", "low", "close"))
    L, U = E.ema(l), E.ema(h)
    Lp = np.concatenate([[np.nan], L[:-1]])
    Up = np.concatenate([[np.nan], U[:-1]])
    lo_t = E.touches(o, h, l, Lp, "lower")
    up_t = E.touches(o, h, l, Up, "upper")
    minutes = (d.date.astype("int64") // 60_000_000_000).to_numpy()
    # 窓の中がほぼ連続した 1 分足で、上下どちらの線にも触れている最後の場面
    for i in lo_t[::-1]:
        a, b = i - n_before, i + n_after
        if b >= len(d) or minutes[b] - minutes[a] > b - a + 5:  # 欠けは 5 分まで
            continue
        if ((up_t > a) & (up_t < b)).any():
            sl = slice(a, b)
            return dict(date=d.date[a:b].reset_index(drop=True), o=o[sl], h=h[sl], l=l[sl], c=c[sl],
                        L=L[sl], U=U[sl], Lp=Lp[sl], Up=Up[sl],
                        lo=[k - a for k in lo_t if a <= k < b], up=[k - a for k in up_t if a <= k < b])
    raise RuntimeError("場面が見つかりません")


def fig_chart(s, fakes=False):
    n = len(s["c"])
    X0, X1, Y0, Y1 = 70, 600, 20, 270
    vals = np.concatenate([s["h"], s["l"], s["U"], s["L"]])
    if fakes:
        vals = np.concatenate([vals, s["L"] * 1.001, s["L"] * 0.999])
    vmin, vmax = vals.min(), vals.max()
    pad = (vmax - vmin) * 0.05
    vmin, vmax = vmin - pad, vmax + pad
    sx = lambda i: X0 + (i + 0.5) / n * (X1 - X0)
    sy = lambda v: Y1 - (v - vmin) / (vmax - vmin) * (Y1 - Y0)
    w = (X1 - X0) / n * 0.6
    parts = []
    for v in np.linspace(vmin + pad, vmax - pad, 4):
        parts.append(f'<line x1="{X0}" x2="{X1}" y1="{sy(v):.1f}" y2="{sy(v):.1f}" class="grid"/>')
        parts.append(f'<text x="{X0-6}" y="{sy(v)+4:.1f}" text-anchor="end" class="tk">{v:,.0f}</text>')
    for i in (0, n // 2, n - 1):
        parts.append(f'<text x="{sx(i):.1f}" y="{Y1+18}" text-anchor="middle" class="tk">{s["date"][i]:%m-%d %H:%M}</text>')
    for i in range(n):
        cls = "up" if s["c"][i] >= s["o"][i] else "dn"
        parts.append(f'<line x1="{sx(i):.1f}" x2="{sx(i):.1f}" y1="{sy(s["h"][i]):.1f}" y2="{sy(s["l"][i]):.1f}" class="wick"/>')
        top, bot = sy(max(s["o"][i], s["c"][i])), sy(min(s["o"][i], s["c"][i]))
        parts.append(f'<rect x="{sx(i)-w/2:.1f}" y="{top:.1f}" width="{w:.1f}" height="{max(bot-top, 1):.1f}" class="{cls}"/>')
    path = lambda arr: "M" + " L".join(f"{sx(i):.1f},{sy(v):.1f}" for i, v in enumerate(arr))
    if fakes:
        for f in (-0.0010, -0.0005, 0.0005, 0.0010):
            parts.append(f'<path d="{path(s["L"] * (1 + f))}" class="l-fake"/>')
            parts.append(f'<text x="{X1+6}" y="{sy(s["L"][-1]*(1+f))+4:.1f}" class="tk">{SHIFT_JA[f]}</text>')
        parts.append(f'<path d="{path(s["L"])}" class="l-low"/>')
        parts.append(f'<text x="{X1+6}" y="{sy(s["L"][-1])+4:.1f}" class="lb c-low">本物</text>')
    else:
        parts.append(f'<path d="{path(s["U"])}" class="l-up"/>')
        parts.append(f'<path d="{path(s["L"])}" class="l-low"/>')
        parts.append(f'<text x="{X1+6}" y="{sy(s["U"][-1])+4:.1f}" class="lb c-up">上の線</text>')
        parts.append(f'<text x="{X1+6}" y="{sy(s["L"][-1])+4:.1f}" class="lb c-low">下の線</text>')
        for k in s["lo"]:
            parts.append(f'<circle cx="{sx(k):.1f}" cy="{sy(s["Lp"][k]):.1f}" r="4.5" class="m-buy"/>')
        for k in s["up"]:
            parts.append(f'<circle cx="{sx(k):.1f}" cy="{sy(s["Up"][k]):.1f}" r="4.5" class="m-sell"/>')
    aria = "1 分足と、下の線をずらした偽の線 4 本" if fakes else "1 分足と、安値の 60EMA・高値の 60EMA、触れた点"
    return f'<svg viewBox="0 0 680 300" role="img" aria-label="{aria}">' + "".join(parts) + "</svg>"


def fig_d(t):
    rows = [(side, k) for side in ("lower", "upper") for k in ("pnl5", "pnl15", "pnl60")]
    X0, X1 = 230, 640
    vmin, vmax = -0.5, 0.5
    sx = lambda v: X0 + (min(max(v, vmin), vmax) - vmin) / (vmax - vmin) * (X1 - X0)
    rh = 44
    H = 30 + rh * len(rows) + 44
    parts = []
    for v in np.arange(-0.5, 0.51, 0.25):
        parts.append(f'<line x1="{sx(v):.1f}" x2="{sx(v):.1f}" y1="20" y2="{H-44}" class="{"zero" if abs(v) < 1e-9 else "grid"}"/>')
        parts.append(f'<text x="{sx(v):.1f}" y="{H-28}" text-anchor="middle" class="tk">{v:+.2f}</text>')
    parts.append(f'<text x="{(X0+X1)/2}" y="{H-8}" text-anchor="middle" class="tk">D ＝ 本物 − 偽の線（bp）。右ほど本物の線で有利</text>')
    tt = t.set_index(["period", "side", "measure"])
    for j, (side, k) in enumerate(rows):
        yy = 30 + j * rh + rh / 2
        parts.append(f'<text x="{X0-12}" y="{yy-2:.1f}" text-anchor="end" class="lb">{SIDE_JA[side]}</text>')
        parts.append(f'<text x="{X0-12}" y="{yy+13:.1f}" text-anchor="end" class="tk">{MEAS_JA[k]}</text>')
        for period, dy, cls in (("IS", -7, "is"), ("OOS", 7, "oos")):
            r = tt.loc[(period, side, k)]
            parts.append(f'<line x1="{sx(r.ci_lo):.1f}" x2="{sx(r.ci_hi):.1f}" y1="{yy+dy:.1f}" y2="{yy+dy:.1f}" class="ci-{cls}"/>')
            if cls == "is":
                parts.append(f'<circle cx="{sx(r.D):.1f}" cy="{yy+dy:.1f}" r="5" class="m-is"/>')
            else:
                parts.append(f'<rect x="{sx(r.D)-4.5:.1f}" y="{yy+dy-4.5:.1f}" width="9" height="9" class="m-oos"/>')
    return f'<svg viewBox="0 0 680 {H}" role="img" aria-label="線ごとの差 D と 95% 区間。確かめる期間ではすべて 0 をまたぐ">' + "".join(parts) + "</svg>"


def fig_profile(m, side, measure, ylab, scale=1.0, fmt="{:+.2f}"):
    shifts = (-0.0010, -0.0005, 0.0, 0.0005, 0.0010)
    names = {s: ("real" if s == 0 else f"fake{s:+.2%}") for s in shifts}
    X0, X1, Y0, Y1 = 80, 600, 24, 210
    sx = lambda i: X0 + i / 4 * (X1 - X0)
    mm = m[(m.side == side) & (m.measure == measure)].set_index(["period", "line"])["mean"] * scale
    vals = mm.to_numpy()
    lo, hi = vals.min(), vals.max()
    pad = (hi - lo) * 0.15
    lo, hi = lo - pad, hi + pad
    sy = lambda v: Y1 - (v - lo) / (hi - lo) * (Y1 - Y0)
    parts = []
    for v in np.linspace(lo + pad, hi - pad, 4):
        parts.append(f'<line x1="{X0}" x2="{X1}" y1="{sy(v):.1f}" y2="{sy(v):.1f}" class="grid"/>')
        label = fmt.format(v).replace("-0.00", "0.00").replace("+0.00", "0.00")
        parts.append(f'<text x="{X0-8}" y="{sy(v)+4:.1f}" text-anchor="end" class="tk">{label}</text>')
    for i, s in enumerate(shifts):
        parts.append(f'<text x="{sx(i):.1f}" y="{Y1+20}" text-anchor="middle" class="{"lb strong" if s == 0 else "tk"}">{SHIFT_JA[s]}</text>')
    parts.append(f'<text x="{(X0+X1)/2}" y="{Y1+40}" text-anchor="middle" class="tk">線の位置（本物の線からのずらし幅）</text>')
    parts.append(f'<text x="14" y="16" class="tk">{ylab}</text>')
    for period, cls in (("IS", "is"), ("OOS", "oos")):
        pts = [(sx(i), sy(mm.loc[(period, names[s])])) for i, s in enumerate(shifts)]
        parts.append('<path d="M' + " L".join(f"{x:.1f},{y:.1f}" for x, y in pts) + f'" class="pl-{cls}"/>')
        for i, (x, y) in enumerate(pts):
            if cls == "is":
                parts.append(f'<circle cx="{x:.1f}" cy="{y:.1f}" r="{6 if i == 2 else 4}" class="m-is"/>')
            else:
                r = 5.5 if i == 2 else 4
                parts.append(f'<rect x="{x-r:.1f}" y="{y-r:.1f}" width="{2*r}" height="{2*r}" class="m-oos"/>')
    return f'<svg viewBox="0 0 680 260" role="img" aria-label="線の位置ごとの{ylab}">' + "".join(parts) + "</svg>"


def main(out_path: str):
    t = pd.read_csv(RES / "tests.csv")
    m = pd.read_csv(RES / "means.csv")
    s = snippet()
    oos_close = float(m[(m.period == "OOS") & (m.measure == "mean_close")]["mean"].iloc[0])
    cost = 50 / oos_close * 1e4
    tt = t.set_index(["period", "side", "measure"])
    rows = "".join(
        f'<tr><td>{p}</td><td>{SIDE_JA[sd]}</td><td>{MEAS_JA[k]}</td><td class="n">{int(r.n_real):,}</td>'
        + (f'<td class="n">{r.real*100:.1f}%</td><td class="n">{r.fake*100:.1f}%</td><td class="n">{r.D*100:+.1f} pt</td>'
           if k == "hold" else
           f'<td class="n">{r.real:+.2f}</td><td class="n">{r.fake:+.2f}</td><td class="n">{r.D:+.2f}</td>')
        + f'<td class="n">{r.p_up:.4f}</td></tr>'
        for (p, sd, k), r in tt.iterrows())
    tpl = (HERE / "page_template.html").read_text(encoding="utf-8")
    page = (tpl.replace("{{FIG_CHART}}", fig_chart(s))
            .replace("{{FIG_FAKES}}", fig_chart(s, fakes=True))
            .replace("{{FIG_D}}", fig_d(t))
            .replace("{{FIG_PNL}}", fig_profile(m, "lower", "pnl5", "5 本後の損益の平均（bp）"))
            .replace("{{FIG_HOLD}}", fig_profile(m, "lower", "hold", "持ちこたえた割合（%）", 100, "{:.0f}"))
            .replace("{{SNIP_DAY}}", f'{s["date"][0]:%Y-%m-%d}')
            .replace("{{COST}}", f"{cost:.1f}").replace("{{OOS_CLOSE}}", f"{oos_close:,.0f}")
            .replace("{{ROWS}}", rows))
    Path(out_path).write_text(inject_toc(page), encoding="utf-8")
    print("wrote", out_path, len(page))


if __name__ == "__main__":
    main(sys.argv[1])
