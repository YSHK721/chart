"""足の並びの有意性の報告ページ（図は results/ の実データから描く）。

実行: lightweight-charts-python-main/.venv/bin/python -m tools.measure.bar_patterns.make_page <出力 html>
前提: run.py を実行済み（results/tests.csv・omnibus.csv）。
"""
from __future__ import annotations

import html
import math
import sys
from pathlib import Path

import numpy as np
import pandas as pd

from tools.measure.page_toc import inject_toc

HERE = Path(__file__).resolve().parent
RES = HERE / "results"
RES_CD = HERE / "results_CD"
TFS = ("15m", "1h", "4h", "1D")
TF_JA = {"15m": "15 分足", "1h": "1 時間足", "4h": "4 時間足", "1D": "日足"}
COST_BP = None  # omnibus から計算


def esc(s):
    return html.escape(str(s))


def fmt_n(x):
    return f"{int(x):,}"


# ---------- 図 1: 並びの例（ローソク足 3 本＋先の値動き） ----------
def fig_example():
    # 小陰・小陰・小陽 → 1 本後。座標は図解用（実データではない）
    bars = [(100, 92, 104, 90), (92, 86, 95, 83), (86, 90, 93, 84)]  # o, c, h, l
    y = lambda v: 210 - (v - 75) * 4.2
    parts = []
    xs = [110, 170, 230]
    for x, (o, c, h, l) in zip(xs, bars):
        cls = "up" if c > o else "dn"
        parts.append(f'<line x1="{x}" x2="{x}" y1="{y(h):.1f}" y2="{y(l):.1f}" class="wick"/>')
        top, bot = y(max(o, c)), y(min(o, c))
        parts.append(f'<rect x="{x-14}" y="{top:.1f}" width="28" height="{max(bot-top,2):.1f}" class="{cls}"/>')
    labels = ["小陰", "小陰", "小陽"]
    for x, t in zip(xs, labels):
        parts.append(f'<text x="{x}" y="250" text-anchor="middle" class="lb">{t}</text>')
    parts.append(f'<path d="M96,262 L244,262" class="brace"/>')
    parts.append(f'<text x="170" y="282" text-anchor="middle" class="tk">これが 1 つの「並び」（長さ 3）</text>')
    # 確定点と先の値動き
    yc = y(90)
    parts.append(f'<circle cx="244" cy="{yc:.1f}" r="5" class="pt"/>')
    parts.append(f'<line x1="244" x2="470" y1="{yc:.1f}" y2="{yc:.1f}" class="guide"/>')
    parts.append(f'<text x="252" y="{yc-10:.1f}" class="tk">並びが確定した終値</text>')
    yf = y(97)
    parts.append(f'<path d="M244,{yc:.1f} C 320,{yc:.1f} 380,{yf+30:.1f} 450,{yf:.1f}" class="fwd"/>')
    parts.append(f'<circle cx="450" cy="{yf:.1f}" r="5" class="fwdpt"/>')
    parts.append(f'<line x1="470" x2="470" y1="{yc:.1f}" y2="{yf:.1f}" class="arrow" marker-end="url(#ah)"/>')
    parts.append(f'<text x="478" y="{(yc+yf)/2+4:.1f}" class="lb acc">先の値動き</text>')
    parts.append(f'<text x="450" y="{yf-12:.1f}" text-anchor="middle" class="tk">1 本後・4 本後の終値</text>')
    defs = ('<defs><marker id="ah" viewBox="0 0 10 10" refX="8" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse">'
            '<path d="M0,0 L10,5 L0,10 z" class="ahead"/></marker></defs>')
    return (f'<svg viewBox="0 0 600 300" role="img" aria-label="小陰・小陰・小陽の並びと、その後の値動きの例">{defs}'
            + "".join(parts) + "</svg>")


