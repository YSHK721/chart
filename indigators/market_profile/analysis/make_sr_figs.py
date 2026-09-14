"""Step10 の図（out/sr_study.json → out/sr_report.html・純 SVG）。

matplotlib を導入せず（依存追加は承認事項）、標準ライブラリのみでインライン SVG を組む。
"""

from __future__ import annotations

import json
from pathlib import Path

_OUT = Path(__file__).resolve().parent / "out"

C_REAL = "#e4572e"   # 本物（高 z バー）
C_CTRL = "#4c9be8"   # 整合偽水準（placebo）
C_FAKE = "#9aa0a6"   # 低 z 水準
C_AX = "#9aa0a6"
C_TXT = "#e8eaed"
C_GRID = "#2f3438"
C_ZERO = "#79808a"
FONT = ("-apple-system,BlinkMacSystemFont,'Segoe UI','Hiragino Sans',"
        "'Noto Sans JP',Meiryo,sans-serif")


def esc(s: str) -> str:
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


class Svg:
    def __init__(self, w: int, h: int, top=64, right=170, bottom=58, left=250):
        self.w, self.h = w, h
        self.t, self.r, self.b, self.l = top, right, bottom, left
        self.parts: "list[str]" = []

    x0 = property(lambda s: s.l)
    x1 = property(lambda s: s.w - s.r)
    y0 = property(lambda s: s.t)
    y1 = property(lambda s: s.h - s.b)

    def add(self, s): self.parts.append(s)

    def line(self, x1, y1, x2, y2, c=C_AX, w=1, dash=None):
        d = f' stroke-dasharray="{dash}"' if dash else ""
        self.add(f'<line x1="{x1:.1f}" y1="{y1:.1f}" x2="{x2:.1f}" y2="{y2:.1f}" '
                 f'stroke="{c}" stroke-width="{w}"{d}/>')

    def rect(self, x, y, w, h, c, op=1.0, rx=0):
        self.add(f'<rect x="{x:.1f}" y="{y:.1f}" width="{max(w,0):.1f}" '
                 f'height="{max(h,0):.1f}" rx="{rx}" fill="{c}" opacity="{op}"/>')

    def text(self, x, y, s, size=12, c=C_TXT, anchor="start", weight="400", rot=None):
        tr = f' transform="rotate({rot} {x:.1f} {y:.1f})"' if rot else ""
        self.add(f'<text x="{x:.1f}" y="{y:.1f}" font-size="{size}" fill="{c}" '
                 f'text-anchor="{anchor}" font-weight="{weight}" '
                 f'font-family="{FONT}"{tr}>{esc(s)}</text>')

    def circle(self, x, y, r, c, op=1.0):
        self.add(f'<circle cx="{x:.1f}" cy="{y:.1f}" r="{r}" fill="{c}" opacity="{op}"/>')

    def path(self, pts, c, w=2, dash=None):
        if not pts:
            return
        d = " ".join(("M" if i == 0 else "L") + f"{x:.1f} {y:.1f}"
                     for i, (x, y) in enumerate(pts))
        da = f' stroke-dasharray="{dash}"' if dash else ""
        self.add(f'<path d="{d}" fill="none" stroke="{c}" stroke-width="{w}"{da}/>')

    def title(self, main, sub=None):
        self.text(14, 26, main, size=15, weight="600")
        if sub:
            self.text(14, 46, sub, size=12, c=C_AX)

    def legend(self, items):
        for j, (name, col) in enumerate(items):
            y = self.y0 + 6 + j * 22
            self.rect(self.x1 + 22, y, 12, 12, col, rx=2)
            self.text(self.x1 + 40, y + 11, name, size=11)

    def render(self) -> str:
        return (f'<svg viewBox="0 0 {self.w} {self.h}" width="100%" '
                f'style="max-width:{self.w}px;display:block" '
                f'xmlns="http://www.w3.org/2000/svg">' + "".join(self.parts) + "</svg>")


