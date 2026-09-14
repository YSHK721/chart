"""Step10: zp 高 z バーは **レジスタンス / サポート** として機能するか（ISSUE-248）。

Step9（ISSUE-061）は事前登録した主検定 1 本で「naked 高 z 水準は S/R として機能しない」を
得たが、次の 3 点が未実施だった。本ステップはそこを埋める。

  1. **方向の分離**: 「跳ね返り」を上下合算で測っていた。S/R は方向で別概念である
     （下から接近 → レジスタンス / 上から接近 → サポート）。
  2. **整合対照**: 対照が「同日の低 z セル」だけだった。低 z セルは日レンジの外縁に偏り、
     接近幾何（水準までの距離・到達しやすさ）が高 z セルと揃わない。本ステップは
     **同一水準を価格方向へ δ 行ずらした偽水準（placebo）**を主対照に据える。同じ形成日・
     同じレンジ位置・ほぼ同じ距離で「その価格が特別か」だけを分離する。
  3. **連続量の反応**: 二値の跳ね返り率に加え、接触後 t 分の符号付き変位プロファイル
     （逆行＝正・行単位）を測る。二値化で捨てていた効果量を検出する。

定義の出典（変更禁止）: 接触判定・跳ね返り判定・行単位（日レンジ/40）は
:mod:`step9_naked_revisit` が参照実装。本ステップは :mod:`sr_core` 経由で同一定義を用いる
（等価性は tests/test_sr_core.py が全水準突合で担保）。

推論: 事件は同一日に多数生じて独立でない。有効標本は**日**であり、同一接触日内で
本物と対照を**対にした差**のみを勘定する（Step9 で確認した Simpson 逆転の再発防止）。
p 値は日次差系列への定常ブートストラップ（Politis-Romano・自動ブロック長）で与える。
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from . import sr_core as sc
from .stats_core import pw_block_len, sign_test_pvalue, stationary_bootstrap_indices

#: 事前登録（Step9 と同一）。
Z_THRESHOLD = 3.0
LOOKBACK_DAYS = 60
REACTION_MINUTES = 30
BOUNCE_ROWS = 4.0
NULL_A_Z_MAX = 0.5

#: 主対照 placebo のオフセット（形成日の行幅単位・両側）。
PLACEBO_ROWS = 5.0

#: 反応プロファイルの最大分数。
PROFILE_MINUTES = 60


@dataclass(frozen=True)
class Day:
    """1 営業日の z 行グリッドと分足経路（キャッシュから 1 回だけ読む）。"""

    day: int
    row_price: "np.ndarray"
    z: "np.ndarray"
    closes: "np.ndarray"
    cell_width: float
    row_width: float


def load_days(znull_dir: Path, mgrid_dir: Path, days: "list[int]") -> "list[Day]":
    """znull / mgrid がともに非空の日を昇順で返す（step9.load_day と同一の読み口）。"""
    out: "list[Day]" = []
    for d in sorted(days):
        zf, mf = znull_dir / f"{d}.npz", mgrid_dir / f"{d}.npz"
        if not zf.exists() or not mf.exists():
            continue
        zd, md = np.load(zf), np.load(mf)
        if bool(zd["empty"]) or bool(md["empty"]):
            continue
        obs, mean, var = zd["obs"], zd["mean"], zd["var"]
        if obs.size == 0:
            continue
        with np.errstate(invalid="ignore", divide="ignore"):
            z = (obs - mean) / np.sqrt(var)
        z = np.where(np.isfinite(z), z, -np.inf)
        grid_w = float(zd["grid_w"])
        k = int(zd["kmin"]) + np.arange(obs.size, dtype=np.float64)
        prices = np.exp(k * grid_w * sc._LOG_UNIT)
        if prices.size < 2:
            continue
        cell = float(np.median(np.diff(prices)))
        row = float(prices[-1] - prices[0]) / sc.N_ROWS_DAILY
        closes = np.asarray(md["closes"], dtype=np.float64)
        if closes.size < PROFILE_MINUTES + 2 or cell <= 0 or row <= 0:
            continue
        out.append(Day(d, prices, z, closes, cell, row))
    return out


# --------------------------------------------------------------------------- #
# 水準の構成
# --------------------------------------------------------------------------- #
def _runs(mask: "np.ndarray") -> "list[tuple[int, int]]":
    """bool 列の連続 True 区間 [(start, stop), ...]（半開）。"""
    if not mask.any():
        return []
    idx = np.flatnonzero(mask)
    brk = np.flatnonzero(np.diff(idx) > 1)
    starts = np.concatenate([[idx[0]], idx[brk + 1]])
    stops = np.concatenate([idx[brk], [idx[-1]]]) + 1
    return list(zip(starts.tolist(), stops.tolist()))


def real_peaks(d: Day, z_thr: float) -> "np.ndarray":
    """z>=z_thr の連続塊ごとに 1 本（塊内 argmax z の行価格）。表示上の「バー」の単位。"""
    return np.array(
        [d.row_price[a + int(np.argmax(d.z[a:b]))] for a, b in _runs(d.z >= z_thr)],
        dtype=np.float64,
    )


def real_cells(d: Day, z_thr: float) -> "np.ndarray":
    """z>=z_thr の全セル（Step9 と同一定義・再現用）。"""
    return d.row_price[d.z >= z_thr]


def fake_a_cells(d: Day, z_max: float = NULL_A_Z_MAX) -> "np.ndarray":
    """同日の低 z セル全件（Step9 の偽水準 A）。"""
    return d.row_price[d.z <= z_max]


def placebo_levels(d: Day, z_thr: float, rows: float = PLACEBO_ROWS) -> "np.ndarray":
    """本物ピークを ±rows 行ずらした偽水準。ずらし先が高 z セルなら棄却する。"""
    peaks = real_peaks(d, z_thr)
    if peaks.size == 0:
        return peaks
    off = d.row_width * rows
    cand = np.concatenate([peaks - off, peaks + off])
    # ずらし先の z を最近傍セルから引く（グリッド外は z=-inf 扱い＝採用）。
    j = np.clip(np.searchsorted(d.row_price, cand), 0, d.row_price.size - 1)
    zc = d.z[j]
    keep = zc < z_thr
    return cand[keep]


def z_band_cells(d: Day, lo: float, hi: float) -> "np.ndarray":
    """z が [lo, hi) にある全セル（z 連続体スキャン用）。"""
    return d.row_price[(d.z >= lo) & (d.z < hi)]


LEVEL_BUILDERS = {
    "real_peak": lambda d, z: real_peaks(d, z),
    "real_cell": lambda d, z: real_cells(d, z),
    "placebo": lambda d, z: placebo_levels(d, z),
    "fake_a": lambda d, z: fake_a_cells(d),
}


def build_levels(name: str, d: Day, z_thr: float) -> "np.ndarray":
    """群名 → 水準列。``zb:<lo>:<hi>`` は z 帯スキャン群（lo<=z<hi の全セル）。"""
    if name.startswith("zb:"):
        _, lo, hi = name.split(":")
        return z_band_cells(d, float(lo), float(hi))
    return LEVEL_BUILDERS[name](d, z_thr)


# --------------------------------------------------------------------------- #
# 事件収集
# --------------------------------------------------------------------------- #
@dataclass
class Acc:
    """群 × 方向ごとの日次集計。"""

    n: "dict[int, int]" = field(default_factory=dict)
    bounce: "dict[int, int]" = field(default_factory=dict)
    cont: "dict[int, int]" = field(default_factory=dict)
    end_sum: "dict[int, float]" = field(default_factory=dict)
    mre_sum: "dict[int, float]" = field(default_factory=dict)
    prof_sum: "dict[int, np.ndarray]" = field(default_factory=dict)

    def add(self, day: int, bounce, cont, end, mre, prof) -> None:
        m = int(bounce.size)
        if m == 0:
            return
        self.n[day] = self.n.get(day, 0) + m
        self.bounce[day] = self.bounce.get(day, 0) + int(bounce.sum())
        self.cont[day] = self.cont.get(day, 0) + int(cont.sum())
        self.end_sum[day] = self.end_sum.get(day, 0.0) + float(end.sum())
        self.mre_sum[day] = self.mre_sum.get(day, 0.0) + float(mre.sum())
        cur = self.prof_sum.get(day)
        s = prof.sum(axis=0)
        self.prof_sum[day] = s if cur is None else cur + s


def merge(*accs: Acc) -> Acc:
    """複数 :class:`Acc` を日キーで足し合わせる（方向合算＝Step9 再現用）。"""
    out = Acc()
    for a in accs:
        for d, n in a.n.items():
            out.n[d] = out.n.get(d, 0) + n
            out.bounce[d] = out.bounce.get(d, 0) + a.bounce[d]
            out.cont[d] = out.cont.get(d, 0) + a.cont[d]
            out.end_sum[d] = out.end_sum.get(d, 0.0) + a.end_sum[d]
            out.mre_sum[d] = out.mre_sum.get(d, 0.0) + a.mre_sum[d]
            cur = out.prof_sum.get(d)
            out.prof_sum[d] = a.prof_sum[d] if cur is None else cur + a.prof_sum[d]
    return out


def _signed_profile(closes: "np.ndarray", idx: "np.ndarray", lv: "np.ndarray",
                    from_above: "np.ndarray", row: float, horizon: int) -> "np.ndarray":
    """接触後 t=0..horizon 分の符号付き変位（逆行＝正・行単位）。末尾は最終値で保持。"""
    M = closes.size
    t = np.arange(horizon + 1)
    j = np.minimum(idx[:, None] + t[None, :], M - 1)
    disp = closes[j] - lv[:, None]
    s = np.where(from_above, 1.0, -1.0)[:, None]
    return disp * s / row


def collect(
    days: "list[Day]",
    *,
    groups: "tuple[str, ...]",
    z_thr: float,
    lookback: int,
    k: int,
    x: float,
    naked: bool = True,
    horizon: int = PROFILE_MINUTES,
) -> "dict[tuple[str, str], Acc]":
    """群 × 方向（``sup``＝上から接近 / ``res``＝下から接近）の日次集計を返す。"""
    levels = {g: [build_levels(g, d, z_thr) for d in days] for g in groups}
    touched = {g: [np.zeros(a.size, dtype=bool) for a in levels[g]] for g in groups}
    acc: "dict[tuple[str, str], Acc]" = {(g, s): Acc() for g in groups for s in ("sup", "res")}

    for i, d in enumerate(days):
        path = sc.make_path(d.closes, d.cell_width, d.row_width, d.day)
        win_hi, win_lo = sc.window_extremes(d.closes, k)
        tol = d.cell_width / 2.0
        lo, hi = path.lo - tol, path.hi + tol
        j0 = max(0, i - lookback)
        for g in groups:
            cand_lv: "list[np.ndarray]" = []
            cand_src: "list[tuple[int, np.ndarray]]" = []
            for j in range(j0, i):
                arr = levels[g][j]
                if arr.size == 0:
                    continue
                sel = (arr >= lo) & (arr <= hi)
                if naked:
                    sel &= ~touched[g][j]
                if not sel.any():
                    continue
                pos = np.flatnonzero(sel)
                cand_lv.append(arr[pos])
                cand_src.append((j, pos))
            if not cand_lv:
                continue
            lv = np.concatenate(cand_lv)
            idx = sc.first_touch_many(path, lv)
            if naked:  # 接触した水準は以後 naked でなくなる
                off = 0
                for (j, pos) in cand_src:
                    n = pos.size
                    hitm = idx[off:off + n] >= 0
                    touched[g][j][pos[hitm]] = True
                    off += n
            r = sc.measure(path, lv, idx, k=k, x=x, win_hi=win_hi, win_lo=win_lo)
            if r is None:
                continue
            prof = _signed_profile(d.closes, r.idx, r.level, r.from_above, d.row_width, horizon)
            for s, m in (("sup", r.from_above), ("res", ~r.from_above)):
                if m.any():
                    acc[(g, s)].add(d.day, r.bounce[m], r.cont[m], r.end[m], r.mre[m], prof[m])
    return acc


# --------------------------------------------------------------------------- #
# 推論（日単位クラスタ・対）
# --------------------------------------------------------------------------- #
def _boot_p(diffs: "np.ndarray", seed: int = 42, B: int = 5000) -> float:
    """日次差系列の平均 = 0 に対する両側 p（定常ブートストラップ・自動ブロック長）。"""
    n = diffs.size
    if n < 10:
        return float("nan")
    blk = max(1, pw_block_len(diffs.reshape(-1, 1)))
    rng = np.random.default_rng(seed)
    c = diffs - diffs.mean()
    obs = float(diffs.mean())
    stat = np.empty(B)
    for b in range(B):
        stat[b] = c[stationary_bootstrap_indices(n, blk, rng)].mean()
    p = float((np.abs(stat) >= abs(obs)).mean())
    return max(p, 1.0 / B)


def _day_values(a: Acc, d: int, metric: str) -> float:
    n = a.n[d]
    if metric == "bounce":
        return a.bounce[d] / n
    if metric == "cont":
        return a.cont[d] / n
    if metric == "end":
        return a.end_sum[d] / n
    if metric == "mre":
        return a.mre_sum[d] / n
    raise ValueError(metric)


def paired_fe(a: Acc, b: Acc, *, metric: str = "bounce", seed: int = 42,
              B: int = 5000) -> dict:
    """接触日固定効果 OLS（本物ダミー）＋ 日クラスタ頑健分散。

    事件 i の結果 y_i を、接触日ダミー（固定効果）と「本物か」ダミー D_i に回帰する。
    2 値回帰子＋日 FE では推定量が日次集計だけで閉形式になる:
        w_d = n1_d·n0_d/(n1_d+n0_d),  β = Σ w_d·(ȳ1_d − ȳ0_d) / Σ w_d
    クラスタ頑健分散（日 = クラスタ）も同じ集計から厳密に得られる:
        V(β) = Σ w_d²(diff_d − β)² / (Σ w_d)²
    Step9 の等重み対検定と違い、片群 1 件の日も捨てず、件数に応じて重み付けする
    （＝有効標本は日だが、日内情報量を活かす）。p 値は日系列の定常ブートストラップ。
    """
    days = sorted(set(a.n) & set(b.n))
    w, diff = [], []
    for d in days:
        n1, n0 = a.n[d], b.n[d]
        if n1 < 1 or n0 < 1:
            continue
        w.append(n1 * n0 / (n1 + n0))
        diff.append(_day_values(a, d, metric) - _day_values(b, d, metric))
    w = np.asarray(w, dtype=float)
    dd = np.asarray(diff, dtype=float)
    out: dict = {"metric": metric, "n_days": int(dd.size),
                 "n_events_real": int(sum(a.n[d] for d in days)),
                 "n_events_ctrl": int(sum(b.n[d] for d in days))}
    if dd.size < 10 or w.sum() <= 0:
        out.update(beta=float("nan"), se=float("nan"), t=float("nan"),
                   p_boot=float("nan"), ci=[float("nan")] * 2)
        return out
    beta = float((w * dd).sum() / w.sum())
    var = float((w ** 2 * (dd - beta) ** 2).sum() / (w.sum() ** 2))
    se = math.sqrt(var)
    out.update(beta=beta, se=se, t=(beta / se) if se > 0 else float("nan"),
               ci=[beta - 1.96 * se, beta + 1.96 * se],
               positive_share=float((dd > 0).mean()))
    # 日系列の定常ブートストラップ（系列相関を許す）で H0: β=0 の両側 p。
    n = dd.size
    blk = max(1, pw_block_len(dd.reshape(-1, 1)))
    rng = np.random.default_rng(seed)
    c = dd - beta
    stat = np.empty(B)
    for bi in range(B):
        ix = stationary_bootstrap_indices(n, blk, rng)
        ww, cc = w[ix], c[ix]
        stat[bi] = (ww * cc).sum() / ww.sum()
    out["p_boot"] = max(float((np.abs(stat) >= abs(beta)).mean()), 1.0 / B)
    out["block_len"] = int(blk)
    return out


def day_mean_by_group(accs: "dict[str, Acc]", metric: str = "bounce",
                      min_events: int = 1) -> dict:
    """群ごとの「日内平均を日で等重み平均した値」＋ 日デミーン値（z 連続体の図用）。

    各接触日について全群の日内平均を取り、その日の**群横断平均**を引いてから日で平均する
    （日ごとの地合い＝当日ボラ・トレンドを除去する。共通日のみ使う）。
    """
    names = list(accs)
    all_days = sorted({d for g in names for d in accs[g].n})
    # その日の基準線 = 全群の全事件を込みにした事件加重平均（どの日にも定義される）。
    base: "dict[int, float]" = {}
    for d in all_days:
        num = den = 0.0
        for g in names:
            if d in accs[g].n:
                num += _day_values(accs[g], d, metric) * accs[g].n[d]
                den += accs[g].n[d]
        if den > 0:
            base[d] = num / den
    out = {}
    for g in names:
        days = [d for d, n in accs[g].n.items() if n >= min_events and d in base]
        if len(days) < 10:
            out[g] = {"n_days": len(days)}
            continue
        raw = np.array([_day_values(accs[g], d, metric) for d in days])
        dem = raw - np.array([base[d] for d in days])
        se = float(dem.std(ddof=1) / math.sqrt(dem.size))
        out[g] = {
            "n_days": len(days),
            "n_events": int(sum(accs[g].n[d] for d in days)),
            "raw_mean": float(raw.mean()),
            "demeaned": float(dem.mean()),
            "se": se,
            "t": float(dem.mean() / se) if se > 0 else float("nan"),
        }
    return out


def paired(a: Acc, b: Acc, *, min_events: int = 3, metric: str = "bounce",
           seed: int = 42) -> dict:
    """同一接触日で対にした「本物 − 対照」の日次差を推論する。"""
    days = sorted(set(a.n) & set(b.n))
    diffs, wts = [], []
    for d in days:
        na, nb = a.n[d], b.n[d]
        if na < min_events or nb < min_events:
            continue
        if metric == "bounce":
            va, vb = a.bounce[d] / na, b.bounce[d] / nb
        elif metric == "cont":
            va, vb = a.cont[d] / na, b.cont[d] / nb
        elif metric == "end":
            va, vb = a.end_sum[d] / na, b.end_sum[d] / nb
        elif metric == "mre":
            va, vb = a.mre_sum[d] / na, b.mre_sum[d] / nb
        else:
            raise ValueError(metric)
        diffs.append(va - vb)
        wts.append(min(na, nb))
    dd = np.asarray(diffs, dtype=float)
    out: dict = {"metric": metric, "n_days_paired": int(dd.size)}
    if dd.size < 10:
        out.update(mean_diff=float("nan"), t=float("nan"), p_boot=float("nan"),
                   p_sign=float("nan"), positive_share=float("nan"), ci=(float("nan"),) * 2)
        return out
    m = float(dd.mean())
    se = float(dd.std(ddof=1) / math.sqrt(dd.size))
    out.update(
        mean_diff=m,
        t=(m / se) if se > 0 else float("nan"),
        se=se,
        ci=(m - 1.96 * se, m + 1.96 * se),
        positive_share=float((dd > 0).mean()),
        p_sign=sign_test_pvalue(int((dd > 0).sum()), int((dd < 0).sum())),
        p_boot=_boot_p(dd, seed=seed),
    )
    return out


def pooled(a: Acc) -> dict:
    """群の素のプール率（参考値。推論には使わない）。"""
    n = sum(a.n.values())
    return {
        "events": int(n),
        "bounce_rate": (sum(a.bounce.values()) / n) if n else float("nan"),
        "cont_rate": (sum(a.cont.values()) / n) if n else float("nan"),
        "end_mean_rows": (sum(a.end_sum.values()) / n) if n else float("nan"),
        "mre_mean_rows": (sum(a.mre_sum.values()) / n) if n else float("nan"),
        "days": int(len(a.n)),
    }


def profile_mean(a: Acc, days: "list[int] | None" = None) -> "np.ndarray":
    """日次平均プロファイルの日間平均（日を等重みにする）。"""
    keys = sorted(a.prof_sum) if days is None else [d for d in days if d in a.prof_sum]
    if not keys:
        return np.zeros(PROFILE_MINUTES + 1)
    rows = np.stack([a.prof_sum[d] / a.n[d] for d in keys])
    return rows.mean(axis=0)