# ---------- 図 2: 円環ずらし ----------
def fig_shift():
    """本物の組み合わせと、値動きの行を右へ 2 つずらした組み合わせ（説明用の数字）。"""
    pat = ["A", "B", "A", "C", "B", "A", "C", "B"]
    fwd = [3, -1, 2, 0, -2, 4, 1, -1]
    s = 2
    shifted = fwd[-s:] + fwd[:-s]
    x0, w, ch = 150, 50, 28
    parts = []
    sign = lambda v: f"{v:+d}" if v else "0"

    def cells(yy, items, hl_cls, base_cls, fmt=str):
        for i, t in enumerate(items):
            cls = hl_cls if pat[i] == "A" else base_cls
            parts.append(f'<rect x="{x0+i*w}" y="{yy}" width="{w-6}" height="{ch}" rx="3" class="{cls}"/>')
            parts.append(f'<text x="{x0+i*w+(w-6)/2}" y="{yy+19}" text-anchor="middle" class="cell">{fmt(t)}</text>')

    def table(y0, title, vals, avg_vals, val_label):
        parts.append(f'<text x="20" y="{y0}" class="lb strong">{title}</text>')
        for i in range(8):
            parts.append(f'<text x="{x0+i*w+(w-6)/2}" y="{y0+24}" text-anchor="middle" class="tk">{i+1}</text>')
        parts.append(f'<text x="{x0-10}" y="{y0+24}" text-anchor="end" class="tk">足</text>')
        parts.append(f'<text x="{x0-10}" y="{y0+52}" text-anchor="end" class="lb">並び</text>')
        cells(y0 + 33, pat, "c-hl", "c-real")
        parts.append(f'<text x="{x0-10}" y="{y0+86}" text-anchor="end" class="lb">{val_label}</text>')
        cells(y0 + 67, vals, "c-hl", "c-real", sign)
        avg = sum(avg_vals) / len(avg_vals)
        parts.append(f'<text x="{x0+8*w+10}" y="{y0+60}" class="tk">A の後の値動き</text>')
        parts.append(f'<text x="{x0+8*w+10}" y="{y0+78}" class="tk">{"・".join(sign(v) for v in avg_vals)}</text>')
        parts.append(f'<text x="{x0+8*w+10}" y="{y0+100}" class="lb acc">平均 {avg:+.1f}</text>')

    a_idx = [i for i, p in enumerate(pat) if p == "A"]
    table(24, "本物の組み合わせ", fwd, [fwd[i] for i in a_idx], "次の値動き")
    # ずらす矢印（右へ 2 つ・あふれた分は左端へ）
    ya = 138
    for i in range(8):
        j = (i + s) % 8
        xa = x0 + i * w + (w - 6) / 2
        xb = x0 + j * w + (w - 6) / 2
        if j > i:
            parts.append(f'<path d="M{xa},{ya} C {xa},{ya+16} {xb},{ya+10} {xb},{ya+26}" class="shiftline" marker-end="url(#sh)"/>')
    parts.append(f'<path d="M{x0+6*w+22},{ya} C {x0+6*w+22},{ya+40} {x0+22},{ya-6} {x0+22},{ya+26}" class="wrapline" marker-end="url(#sh)"/>')
    parts.append(f'<text x="{x0+8*w+10}" y="{ya+18}" class="lb">値動きの行だけを右へ 2 つずらす</text>')
    parts.append(f'<text x="{x0+8*w+10}" y="{ya+34}" class="tk">右端からあふれた 2 つは左端へ回す</text>')
    table(196, "でたらめな組み合わせ（ずらした後）", shifted, [shifted[i] for i in a_idx], "次の値動き")
    parts.append(f'<text x="20" y="338" class="tk">並びの行は動かさない。値動きの行の中身と順番も変わらない。変わるのは「どの並びの後にどの値動きが来たか」だけ。</text>')
    parts.append(f'<text x="20" y="358" class="tk">ずらす幅を変えて 999 通り作り、本物の偏り（+3.0）が 999 通りのほとんどより大きければ「偶然ではない」と判定する。</text>')
    defs = ('<defs><marker id="sh" viewBox="0 0 10 10" refX="8" refY="5" markerWidth="6" markerHeight="6" orient="auto-start-reverse">'
            '<path d="M0,0 L10,5 L0,10 z" class="ahead"/></marker></defs>')
    return ('<svg viewBox="0 0 760 372" role="img" aria-label="本物の組み合わせと、値動きの行を右へ 2 つずらしたでたらめな組み合わせの比較。並び A の後の平均は +3.0 から +1.3 に変わる">'
            + defs + "".join(parts) + "</svg>")