# --------------------------------------------------------------------------- #
def fig_forest(rows, main, sub, xlab, fmt="{:+.2f}", w=1000) -> str:
    """rows = [(label, beta, se, p)]。水平フォレスト（95% CI）。"""
    h = 100 + 34 * len(rows)
    s = Svg(w, h, top=76, right=200, bottom=52, left=360)
    lo = min(b - 2.4 * e for _, b, e, _ in rows)
    hi = max(b + 2.4 * e for _, b, e, _ in rows)
    if lo > 0:
        lo = -hi * 0.15
    if hi < 0:
        hi = -lo * 0.15
    pad = (hi - lo) * 0.08
    lo, hi = lo - pad, hi + pad

    def X(v): return s.x0 + (v - lo) / (hi - lo) * (s.x1 - s.x0)

    s.title(main, sub)
    for f in range(5):
        v = lo + (hi - lo) * f / 4
        s.line(X(v), s.y0 - 6, X(v), s.y1 + 6, C_GRID)
    s.line(X(0), s.y0 - 6, X(0), s.y1 + 6, C_ZERO, 1.5, dash="5 4")
    for i, (lab, b, e, p) in enumerate(rows):
        y = s.y0 + 16 + i * 34
        col = C_REAL if p < 0.05 else C_CTRL
        s.text(s.x0 - 16, y + 4, lab, size=12, anchor="end")
        s.line(X(b - 1.96 * e), y, X(b + 1.96 * e), y, "#6f767d", 2.5)
        s.circle(X(b), y, 5.5, col)
        s.text(s.x1 + 22, y + 4, f"{fmt.format(b)}   p={p:.3f}", size=11,
               c=C_REAL if p < 0.05 else C_TXT, weight="600" if p < 0.05 else "400")
    s.line(s.x0, s.y1 + 6, s.x1, s.y1 + 6, C_AX)
    for f in range(5):
        v = lo + (hi - lo) * f / 4
        s.line(X(v), s.y1 + 6, X(v), s.y1 + 11, C_AX)
        s.text(X(v), s.y1 + 26, fmt.format(v), size=10, anchor="middle", c=C_AX)
    s.text((s.x0 + s.x1) / 2, s.y1 + 44, xlab, size=11, anchor="middle", c=C_AX)
    return s.render()


def fig_points(cats, series, main, sub, xlab, ylab, fmt="{:+.3f}", w=1000, h=400) -> str:
    """カテゴリ × 系列の点＋95%CI 折れ線。series = {name: (color, [(mean, se)])}。"""
    s = Svg(w, h, top=76, right=210, bottom=72, left=92)
    vals = [(m, e) for _, rows in series.values() for m, e in rows if m == m]
    lo = min(m - 2.2 * e for m, e in vals)
    hi = max(m + 2.2 * e for m, e in vals)
    pad = (hi - lo) * 0.1 or 0.01
    lo, hi = lo - pad, hi + pad

    def X(i): return s.x0 + (i + 0.5) / len(cats) * (s.x1 - s.x0)

    def Y(v): return s.y1 - (v - lo) / (hi - lo) * (s.y1 - s.y0)

    s.title(main, sub)
    for f in range(5):
        v = lo + (hi - lo) * f / 4
        s.line(s.x0, Y(v), s.x1, Y(v), C_GRID)
        s.text(s.x0 - 10, Y(v) + 4, fmt.format(v), size=10, anchor="end", c=C_AX)
    if lo < 0 < hi:
        s.line(s.x0, Y(0), s.x1, Y(0), C_ZERO, 1.5, dash="5 4")
    for j, (name, (col, rows)) in enumerate(series.items()):
        pts = []
        for i, (m, e) in enumerate(rows):
            if m != m:
                continue
            x = X(i) + (j - (len(series) - 1) / 2) * 7
            pts.append((x, Y(m)))
            s.line(x, Y(m - 1.96 * e), x, Y(m + 1.96 * e), col, 2)
            s.circle(x, Y(m), 4.5, col)
        s.path(pts, col, 2)
    s.legend([(n, c) for n, (c, _) in series.items()])
    for i, lab in enumerate(cats):
        s.text(X(i), s.y1 + 22, lab, size=11, anchor="middle", c=C_AX)
    s.text((s.x0 + s.x1) / 2, s.y1 + 46, xlab, size=11, anchor="middle", c=C_AX)
    s.text(22, (s.y0 + s.y1) / 2, ylab, size=11, c=C_AX, anchor="middle", rot=-90)
    return s.render()


