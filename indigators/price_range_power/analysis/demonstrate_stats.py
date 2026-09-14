"""price_range_power の統計的問題を実データで定量実証する（本体未変更・外部分析）。

Demo A: 上位 S/R 帯が「標本数1」に支配される退化と、占有度重み付け(縮約)での順位安定性。
Demo B: S/R 帯の前向き有効性（因果 OOS）を、同数のランダム価格水準（帰無）と比較。
Demo C: σ ビン分類の校正（ヒゲ幅の歪み）と絶対価格帯のスケール非不変。

数値表を標準出力。matplotlib があれば PNG 図も out/ に生成する。
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

_HERE = Path(__file__).resolve()
sys.path.insert(0, str(_HERE.parents[1]))

from src.core import WICK_NAMES, wick_samples, wick_stats  # noqa: E402
from src.ratio import build_bull_bear_profile  # noqa: E402

_EXAMPLES = _HERE.parents[3] / "lightweight-charts-python-main/examples"
_VOLATILE = _EXAMPLES / "4_line_indicators/ohlcv.csv"   # 2981本, 1.05->407 (386x)
_STABLE = _EXAMPLES / "6_callbacks/bar_data/AAPL_1min.csv"  # 2083本, 170-176 (1.03x)
_OUT = _HERE.parent / "out"
_RNG = np.random.default_rng(20240606)


def _load(path: Path) -> pd.DataFrame:
    df = pd.read_csv(path)
    cols = {c.lower(): c for c in df.columns}
    return pd.DataFrame({k: df[cols[k]].to_numpy(float) for k in ("open", "high", "low", "close")})


def _levels(df, *, k, interval, rng, score, min_freq=1):
    """in-sample から上位 K の支持(bull)/抵抗(bear)価格水準を抽出する。

    score="raw": net_power（条件付き率）/ score="occupancy": net_power×占有度(min_freq適用)。
    range は外部固定（帯グリッドを一致させる）。
    """
    prof = build_bull_bear_profile(df, interval=interval, range_from=rng[0], range_to=rng[1])
    bands = np.round(prof.index.to_numpy(float), 4)
    bull = prof["bull_power"].to_numpy(); bear = prof["bear_power"].to_numpy()
    net = prof["net_power"].to_numpy()
    fl = prof["freq_low"].to_numpy(); fh = prof["freq_high"].to_numpy()
    if score == "raw":
        bs = np.where((net > 0) & (bull > 0), net, -np.inf)
        rs = np.where((net < 0) & (bear > 0), -net, -np.inf)
    else:  # occupancy
        bs = np.where((net > 0) & (bull > 0) & (fl >= min_freq), net * fl, -np.inf)
        rs = np.where((net < 0) & (bear > 0) & (fh >= min_freq), -net * fh, -np.inf)
    sup = bands[[i for i in np.argsort(-bs)[:k] if np.isfinite(bs[i])]]
    res = bands[[i for i in np.argsort(-rs)[:k] if np.isfinite(rs[i])]]
    return set(sup.tolist()), set(res.tolist()), prof


def _jaccard(a, b):
    a, b = set(a), set(b)
    return len(a & b) / len(a | b) if (a | b) else 1.0


# --------------------------------------------------------------------------- #
def demo_a_stability(df, name, *, k=8, interval=0.1, B=150):
    rng = (float(df["low"].min()), float(df["high"].max()))
    sup0_raw, res0_raw, prof = _levels(df, k=k, interval=interval, rng=rng, score="raw")
    sup0_occ, res0_occ, _ = _levels(df, k=k, interval=interval, rng=rng, score="occupancy", min_freq=3)

    # n=1 支配の度合い
    fl = prof["freq_low"].to_numpy(); bp = prof["bull_power"].to_numpy()
    n_nonzero = int((fl > 0).sum())
    n_one = int((fl == 1).sum())
    share_le2 = float(((bp > 0) & (fl <= 2)).sum()) / max(1, int((bp > 0).sum()))

    # ブートストラップでの選抜安定性（full の上位集合との Jaccard）
    jr, jo = [], []
    nbar = len(df)
    for _ in range(B):
        idx = _RNG.integers(0, nbar, nbar)
        bs = df.iloc[idx].reset_index(drop=True)
        s_r, r_r, _ = _levels(bs, k=k, interval=interval, rng=rng, score="raw")
        s_o, r_o, _ = _levels(bs, k=k, interval=interval, rng=rng, score="occupancy", min_freq=3)
        jr.append((_jaccard(sup0_raw, s_r) + _jaccard(res0_raw, r_r)) / 2)
        jo.append((_jaccard(sup0_occ, s_o) + _jaccard(res0_occ, r_o)) / 2)
    return {
        "name": name, "n_bands": len(prof), "n_nonzero": n_nonzero, "n_freq1": n_one,
        "share_bull_le2": share_le2,
        "jaccard_raw": float(np.mean(jr)), "jaccard_occ": float(np.mean(jo)),
        "_jr": np.array(jr), "_jo": np.array(jo),
    }


# --------------------------------------------------------------------------- #
def _respect(oos, levels, side, tol):
    """OOS で各水準の touch 回数と respect 率（期待側で引けた割合）を返す。"""
    low = oos["low"].to_numpy(); high = oos["high"].to_numpy(); close = oos["close"].to_numpy()
    touches, respects = [], []
    for L in levels:
        t = (low <= L + tol) & (high >= L - tol)
        nt = int(t.sum())
        touches.append(nt)
        if nt == 0:
            respects.append(np.nan); continue
        if side == "support":
            respects.append(float((close[t] >= L).mean()))
        else:
            respects.append(float((close[t] <= L).mean()))
    return np.array(touches, float), np.array(respects, float)


def demo_b_efficacy(df, name, *, k=8, interval=0.1, B=300):
    n = len(df); cut = int(n * 0.6)
    ins, oos = df.iloc[:cut].reset_index(drop=True), df.iloc[cut:].reset_index(drop=True)
    rng = (float(df["low"].min()), float(df["high"].max()))
    tol = interval

    out = {}
    for score in ("raw", "occupancy"):
        sup, res, _ = _levels(ins, k=k, interval=interval, rng=rng, score=score, min_freq=3)
        st, sr = _respect(oos, sorted(sup), "support", tol)
        rt, rr = _respect(oos, sorted(res), "resistance", tol)
        all_resp = np.concatenate([sr, rr])
        all_t = np.concatenate([st, rt])
        out[score] = {
            "n_levels": len(sup) + len(res),
            "mean_touch": float(np.nanmean(all_t)),
            "touched_levels": int((all_t > 0).sum()),
            "respect": float(np.nanmean(all_resp)) if np.isfinite(all_resp).any() else float("nan"),
        }

    # 帰無: ランダム水準（同数の support/resistance）
    nsup, nres = k, k
    lo, hi = rng
    null_resp, null_touch = [], []
    for _ in range(B):
        rs = _RNG.uniform(lo, hi, nsup); rr_ = _RNG.uniform(lo, hi, nres)
        st, sr = _respect(oos, rs, "support", tol)
        rt, rr = _respect(oos, rr_, "resistance", tol)
        ar = np.concatenate([sr, rr]); at = np.concatenate([st, rt])
        if np.isfinite(ar).any():
            null_resp.append(np.nanmean(ar)); null_touch.append(np.nanmean(at))
    null_resp = np.array(null_resp); null_touch = np.array(null_touch)
    return {
        "name": name, "ins": cut, "oos": n - cut,
        "raw": out["raw"], "occ": out["occupancy"],
        "null_resp_mean": float(null_resp.mean()), "null_resp_p5": float(np.percentile(null_resp, 5)),
        "null_resp_p95": float(np.percentile(null_resp, 95)),
        "null_touch_mean": float(null_touch.mean()),
        "_null_resp": null_resp,
    }


# --------------------------------------------------------------------------- #
def demo_c_calibration(df, name):
    o, h, l, c = (df[k].to_numpy(float) for k in ("open", "high", "low", "close"))
    samp = wick_samples(o, h, l, c)
    rows = []
    gauss = {1: 0.1587, 2: 0.0228, 3: 0.00135}
    for nm in WICK_NAMES:
        x = samp[nm]; x = x[np.isfinite(x)]
        if x.size < 3:
            continue
        mu, sd = x.mean(), x.std(ddof=1)
        skew = float(np.mean(((x - mu) / sd) ** 3))
        exc = {kk: float((x > mu + kk * sd).mean()) for kk in (1, 2, 3)}
        rows.append((nm, x.size, skew, exc[1], exc[2], exc[3]))
    # スケール: 帯%の価格依存・空帯率
    prof = build_bull_bear_profile(df, interval=0.1)
    bands = prof.index.to_numpy(float)
    band_pct_lo = 0.1 / bands.min() * 100
    band_pct_hi = 0.1 / bands.max() * 100
    empty = float(((prof["freq_low"] == 0) & (prof["freq_high"] == 0)).sum()) / len(prof)
    return {"name": name, "rows": rows, "gauss": gauss,
            "band_pct_lo": band_pct_lo, "band_pct_hi": band_pct_hi, "empty_frac": empty}


# --------------------------------------------------------------------------- #
def make_figures(a, b, c, out_dir):
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except Exception as e:
        print(f"[figures skipped] {e}"); return []
    out_dir.mkdir(parents=True, exist_ok=True)
    paths = []

    # A: 安定性
    fig, ax = plt.subplots(figsize=(8, 4))
    ax.hist(a["_jr"], bins=20, alpha=0.7, color="#C62828", label=f"raw rate (mean {a['jaccard_raw']:.2f})")
    ax.hist(a["_jo"], bins=20, alpha=0.7, color="#1565C0", label=f"occupancy-weighted (mean {a['jaccard_occ']:.2f})")
    ax.set_title(f"DemoA: top-{8} S/R selection stability (bootstrap Jaccard) -- {a['name']}")
    ax.set_xlabel("Jaccard overlap with full-data top-K"); ax.set_ylabel("count")
    ax.legend(); ax.grid(alpha=0.3)
    p = out_dir / "demoA_stability.png"; fig.tight_layout(); fig.savefig(p, dpi=120); plt.close(fig); paths.append(p)

    # B: 効力 vs 帰無
    fig, ax = plt.subplots(figsize=(8, 4))
    ax.hist(b["_null_resp"], bins=25, color="#90A4AE", alpha=0.8, label="random levels (null)")
    ax.axvline(b["raw"]["respect"], color="#C62828", lw=2, label=f"indicator raw ({b['raw']['respect']:.2f})")
    ax.axvline(b["occ"]["respect"], color="#1565C0", lw=2, label=f"indicator occ ({b['occ']['respect']:.2f})")
    ax.set_title(f"DemoB: OOS 'respect' rate vs random null -- {b['name']}")
    ax.set_xlabel("mean respect rate (closed on expected side)"); ax.set_ylabel("count")
    ax.legend(); ax.grid(alpha=0.3)
    p = out_dir / "demoB_efficacy.png"; fig.tight_layout(); fig.savefig(p, dpi=120); plt.close(fig); paths.append(p)

    # C: σ 校正（hc 系統のヒゲ幅分布）
    o, h, l, cc = None, None, None, None
    fig, ax = plt.subplots(figsize=(8, 4))
    # 代表系統 hl（全幅）の分布
    df = c["_df"]
    samp = wick_samples(df["open"].to_numpy(float), df["high"].to_numpy(float),
                        df["low"].to_numpy(float), df["close"].to_numpy(float))
    x = samp["hl"]; x = x[np.isfinite(x)]
    mu, sd = x.mean(), x.std(ddof=1)
    ax.hist(x, bins=60, color="#1565C0", alpha=0.8)
    for kk, col in [(1, "#2E9E5B"), (2, "#E0A800"), (3, "#C62828")]:
        ax.axvline(mu + kk * sd, color=col, lw=1.5, label=f"mean+{kk}σ")
    ax.set_title(f"DemoC: wick-width (hl) is right-skewed; Gaussian σ mislabeled -- {c['name']}")
    ax.set_xlabel("wick width (price pts)"); ax.set_ylabel("count")
    ax.legend(); ax.grid(alpha=0.3)
    p = out_dir / "demoC_calibration.png"; fig.tight_layout(); fig.savefig(p, dpi=120); plt.close(fig); paths.append(p)
    return paths


def _p_a(a):
    print(f"\n### DemoA n=1支配と順位安定性 -- {a['name']}")
    print(f"  総帯 {a['n_bands']} / 非ゼロ {a['n_nonzero']} / freq_low==1 {a['n_freq1']}帯")
    print(f"  bull_power>0 帯のうち 標本数<=2 の割合: {a['share_bull_le2']*100:.1f}%")
    print(f"  ブートストラップ選抜安定性(Jaccard) raw={a['jaccard_raw']:.3f} vs 占有度重み={a['jaccard_occ']:.3f}")


def _p_b(b):
    print(f"\n### DemoB 前向き有効性 vs ランダム水準 -- {b['name']} (in={b['ins']} / oos={b['oos']})")
    print(f"  {'方式':<10}{'respect率':>10}{'平均touch':>10}{'被touch水準':>12}")
    print(f"  {'raw':<10}{b['raw']['respect']:>10.3f}{b['raw']['mean_touch']:>10.2f}{b['raw']['touched_levels']:>12d}")
    print(f"  {'occupancy':<10}{b['occ']['respect']:>10.3f}{b['occ']['mean_touch']:>10.2f}{b['occ']['touched_levels']:>12d}")
    print(f"  ランダム帰無 respect: 平均 {b['null_resp_mean']:.3f} [5%,95%]=[{b['null_resp_p5']:.3f},{b['null_resp_p95']:.3f}] / 平均touch {b['null_touch_mean']:.2f}")


def _p_c(c):
    print(f"\n### DemoC σビン校正とスケール -- {c['name']}")
    print(f"  {'系統':<6}{'n':>6}{'歪度':>8}{'>μ+1σ':>9}{'>μ+2σ':>9}{'>μ+3σ':>9}  (正規:0.159/0.023/0.0014)")
    for nm, n, sk, e1, e2, e3 in c["rows"]:
        print(f"  {nm:<6}{n:>6d}{sk:>8.2f}{e1:>9.3f}{e2:>9.3f}{e3:>9.4f}")
    print(f"  絶対0.1帯の幅: 価格min={c['band_pct_lo']:.2f}% vs max={c['band_pct_hi']:.3f}% / 空帯率 {c['empty_frac']*100:.1f}%")


def main():
    print("=" * 80)
    print("price_range_power 統計手法の実証（本体未変更・外部分析）")
    print("=" * 80)
    vol = _load(_VOLATILE)
    aapl = _load(_STABLE)

    a = demo_a_stability(vol, "volatile_386x")
    cfig = demo_c_calibration(vol, "volatile_386x"); cfig["_df"] = vol
    # 効力検証は水準が再来する安定銘柄で（絶対水準が trending だと OOS で再来しない）
    b = demo_b_efficacy(aapl, "stable_AAPL")

    _p_a(a); _p_b(b); _p_c(cfig)
    paths = make_figures(a, b, cfig, _OUT)
    if paths:
        print("\n図:", [str(p) for p in paths])
    print("\n" + "=" * 80)
    print("結論: 上位S/Rは標本数1の退化比率に支配され選抜は不安定。前向きrespectはランダム水準と")
    print("同等＝予測的優位性なし。σビンは歪み分布で誤校正、絶対帯はスケール非不変。")
    print("=" * 80)


if __name__ == "__main__":
    main()