# ---------- 図 3: 全並びの |t| と通過線 ----------
def fig_strip(tests, omni):
    is_ = tests[(tests.period == "IS") & tests.tested]
    om = omni[omni.period == "IS"].set_index(["tf", "h"])
    rows = [(tf, h) for tf in TFS for h in (1, 4)]
    X0, X1 = 150, 640
    xmax = 5.5
    sx = lambda v: X0 + v / xmax * (X1 - X0)
    rh = 34
    H = 30 + rh * len(rows) + 40
    parts = []
    for v in range(0, 6):
        parts.append(f'<line x1="{sx(v):.1f}" x2="{sx(v):.1f}" y1="20" y2="{H-40}" class="grid"/>')
        parts.append(f'<text x="{sx(v):.1f}" y="{H-24}" text-anchor="middle" class="tk">{v}</text>')
    parts.append(f'<text x="{(X0+X1)/2}" y="{H-6}" text-anchor="middle" class="tk">|t|（大きいほど、並びの後の値動きが平均から外れている）</text>')
    rng = np.random.default_rng(1)
    for i, (tf, h) in enumerate(rows):
        yy = 30 + i * rh + rh / 2
        parts.append(f'<text x="{X0-10}" y="{yy+4:.1f}" text-anchor="end" class="lb">{TF_JA[tf]}・{h} 本後</text>')
        g = is_[(is_.tf == tf) & (is_.h == h)]
        jit = rng.uniform(-9, 9, size=len(g))
        for v, j in zip(np.abs(g.t.to_numpy()), jit):
            parts.append(f'<circle cx="{sx(min(v, xmax)):.1f}" cy="{yy+j:.1f}" r="2.6" class="dot"/>')
        thr = om.loc[(tf, h), "t_pass"]
        parts.append(f'<line x1="{sx(thr):.1f}" x2="{sx(thr):.1f}" y1="{yy-14:.1f}" y2="{yy+14:.1f}" class="thr"/>')
    parts.append(f'<text x="{sx(4.3):.1f}" y="16" text-anchor="middle" class="lb red">通過線</text>')
    return (f'<svg viewBox="0 0 680 {H}" role="img" aria-label="選ぶ期間の全並びの |t| と通過線。通過線を越えた点は無い">'
            + "".join(parts) + "</svg>")


# ---------- 図 4: 全体検定の p ----------
def fig_omni(omni, aria="全体検定の p。選ぶ期間はすべて 0.05 より右"):
    rows = [(tf, h) for tf in TFS for h in (1, 4)]
    X0, X1 = 150, 620
    lo, hi = math.log10(0.001), math.log10(1.0)  # p の最小値は 1/1000
    sx = lambda p: X0 + (math.log10(p) - lo) / (hi - lo) * (X1 - X0)
    rh = 30
    H = 40 + rh * len(rows) + 40
    parts = []
    for p in (0.001, 0.01, 0.05, 0.1, 0.5, 1.0):
        parts.append(f'<line x1="{sx(p):.1f}" x2="{sx(p):.1f}" y1="28" y2="{H-40}" class="{"thr" if p == 0.05 else "grid"}"/>')
        parts.append(f'<text x="{sx(p):.1f}" y="{H-24}" text-anchor="middle" class="tk">{p:g}</text>')
    parts.append(f'<text x="{sx(0.05):.1f}" y="20" text-anchor="middle" class="lb red">0.05</text>')
    parts.append(f'<text x="{(X0+X1)/2}" y="{H-6}" text-anchor="middle" class="tk">p（左へ行くほど「偶然では説明しにくい」・対数目盛）</text>')
    om = omni.set_index(["period", "tf", "h"])
    for i, (tf, h) in enumerate(rows):
        yy = 40 + i * rh + rh / 2
        parts.append(f'<text x="{X0-10}" y="{yy+4:.1f}" text-anchor="end" class="lb">{TF_JA[tf]}・{h} 本後</text>')
        pi, po = om.loc[("IS", tf, h), "p"], om.loc[("OOS", tf, h), "p"]
        parts.append(f'<line x1="{sx(pi):.1f}" x2="{sx(po):.1f}" y1="{yy:.1f}" y2="{yy:.1f}" class="link"/>')
        parts.append(f'<circle cx="{sx(pi):.1f}" cy="{yy:.1f}" r="6" class="m-is"/>')
        parts.append(f'<rect x="{sx(po)-5:.1f}" y="{yy-5:.1f}" width="10" height="10" class="m-oos"/>')
    return (f'<svg viewBox="0 0 680 {H}" role="img" aria-label="{aria}">'
            + "".join(parts) + "</svg>")