def fig_profile(profiles, main, sub, w=1000, h=380) -> str:
    s = Svg(w, h, top=76, right=230, bottom=64, left=86)
    allv = [v for _, ys in profiles.values() for v in ys]
    lo, hi = min(allv), max(allv)
    pad = (hi - lo) * 0.12 or 0.01
    lo, hi = lo - pad, hi + pad
    n = max(len(ys) for _, ys in profiles.values())

    def X(i): return s.x0 + i / (n - 1) * (s.x1 - s.x0)

    def Y(v): return s.y1 - (v - lo) / (hi - lo) * (s.y1 - s.y0)

    s.title(main, sub)
    for f in range(5):
        v = lo + (hi - lo) * f / 4
        s.line(s.x0, Y(v), s.x1, Y(v), C_GRID)
        s.text(s.x0 - 10, Y(v) + 4, f"{v:+.2f}", size=10, anchor="end", c=C_AX)
    if lo < 0 < hi:
        s.line(s.x0, Y(0), s.x1, Y(0), C_ZERO, 1.5, dash="5 4")
    for name, (col, ys) in profiles.items():
        s.path([(X(i), Y(v)) for i, v in enumerate(ys)], col, 2.4)
    s.legend([(n_, c) for n_, (c, _) in profiles.items()])
    for t in range(0, n, 10):
        s.line(X(t), s.y1, X(t), s.y1 + 5, C_AX)
        s.text(X(t), s.y1 + 21, str(t), size=10, anchor="middle", c=C_AX)
    s.text((s.x0 + s.x1) / 2, s.y1 + 44, "接触からの経過（分）", size=11,
           anchor="middle", c=C_AX)
    s.text(22, (s.y0 + s.y1) / 2, "← 貫通  |  押し返し →（行）", size=11,
           c=C_AX, anchor="middle", rot=-90)
    return s.render()


def fig_sens(grid, main, sub, w=1000, h=380) -> str:
    s = Svg(w, h, top=76, right=210, bottom=64, left=76)
    ts = [r[d]["t"] for r in grid for d in ("sup", "res") if r[d]["t"] == r[d]["t"]]
    lim = max(4.0, max(abs(t) for t in ts) * 1.12)
    n = len(grid)

    def X(i): return s.x0 + (i + 0.5) / n * (s.x1 - s.x0)

    def Y(v): return s.y1 - (v + lim) / (2 * lim) * (s.y1 - s.y0)

    s.title(main, sub)
    s.rect(s.x0, Y(1.96), s.x1 - s.x0, Y(-1.96) - Y(1.96), "#3b4147", 0.5, rx=4)
    for v in (-1.96, 1.96):
        s.line(s.x0, Y(v), s.x1, Y(v), C_ZERO, 1.2, dash="5 4")
    s.line(s.x0, Y(0), s.x1, Y(0), C_AX, 1)
    s.text(s.x0 + 10, Y(0) - 8, "灰帯 = 有意差なし（|t| < 1.96）", size=11, c="#c3c8cd")
    s.text(s.x0 + 10, Y(-1.96) + 22, "この下側 = 高zバーの方が押し返さない（有意）",
           size=11, c=C_REAL)
    for j, (d, col, nm) in enumerate((("res", C_CTRL, "レジスタンス方向（下から接近）"),
                                      ("sup", C_REAL, "サポート方向（上から接近）"))):
        for i, r in enumerate(grid):
            t = r[d]["t"]
            if t == t:
                s.circle(X(i) + (j - 0.5) * 5, Y(max(-lim, min(lim, t))), 4, col, 0.95)
    s.legend([("レジスタンス方向", C_CTRL), ("サポート方向", C_REAL)])
    for f in range(5):
        v = -lim + 2 * lim * f / 4
        s.text(s.x0 - 10, Y(v) + 4, f"{v:+.1f}", size=10, anchor="end", c=C_AX)
    s.text((s.x0 + s.x1) / 2, s.y1 + 40,
           f"{n} 通りの設定（z 閾値 × 反応窓 × 跳ね返り幅 × 遡及日数）",
           size=11, anchor="middle", c=C_AX)
    s.text(20, (s.y0 + s.y1) / 2, "t 値", size=11, c=C_AX, anchor="middle", rot=-90)
    return s.render()


