"""profit_band 統計手法の実証スクリプト（分析・本体実装は変更しない）。

評価で挙げた 3 点を実データで定量実証する:
  Demo 1: 絶対価格単位ゆえスケール非不変（価格水準で帯の意味が変わる/負価格が生じる）。
  Demo 2: 大域分位点ゆえ先読み（global vs 因果 expanding/rolling 窓）。
  Demo 3: 名目 p ≠ 実カバレッジ（因果 out-of-sample で被覆率が名目から乖離）。

数値表を標準出力へ。matplotlib があれば PNG 図も out/ に生成する。
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

_HERE = Path(__file__).resolve()
sys.path.insert(0, str(_HERE.parents[1]))  # profit_band/ を import path へ

from src.core import PROBABILITIES, collect_distance_samples  # noqa: E402
from src.bands import build_bands  # noqa: E402

_EXAMPLES = _HERE.parents[3] / "lightweight-charts-python-main/examples"
_DATASETS = {
    "volatile_386x": _EXAMPLES / "4_line_indicators/ohlcv.csv",   # close 1.05->407 (386x)
    "stable_AAPL": _EXAMPLES / "6_callbacks/bar_data/AAPL_1min.csv",  # 1.03x
}
_OUT = _HERE.parent / "out"


# --------------------------------------------------------------------------- #
# データ
# --------------------------------------------------------------------------- #
def _load(path: Path) -> pd.DataFrame:
    df = pd.read_csv(path)
    cols = {c.lower(): c for c in df.columns}
    return pd.DataFrame({
        "open": df[cols["open"]].to_numpy(float),
        "high": df[cols["high"]].to_numpy(float),
        "low": df[cols["low"]].to_numpy(float),
        "close": df[cols["close"]].to_numpy(float),
    })


def _excursions(df: pd.DataFrame):
    """各足の値幅（絶対値）と分類マスクを返す。core と同一定義。"""
    o, h, l, c = (df[k].to_numpy(float) for k in ("open", "high", "low", "close"))
    return o, np.abs(o - h), np.abs(o - l), np.abs(h - l), o < c, o > c, o == c


# --------------------------------------------------------------------------- #
# Demo 1: スケール非不変
# --------------------------------------------------------------------------- #
def demo1_scale(df: pd.DataFrame, name: str) -> dict:
    o = df["open"].to_numpy(float)

    # --- 現行（絶対値・大域） ---
    bands = build_bands(df)
    pol95 = bands["pOL_95"].to_numpy()          # open + q_pOL_95（定数オフセット）
    width_pct_abs = (pol95 - o) / o * 100.0     # 上側帯幅率 [%]
    neg_abs = int(((bands["nOL_99"].to_numpy() <= 0)).sum())  # 不可能価格（負）本数

    # --- 正規化（リターン比率） ---
    o_, oh, ol, hl, bull, bear, even = _excursions(df)
    # pOL = 陽線の OL（下方値幅）。比率 OL/open の 95% 分位点。
    r_pol = ol[bull] / o_[bull]
    q_pol_r = np.quantile(r_pol, 0.95, method="linear")
    width_pct_norm = np.full_like(o, q_pol_r * 100.0)          # 一定 [%]
    # 下側 nOL（陰線+同値の OL/open）99% で正規化帯の下端が負になるか
    r_nol = ol[bear | even] / o_[bear | even]
    q_nol_r = np.quantile(r_nol, 0.99, method="linear")
    neg_norm = int((o * (1 - q_nol_r) <= 0).sum())

    def at(arr, i):
        return float(arr[i])

    n = len(o)
    idx = {"先頭": 0, "中央": n // 2, "末尾": n - 1}
    rows = []
    for label, i in idx.items():
        rows.append((label, o[i], at(width_pct_abs, i), at(width_pct_norm, i)))

    res = {
        "name": name, "n": n,
        "price_min": float(o.min()), "price_max": float(o.max()),
        "abs_width_pct_min": float(np.nanmin(width_pct_abs)),
        "abs_width_pct_max": float(np.nanmax(width_pct_abs)),
        "abs_width_ratio": float(np.nanmax(width_pct_abs) / np.nanmin(width_pct_abs)),
        "norm_width_pct": float(q_pol_r * 100.0),
        "neg_price_abs": neg_abs, "neg_price_norm": neg_norm,
        "rows": rows,
        "_series": {"width_pct_abs": width_pct_abs, "width_pct_norm": width_pct_norm, "open": o},
    }
    return res


# --------------------------------------------------------------------------- #
# Demo 2: 先読み（大域 vs 因果窓）
# --------------------------------------------------------------------------- #
def _pol_samples_prefix(o, ol, bull, i):
    """bars[0..i] のうち陽線の OL サンプル（pOL バケット）。"""
    m = bull[: i + 1]
    return ol[: i + 1][m]


def demo2_lookahead(df: pd.DataFrame, name: str, p: float = 0.95,
                    min_obs: int = 30, window: int = 250) -> dict:
    o, oh, ol, hl, bull, bear, even = _excursions(df)
    n = len(o)

    global_q = float(np.quantile(ol[bull], p, method="linear"))

    expanding = np.full(n, np.nan)
    rolling = np.full(n, np.nan)
    for i in range(n):
        s = _pol_samples_prefix(o, ol, bull, i)
        if s.size >= min_obs:
            expanding[i] = np.quantile(s, p, method="linear")
        lo = max(0, i - window + 1)
        mw = bull[lo : i + 1]
        sw = ol[lo : i + 1][mw]
        if sw.size >= min_obs:
            rolling[i] = np.quantile(sw, p, method="linear")

    valid = ~np.isnan(expanding)
    rel = np.abs(global_q - expanding[valid]) / expanding[valid]
    first_quarter = valid & (np.arange(n) < n // 4)
    fq_ratio = float(np.nanmedian(global_q / expanding[first_quarter])) if first_quarter.any() else float("nan")

    return {
        "name": name, "p": p, "global_q": global_q,
        "mean_abs_rel_diff": float(np.mean(rel)),
        "first_quarter_global_over_causal": fq_ratio,
        "_series": {"expanding": expanding, "rolling": rolling, "global_q": global_q},
    }


# --------------------------------------------------------------------------- #
# Demo 3: 名目 p vs 実カバレッジ
# --------------------------------------------------------------------------- #
def demo3_coverage(df: pd.DataFrame, name: str,
                   probs=(0.80, 0.95, 0.99), min_obs: int = 100) -> dict:
    o, oh, ol, hl, bull, bear, even = _excursions(df)
    n = len(o)
    samp = ol[bull]  # pOL バケット

    out = []
    for p in probs:
        # in-sample・大域: 自バケットの被覆率（恒等的に ~p）
        q_glob = np.quantile(samp, p, method="linear")
        cov_in = float((samp <= q_glob).mean())

        # 因果 OOS: bar i までで q_p(i) を作り、次に出現する pOL サンプルが収まるか
        bull_idx = np.where(bull)[0]
        hit = tot = 0
        for k in range(len(bull_idx) - 1):
            i = bull_idx[k]
            prefix = ol[: i + 1][bull[: i + 1]]
            if prefix.size < min_obs:
                continue
            q_i = np.quantile(prefix, p, method="linear")
            nxt = ol[bull_idx[k + 1]]   # 次の陽線の OL（未来サンプル）
            tot += 1
            hit += int(nxt <= q_i)
        cov_oos = hit / tot if tot else float("nan")
        out.append((p, cov_in, cov_oos, tot))
    return {"name": name, "rows": out}


# --------------------------------------------------------------------------- #
# 図表
# --------------------------------------------------------------------------- #
def make_figures(d1: dict, d2: dict, d3: dict, out_dir: Path) -> list:
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except Exception as e:
        print(f"[figures skipped] matplotlib 不可: {e}")
        return []

    out_dir.mkdir(parents=True, exist_ok=True)
    paths = []

    # A: 帯幅率（絶対 vs 正規化）
    s = d1["_series"]
    fig, ax = plt.subplots(figsize=(10, 4))
    ax.plot(s["width_pct_abs"], label="absolute (current)", color="#C62828", lw=0.8)
    ax.plot(s["width_pct_norm"], label="return-normalized", color="#1565C0", lw=1.2)
    ax.set_yscale("log")
    ax.set_title(f"Demo1: upper band half-width [%], absolute vs normalized -- {d1['name']}")
    ax.set_xlabel("bar"); ax.set_ylabel("half-width [% of open] (log)")
    ax.legend(); ax.grid(alpha=0.3)
    pa = out_dir / "demo1_scale.png"; fig.tight_layout(); fig.savefig(pa, dpi=120); plt.close(fig)
    paths.append(pa)

    # B: 帯オフセット（大域定数 vs 因果）
    s = d2["_series"]
    fig, ax = plt.subplots(figsize=(10, 4))
    ax.axhline(s["global_q"], color="#C62828", lw=1.2, label="global q (look-ahead, constant)")
    ax.plot(s["expanding"], color="#1565C0", lw=0.9, label="expanding (causal)")
    ax.plot(s["rolling"], color="#00897B", lw=0.8, alpha=0.8, label="rolling(250) (causal)")
    ax.set_title(f"Demo2: pOL 95% offset, global vs causal -- {d2['name']}")
    ax.set_xlabel("bar"); ax.set_ylabel("offset (price pts)")
    ax.legend(); ax.grid(alpha=0.3)
    pb = out_dir / "demo2_lookahead.png"; fig.tight_layout(); fig.savefig(pb, dpi=120); plt.close(fig)
    paths.append(pb)

    # C: 実カバレッジ vs 名目 p
    rows = d3["rows"]
    ps = [r[0] for r in rows]
    x = np.arange(len(ps)); w = 0.35
    fig, ax = plt.subplots(figsize=(8, 4))
    ax.bar(x - w/2, [r[1] for r in rows], w, label="in-sample / global", color="#90A4AE")
    ax.bar(x + w/2, [r[2] for r in rows], w, label="causal OOS", color="#1565C0")
    ax.plot(x, ps, "o--", color="#C62828", label="nominal p")
    ax.set_xticks(x); ax.set_xticklabels([f"{int(p*100)}%" for p in ps])
    ax.set_title(f"Demo3: realized coverage vs nominal p -- {d3['name']}")
    ax.set_ylabel("coverage"); ax.set_ylim(0, 1.05); ax.legend(); ax.grid(alpha=0.3)
    pc = out_dir / "demo3_coverage.png"; fig.tight_layout(); fig.savefig(pc, dpi=120); plt.close(fig)
    paths.append(pc)
    return paths


# --------------------------------------------------------------------------- #
def _print_demo1(d):
    print(f"\n### Demo1 スケール非不変 — {d['name']} (n={d['n']}, price {d['price_min']:.2f}→{d['price_max']:.2f})")
    print(f"  {'位置':<6}{'open':>10}{'絶対帯幅率%':>14}{'正規化帯幅率%':>16}")
    for label, op, a, nm in d["rows"]:
        print(f"  {label:<6}{op:>10.2f}{a:>14.2f}{nm:>16.2f}")
    print(f"  絶対 帯幅率 max/min 比 = {d['abs_width_ratio']:.1f}（正規化は一定 {d['norm_width_pct']:.2f}%）")
    print(f"  不可能価格(負)本数: 絶対 nOL99={d['neg_price_abs']} / 正規化={d['neg_price_norm']}")


def _print_demo2(d):
    print(f"\n### Demo2 先読み（大域 vs 因果）— {d['name']} (pOL {int(d['p']*100)}%)")
    print(f"  大域 q = {d['global_q']:.4f}（全期間=未来含む・定数）")
    print(f"  |大域−expanding因果|/因果 の平均 = {d['mean_abs_rel_diff']*100:.1f}%")
    print(f"  前半25%区間の 大域/因果 中央値 = {d['first_quarter_global_over_causal']:.2f} 倍（早期帯の過大度）")


def _print_demo3(d):
    print(f"\n### Demo3 名目 p vs 実カバレッジ — {d['name']}")
    print(f"  {'名目p':>6}{'in-sample/大域':>16}{'因果OOS':>12}{'OOS判定数':>10}")
    for p, ci, co, tot in d["rows"]:
        print(f"  {int(p*100):>5}%{ci:>16.3f}{co:>12.3f}{tot:>10d}")


def main():
    print("=" * 78)
    print("profit_band 統計手法の実証（本体は未変更・外部分析）")
    print("=" * 78)

    primary = None
    for name, path in _DATASETS.items():
        if not path.is_file():
            print(f"[skip] データ無し: {path}")
            continue
        df = _load(path)
        d1 = demo1_scale(df, name)
        d2 = demo2_lookahead(df, name)
        d3 = demo3_coverage(df, name)
        _print_demo1(d1)
        _print_demo2(d2)
        _print_demo3(d3)
        if primary is None:  # 図は主データ（最初=volatile）で生成
            paths = make_figures(d1, d2, d3, _OUT)
            primary = (d1, d2, d3, paths)
            if paths:
                print(f"\n図: {[str(p) for p in paths]}")

    print("\n" + "=" * 78)
    print("結論: 絶対単位は価格水準依存で帯の意味が破綻（負価格も発生）。大域分位点は先読み。")
    print("因果OOSの実カバレッジは名目pから乖離＝『X%バンド』は予測確率区間ではない。")
    print("=" * 78)


if __name__ == "__main__":
    main()