# ---------- 図 5: 上位 5 件の IS → OOS ----------
def fig_top(tests):
    key = ["tf", "h", "family", "k", "pattern"]
    i = tests[(tests.period == "IS") & tests.tested].nsmallest(5, "p_fw")
    o = tests[tests.period == "OOS"][key + ["n", "excess_bp", "t"]]
    m = i.merge(o, on=key, suffixes=("_is", "_oos"))
    X0, X1 = 250, 640
    vmin, vmax = -10, 25
    sx = lambda v: X0 + (v - vmin) / (vmax - vmin) * (X1 - X0)
    rh = 40
    H = 30 + rh * len(m) + 44
    parts = []
    for v in range(-10, 26, 5):
        parts.append(f'<line x1="{sx(v):.1f}" x2="{sx(v):.1f}" y1="20" y2="{H-44}" class="{"zero" if v == 0 else "grid"}"/>')
        parts.append(f'<text x="{sx(v):.1f}" y="{H-28}" text-anchor="middle" class="tk">{v:+d}</text>')
    parts.append(f'<text x="{(X0+X1)/2}" y="{H-8}" text-anchor="middle" class="tk">平均との差（bp・1 bp = 0.01%）</text>')
    for j, r in enumerate(m.itertuples()):
        yy = 30 + j * rh + rh / 2
        parts.append(f'<text x="{X0-12}" y="{yy-2:.1f}" text-anchor="end" class="lb">{esc(r.pattern)}</text>')
        parts.append(f'<text x="{X0-12}" y="{yy+13:.1f}" text-anchor="end" class="tk">{TF_JA[r.tf]}・{r.h} 本後</text>')
        a, b = sx(r.excess_bp_is), sx(r.excess_bp_oos)
        parts.append(f'<line x1="{a:.1f}" x2="{b:.1f}" y1="{yy:.1f}" y2="{yy:.1f}" class="link"/>')
        parts.append(f'<circle cx="{a:.1f}" cy="{yy:.1f}" r="6" class="m-is"/>')
        parts.append(f'<rect x="{b-5:.1f}" y="{yy-5:.1f}" width="10" height="10" class="m-oos"/>')
    return (f'<svg viewBox="0 0 680 {H}" role="img" aria-label="選ぶ期間の上位 5 件が、確かめる期間で 0 付近に戻る">'
            + "".join(parts) + "</svg>"), m


# ---------- 図 6: 検出できる大きさ vs 費用 ----------
def fig_power(tests, omni, cost_bp):
    is_ = tests[(tests.period == "IS") & tests.tested].merge(
        omni[omni.period == "IS"][["tf", "h", "t_pass"]], on=["tf", "h"])
    is_["det"] = is_.t_pass * is_.se_bp.abs()
    X0, X1 = 150, 620
    lo, hi = math.log10(0.1), math.log10(300)
    sx = lambda v: X0 + (math.log10(v) - lo) / (hi - lo) * (X1 - X0)
    rows = [(tf, h) for tf in TFS for h in (1, 4)]
    rh = 30
    H = 40 + rh * len(rows) + 40
    parts = []
    for v in (0.1, 1, 10, 100):
        parts.append(f'<line x1="{sx(v):.1f}" x2="{sx(v):.1f}" y1="28" y2="{H-40}" class="grid"/>')
        parts.append(f'<text x="{sx(v):.1f}" y="{H-24}" text-anchor="middle" class="tk">{v:g}</text>')
    parts.append(f'<line x1="{sx(cost_bp):.1f}" x2="{sx(cost_bp):.1f}" y1="28" y2="{H-40}" class="cost"/>')
    parts.append(f'<text x="{sx(cost_bp):.1f}" y="20" text-anchor="middle" class="lb amb">費用 {cost_bp:.0f} bp</text>')
    parts.append(f'<text x="{(X0+X1)/2}" y="{H-6}" text-anchor="middle" class="tk">検出できた最小の差（bp・対数目盛）</text>')
    for i, (tf, h) in enumerate(rows):
        yy = 40 + i * rh + rh / 2
        g = is_[(is_.tf == tf) & (is_.h == h)].det
        a, b, md = g.quantile(0.1), g.quantile(0.9), g.median()
        parts.append(f'<text x="{X0-10}" y="{yy+4:.1f}" text-anchor="end" class="lb">{TF_JA[tf]}・{h} 本後</text>')
        parts.append(f'<rect x="{sx(a):.1f}" y="{yy-6:.1f}" width="{sx(b)-sx(a):.1f}" height="12" rx="3" class="band"/>')
        parts.append(f'<line x1="{sx(md):.1f}" x2="{sx(md):.1f}" y1="{yy-9:.1f}" y2="{yy+9:.1f}" class="med"/>')
    return (f'<svg viewBox="0 0 680 {H}" role="img" aria-label="時間足ごとの検出できた最小の差と費用の比較">'
            + "".join(parts) + "</svg>")