# --------------------------------------------------------------------------- #
def build(path: Path = _OUT / "sr_study.json", out: Path = _OUT / "sr_report.html") -> Path:
    R = json.load(open(path))
    m = R["meta"]
    cards: "list[str]" = []

    def card(svg, note=""):
        n = f'<div class="note">{note}</div>' if note else ""
        cards.append(f'<div class="card">{svg}{n}</div>')

    # 図1: 主検定（跳ね返り率・％ポイント）
    rows = []
    for d, dn in (("res", "レジスタンス｜下から接近"), ("sup", "サポート｜上から接近")):
        b = R["main"][d]
        for key, kn in (("placebo_bounce", "整合偽水準"), ("fake_a_bounce", "低z水準")):
            r = b[key]
            rows.append((f"{dn}　vs {kn}", r["beta"] * 100, r["se"] * 100, r["p_boot"]))
    card(fig_forest(rows, "図1  高zバーは「跳ね返す」か — 対照との差",
                    "接触日固定効果＋日クラスタ頑健分散／95% 信頼区間／p は日系列の定常ブートストラップ",
                    "跳ね返り率の差（％ポイント）　0 = 効果なし　正 = 高zバーの方が押し返す",
                    fmt="{:+.2f}"),
         "正の側に出て初めて S/R として実在。実測は全て 0 以下で、"
         "低z水準との比較では<b>有意に負</b>（高zバーの方が押し返さない）。")

    # 図2: 連続量
    rows2 = []
    for d, dn in (("res", "レジスタンス"), ("sup", "サポート")):
        b = R["main"][d]
        for key, kn in (("placebo_end", "30分後の押し返し　vs 整合偽水準"),
                        ("placebo_mre", "最大押し返し　vs 整合偽水準"),
                        ("fake_a_end", "30分後の押し返し　vs 低z水準")):
            r = b[key]
            rows2.append((f"{dn}｜{kn}", r["beta"], r["se"], r["p_boot"]))
    card(fig_forest(rows2, "図2  二値でなく連続量で測っても同じ",
                    "1 行 = 当日レンジの 1/40（≒ 2.5%）",
                    "押し返し量の差（行）　正 = 高zバーの方が押し返す", fmt="{:+.3f}"),
         "効果量を捨てない連続量でも正側の証拠は出ない。")

    # 図3: z 連続体
    bands = R["z_scan"]["bands"]
    gn = [f"zb:{a}:{b}" for a, b in bands]
    cats = [(f"z<{b:g}" if a <= -50 else (f"z≥{a:g}" if b >= 50 else f"{a:g}〜{b:g}"))
            for a, b in bands]
    for metric, ylab, fmt, mn in (
            ("bounce", "当日平均との差（跳ね返り率）", "{:+.3f}", "3a  跳ね返り率"),
            ("end", "当日平均との差（行）", "{:+.2f}", "3b  30分後の押し返し量")):
        ser = {}
        for d, col, nm in (("res", C_CTRL, "レジスタンス方向"), ("sup", C_REAL, "サポート方向")):
            S = R["z_scan"]["result"][d][metric]
            ser[nm] = (col, [(S[g].get("demeaned", float("nan")),
                              S[g].get("se", float("nan"))) for g in gn])
        card(fig_points(cats, ser, f"図{mn} — 閾値によらない検証",
                        "全セルを z の水準で層化し、当日平均を引いた反応（z≥3 という恣意的な線を使わない）",
                        "水準の z（超過占有スコア。高い＝価格が長く受容した価格帯）", ylab, fmt=fmt),
             "S/R 仮説が正しければ右肩上がりになるはず。実測は<b>右肩下がり</b>＝"
             "z が高い価格帯ほど反応が弱い。")

    # 図4: 反応プロファイル
    p = R["profiles"]
    for d, nm in (("res", "レジスタンス方向（下から接近）"),
                  ("sup", "サポート方向（上から接近）")):
        card(fig_profile(
            {"高zバー": (C_REAL, p[f"real_peak_{d}"]),
             "整合偽水準（±5行ずらし）": (C_CTRL, p[f"placebo_{d}"]),
             "低z水準": (C_FAKE, p[f"fake_a_{d}"])},
            f"図4  接触後 60 分の平均経路 — {nm}",
            "接触した分に価格は水準を行き過ぎる（起点が負）。その後どれだけ押し返されるかを見る"),
            "高zバー（赤）が最も押し返されない。")

    # 図5: 感度
    if "sensitivity" in R:
        g = R["sensitivity"]
        npos = sum(1 for r in g for d in ("sup", "res")
                   if r[d]["t"] == r[d]["t"] and r[d]["t"] > 1.96)
        nneg = sum(1 for r in g for d in ("sup", "res")
                   if r[d]["t"] == r[d]["t"] and r[d]["t"] < -1.96)
        card(fig_sens(g, "図5  設定を変えても結論は動かない",
                      "対 整合偽水準・跳ね返り率／z閾値 2〜5・反応窓 10〜60分・跳ね返り幅 2〜8行・遡及 10〜250日"),
             f"{2*len(g)} 通り中、S/R を支持する側（t &gt; +1.96）は <b>{npos} 件</b>、"
             f"否定する側（t &lt; −1.96）は <b>{nneg} 件</b>。")

    body = "\n".join(cards)
    html = f"""<!doctype html>
<html lang="ja"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>MP zp 高zバーの S/R 検定</title>
<style>
body{{margin:0;background:#15181b;color:#e8eaed;font-family:{FONT};padding:26px 22px 60px}}
h1{{font-size:21px;margin:0 0 6px}}
.sub{{color:#9aa0a6;font-size:13px;line-height:1.7;margin-bottom:8px}}
.verdict{{background:#241c1a;border:1px solid #5a3428;border-left:4px solid #e4572e;
border-radius:8px;padding:14px 16px;margin:16px 0 24px;font-size:14px;line-height:1.75}}
.card{{background:#1c2024;border:1px solid #2f3438;border-radius:10px;
padding:14px 14px 10px;margin-bottom:18px;overflow-x:auto}}
.note{{color:#b6bcc2;font-size:12px;line-height:1.7;margin:6px 6px 4px;
border-top:1px solid #2f3438;padding-top:8px}}
b{{color:#f0b6a4}}
</style></head><body>
<h1>MP 指標 zp — 高 z バーはレジスタンス／サポートとして機能するか</h1>
<div class="sub">JP225・{m['period'][0]} 〜 {m['period'][1]}・{m['n_days']} 営業日／
主検定: z≥{m['z_thr']:g}・遡及 {m['lookback']} 日・反応窓 {m['k_minutes']} 分・跳ね返り {m['x_rows']:g} 行・
形成後初回接触のみ／推論: 接触日固定効果＋日クラスタ頑健分散</div>
<div class="verdict"><b>結論: 機能しない。</b>
高 z バーへの初回接触は、同じ日・同じレンジ位置にある「ただの価格」（±5行ずらした整合偽水準）
と比べて押し返されやすくない。低 z 水準との比較では<b>有意に押し返されにくい</b>。
方向を分けても（サポート／レジスタンス）、二値でなく連続量で測っても、閾値を外して z の連続体で見ても、
40 通りの設定に振っても、S/R を支持する有意な証拠は 1 件も出ない。</div>
{body}
</body></html>"""
    out.write_text(html, encoding="utf-8")
    return out


if __name__ == "__main__":
    print(build())