# ---------- 図 7: ひげの 4 種類 ----------
def fig_wicks():
    shapes = [("上短下短", 4, 4), ("上短下長", 4, 30), ("上長下短", 30, 4), ("上長下長", 30, 30)]
    parts = []
    for i, (name, up, lo) in enumerate(shapes):
        x = 80 + i * 150
        top, bot = 70, 110
        parts.append(f'<line x1="{x}" x2="{x}" y1="{top-up}" y2="{bot+lo}" class="wick"/>')
        parts.append(f'<rect x="{x-14}" y="{top}" width="28" height="{bot-top}" class="up"/>')
        parts.append(f'<text x="{x}" y="176" text-anchor="middle" class="lb">{name}</text>')
    parts.append('<text x="20" y="196" class="tk">ひげの長短は、その足より前の 100 本の同じひげの中央値と比べて決める（図は陽線の例）。</text>')
    return ('<svg viewBox="0 0 640 206" role="img" aria-label="ひげの形の 4 種類">' + "".join(parts) + "</svg>")


# ---------- 図 8: ひげの効果と費用 ----------
def fig_wick_cost(tests_cd, cost_bp):
    want = [("C", 1, "陰・上長下短"), ("C", 1, "陽・上長下短"), ("C", 1, "陽・上短下長"), ("D", 1, "大陽・上短下長")]
    t = tests_cd[(tests_cd.tf == "15m") & (tests_cd.h == 1)].set_index(["family", "k", "pattern", "period"])
    X0, X1 = 200, 640
    vmax = 16
    sx = lambda v: X0 + v / vmax * (X1 - X0)
    rh = 30
    rows = [(f"{p}", t.loc[(f, k, p, "OOS"), "excess_bp"]) for f, k, p in want]
    H = 30 + rh * (len(rows) + 1) + 40
    parts = []
    for v in range(0, 17, 4):
        parts.append(f'<line x1="{sx(v):.1f}" x2="{sx(v):.1f}" y1="20" y2="{H-40}" class="grid"/>')
        parts.append(f'<text x="{sx(v):.1f}" y="{H-24}" text-anchor="middle" class="tk">{v}</text>')
    parts.append(f'<text x="{(X0+X1)/2}" y="{H-6}" text-anchor="middle" class="tk">大きさ（bp）</text>')
    for i, (name, v) in enumerate(rows):
        yy = 30 + i * rh + rh / 2
        parts.append(f'<text x="{X0-10}" y="{yy+4:.1f}" text-anchor="end" class="lb">{esc(name)}（{v:+.2f}）</text>')
        parts.append(f'<rect x="{X0}" y="{yy-7:.1f}" width="{max(sx(abs(v))-X0, 1.5):.1f}" height="14" class="m-is"/>')
    yy = 30 + len(rows) * rh + rh / 2
    parts.append(f'<text x="{X0-10}" y="{yy+4:.1f}" text-anchor="end" class="lb amb">費用</text>')
    parts.append(f'<rect x="{X0}" y="{yy-7:.1f}" width="{sx(cost_bp)-X0:.1f}" height="14" class="m-cost"/>')
    parts.append(f'<text x="{sx(cost_bp)+6:.1f}" y="{yy+4:.1f}" class="lb amb">{cost_bp:.1f}</text>')
    return (f'<svg viewBox="0 0 680 {H}" role="img" aria-label="ひげの形による次の足の戻りは費用に比べてごく小さい">'
            + "".join(parts) + "</svg>")


def main(out_path: str):
    tests = pd.read_csv(RES / "tests.csv")
    omni = pd.read_csv(RES / "omnibus.csv")
    oos_close = omni[omni.period == "OOS"].mean_close.iloc[0]
    cost_bp = 50 / oos_close * 1e4
    n_is = int(((tests.period == "IS") & tests.tested).sum())
    passed = int(((tests.period == "IS") & tests.tested & (tests.p_fw <= 0.05 / 8)).sum())
    top_svg, top = fig_top(tests)
    om = omni.set_index(["period", "tf", "h"])
    omni_rows = "".join(
        f'<tr><td>{TF_JA[tf]}</td><td class="n">{h}</td><td class="n">{fmt_n(om.loc[("IS",tf,h),"bars"])}</td>'
        f'<td class="n">{fmt_n(om.loc[("OOS",tf,h),"bars"])}</td><td class="n">{om.loc[("IS",tf,h),"p"]:.3f}</td>'
        f'<td class="n">{om.loc[("OOS",tf,h),"p"]:.3f}</td><td class="n">{om.loc[("IS",tf,h),"t_pass"]:.2f}</td></tr>'
        for tf in TFS for h in (1, 4))
    top_rows = "".join(
        f'<tr><td>{TF_JA[r.tf]}</td><td class="n">{r.h}</td><td>{esc(r.pattern)}</td><td class="n">{fmt_n(r.n_is)}</td>'
        f'<td class="n">{r.excess_bp_is:+.1f}</td><td class="n">{r.t_is:.2f}</td><td class="n">{r.p_fw:.3f}</td>'
        f'<td class="n">{fmt_n(r.n_oos)}</td><td class="n">{r.excess_bp_oos:+.1f}</td><td class="n">{r.t_oos:.2f}</td></tr>'
        for r in top.itertuples())
    tests_cd = pd.read_csv(RES_CD / "tests.csv")
    omni_cd = pd.read_csv(RES_CD / "omnibus.csv")
    n_cd = int(((tests_cd.period == "IS") & tests_cd.tested).sum())
    sel_cd = pd.read_csv(RES_CD / "selected.csv")
    cd_rows = "".join(
        f'<tr><td>{TF_JA[r.tf]}</td><td class="n">{r.h}</td><td>{esc(r.pattern)}</td><td class="n">{r.is_excess_bp:+.1f}</td>'
        f'<td class="n">{r.is_t:.2f}</td><td class="n">{r.is_p_fw:.3f}</td><td class="n">{r.oos_excess_bp:+.1f}</td>'
        f'<td class="n">{r.oos_t:.2f}</td><td class="n">{r.oos_holm:.3f}</td></tr>' for r in sel_cd.itertuples())
    tpl = (HERE / "page_template.html").read_text(encoding="utf-8")
    page = (tpl.replace("{{FIG_EXAMPLE}}", fig_example())
            .replace("{{FIG_SHIFT}}", fig_shift())
            .replace("{{FIG_STRIP}}", fig_strip(tests, omni))
            .replace("{{FIG_OMNI}}", fig_omni(omni))
            .replace("{{FIG_TOP}}", top_svg)
            .replace("{{FIG_POWER}}", fig_power(tests, omni, cost_bp))
            .replace("{{N_IS}}", fmt_n(n_is)).replace("{{PASSED}}", str(passed))
            .replace("{{COST_BP}}", f"{cost_bp:.0f}").replace("{{OOS_CLOSE}}", fmt_n(oos_close))
            .replace("{{OMNI_ROWS}}", omni_rows).replace("{{TOP_ROWS}}", top_rows)
            .replace("{{FIG_WICKS}}", fig_wicks())
            .replace("{{FIG_OMNI_CD}}", fig_omni(omni_cd, "ひげを入れた並びの全体検定の p。15 分足と 1 時間足の 1 本後は両期間で小さい"))
            .replace("{{FIG_WICK_COST}}", fig_wick_cost(tests_cd, cost_bp))
            .replace("{{N_CD}}", fmt_n(n_cd)).replace("{{CD_ROWS}}", cd_rows))
    Path(out_path).write_text(inject_toc(page), encoding="utf-8")
    print("wrote", out_path, len(page), "bytes")


if __name__ == "__main__":
    main(sys.argv[1])
