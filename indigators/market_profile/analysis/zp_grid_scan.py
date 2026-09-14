"""zp_grid_scan — bp 相対格子（log 一様格子）の無次元校正スキャン（ISSUE-079）。

目的:
    zp の格子幅を絶対 pt（GRID_W=10）から価格比（bp）へ再設計するにあたり、
    「統計が成立する最小セルあたり分数」（時間不変の無次元定数）を実測で確定し、
    そこから bp 幅を導出する。絶対 pt は価格水準に追従せず（本データ内で日経約 8 倍）、
    現在データへの過剰適合になる（依頼者指摘・ISSUE-079）。

格子の定義（bp 相対 = log 一様）:
    セル index k = floor(ln(price) / w_log)、w_log = ln(1 + bp/10^4)。
    隣接セル中心の価格比は一定 exp(w_log)＝セルの価格幅が価格に比例（相対格子）。
    log 空間の**絶対**一様格子なので、跨日 Σobs/Σmean/Σvar の窓合算は現行の絶対 pt 格子と
    同型に成立する（グリッド共有・ISSUE-079 設計の核心）。

判定（Step2c の流儀＝シミュレーション校正・基準は「現行からの相対劣化」）:
    単日 z の計数分布は正規裾を持たない（占有カウントは歪む）ため、名目 N(0,1) 裾との
    絶対比較は不適切（実測: 現行 10pt 自身の FPR(z>=3)≈2%）。よって基準を
    「同一評価系での現行 10pt（絶対格子・production 同等）の FPR」に置き、
    合格条件（事前固定・レポートに明記）:
      (1) FPR(z>=3, w) <= 1.25 × FPR(z>=3, 現行10pt・同期間)（相対劣化 25% 以内）
      (2) 定義セル率（var>0）>= 0.95
    合格最細幅でのセルあたり分数（中央値）＝求める無次元定数。推奨 bp は
    高価格期（直近）と低価格期（2013）の**両方**で合格する最細 bp。

実行（実データ・要 api パス）:
    PYTHONPATH=.../indicator_ui/api:.../market_profile/api python zp_grid_scan.py \
        --days 60 --m-null 800 --m-surr 200
"""
from __future__ import annotations

import math

import numpy as np

# CHUNK は production（market_profile_zp）と同値の逐次幅（メモリ有界・決定論の消費順）。
_CHUNK = 2000


def bp_to_wlog(bp: float) -> float:
    """bp（1e-4 比率）→ log 格子幅 w_log = ln(1 + bp/10^4)。"""
    return math.log1p(float(bp) / 1e4)


def _default_brackets(G: int) -> "np.ndarray":
    """既定のブラケット写像（30 分・セッション始端起点）。実データ CLI は production の
    _B_OF_MINUTE を渡す（規則の単一情報源は production 側）。"""
    return (np.arange(G) // 30).astype(np.int32)


def obs_cell_counts_log(
    closes: "np.ndarray", klo: int, khi: int, wlog: float,
    *, col_lo: int = 0, col_hi: "int | None" = None,
) -> "np.ndarray":
    """観測の行占有分数 N_obs(k)（log 格子版・production obs_cell_counts の bp 相対版）。"""
    c = np.asarray(closes, dtype=float)[col_lo:(None if col_hi is None else col_hi)]
    k = np.clip(np.floor(np.log(c) / float(wlog)).astype(np.int64) - int(klo),
                0, int(khi) - int(klo))
    return np.bincount(k, minlength=int(khi) - int(klo) + 1).astype(float)


def null_b_moments_log(
    S: "np.ndarray",
    open_d: float,
    klo: int,
    khi: int,
    wlog: float,
    *,
    rng,
    m_reps: int,
    b_of_minute: "np.ndarray | None" = None,
) -> "tuple[np.ndarray, np.ndarray]":
    """log 格子版 Null B モーメント（production null_b_moments_abs の bp 相対版）。

    サロゲート log 価格 = log(open) + cumsum(S 行のブラケット別リサンプル)。絶対格子版が
    exp してから floor(price/GRID_W) するのに対し、log 格子は log のまま floor(x/wlog)
    ＝指数関数すら不要（数値的にも安価）。アルゴリズム（ブラケット別・日跨ぎ・レンジ外棄却・
    チャンク逐次）は production と同一。
    """
    L, G = S.shape
    b = _default_brackets(G) if b_of_minute is None else np.asarray(b_of_minute)
    kb = int(b.max()) + 1
    C = int(khi) - int(klo) + 1
    ssum = np.zeros(C)
    ssq = np.zeros(C)
    log_open = math.log(float(open_d))
    col = np.arange(G)[None, :]
    done = 0
    while done < m_reps:
        m = min(_CHUNK, m_reps - done)
        days = rng.integers(0, L, size=(m, kb))
        s_surr = S[days[:, b], col]
        logp = log_open + np.cumsum(s_surr, axis=1)
        idx = np.floor(logp / float(wlog)).astype(np.int64) - int(klo)
        valid = (idx >= 0) & (idx < C)
        flat = (idx + np.arange(m)[:, None] * C)[valid]
        counts = np.bincount(flat, minlength=m * C).reshape(m, C).astype(float)
        ssum += counts.sum(axis=0)
        ssq += (counts ** 2).sum(axis=0)
        done += m
    mean = ssum / m_reps
    var = np.maximum(ssq / m_reps - mean ** 2, 0.0)
    return mean, var


def fpr_of_surrogates(
    S: "np.ndarray",
    open_d: float,
    klo: int,
    khi: int,
    wlog: float,
    mean: "np.ndarray",
    var: "np.ndarray",
    *,
    rng,
    m_surr: int,
    z_thr: float = 3.0,
    b_of_minute: "np.ndarray | None" = None,
) -> dict:
    """帰無サロゲート自身への z 適用による偽陽性率（校正チェックの心臓部）。

    帰無が正しく較正されていれば、サロゲート占有の z は近似的に N(0,1) 以下の裾を持ち、
    FPR(z>=3) は名目 0.00135 近傍に収まる。格子が細かすぎて計数が退化（0/1 化・var 過小）
    すると z が離散スパイク化し FPR が膨張する＝「統計が成立しない」の操作的定義。

    Returns: {fpr, exceed, cells, defined_share, minutes_per_cell_median}。
    """
    L, G = S.shape
    b = _default_brackets(G) if b_of_minute is None else np.asarray(b_of_minute)
    kb = int(b.max()) + 1
    C = int(khi) - int(klo) + 1
    log_open = math.log(float(open_d))
    col = np.arange(G)[None, :]
    sd = np.sqrt(var)
    defined = sd > 0
    exceed = 0
    cells = 0
    done = 0
    occ_all: list[np.ndarray] = []
    while done < m_surr:
        m = min(_CHUNK, m_surr - done)
        days = rng.integers(0, L, size=(m, kb))
        s_surr = S[days[:, b], col]
        logp = log_open + np.cumsum(s_surr, axis=1)
        idx = np.floor(logp / float(wlog)).astype(np.int64) - int(klo)
        valid = (idx >= 0) & (idx < C)
        flat = (idx + np.arange(m)[:, None] * C)[valid]
        counts = np.bincount(flat, minlength=m * C).reshape(m, C).astype(float)
        with np.errstate(invalid="ignore", divide="ignore"):
            z = (counts - mean[None, :]) / sd[None, :]
        z = z[:, defined]
        exceed += int((z >= z_thr).sum())
        cells += int(z.size)
        occ = counts[:, defined]
        occ_all.append(occ[occ > 0])
        done += m
    occ_cat = np.concatenate(occ_all) if occ_all else np.array([0.0])
    return {
        "fpr": (exceed / cells) if cells else float("nan"),
        "exceed": exceed,
        "cells": cells,
        "defined_share": float(defined.mean()) if C else 0.0,
        "minutes_per_cell_median": float(np.median(occ_cat)),
    }


# --------------------------------------------------------------------------- #
# 実データ CLI（production の mgrid/hist/セッション窓を再利用・read-only）
# --------------------------------------------------------------------------- #
def _run_real_scan(days_per_era: int, m_null: int, m_surr: int, bp_list, out_path):
    import sys
    import time
    from pathlib import Path

    here = Path(__file__).resolve()
    sys.path.insert(0, str(here.parents[2] / "indicator_ui" / "api"))
    sys.path.insert(0, str(here.parents[1] / "api"))
    from market_profile_api.compute import market_profile_zp as zp  # noqa: E402
    from marketdata.session_day import session_day_start, next_session_day_start  # noqa: E402

    now = time.time()
    rel_tol = 1.25   # 現行 10pt 基準からの許容劣化（FPR 比）。
    pass_defined = 0.95

    def era_days(anchor: float, n: int) -> list:
        out = []
        d = session_day_start(anchor)
        while len(out) < n:
            if next_session_day_start(d) <= now and zp._mgrid_of_day("JP225", d, now) is not None:
                out.append(d)
            d = session_day_start(d - 1)
        return out

    eras = {
        "recent(高価格)": era_days(now - 3 * 86400, days_per_era),
        "2013(低価格)": era_days(1380000000, days_per_era),  # 2013-09 起点で遡ium（帰無250日確保のため2012年初は避ける）
    }
    lines = ["# zp 格子幅 校正スキャン（ISSUE-079）",
             f"- 判定: FPR(z>=3, w) <= {rel_tol} × FPR(現行10pt・同期間) かつ 定義セル率 >= {pass_defined}",
             "  （単日 z の計数分布は正規裾でないため絶対名目でなく現行基準からの相対劣化で判定）",
             f"- days/era={days_per_era} m_null={m_null} m_surr={m_surr}",
             ""]
    verdict: dict[str, dict] = {}
    for era, dlist in eras.items():
        lines.append(f"## {era}（{len(dlist)}セッション）")
        lines.append("| 幅 | FPR(z≥3) | 定義セル率 | 分/セル中央値 | 秒/日 |")
        lines.append("|---|---|---|---|---|")
        for label, wlog in [("10pt(現行絶対)", None)] + [(f"{bp}bp", bp_to_wlog(bp)) for bp in bp_list]:
            t0 = time.time()
            agg = {"exceed": 0, "cells": 0, "def": [], "mpc": []}
            for d in dlist:
                grid = zp._mgrid_of_day("JP225", d, now)
                S = zp._hist_step_matrix("JP225", d, now)
                if grid is None or S is None:
                    continue
                closes, open_d = grid
                rng = np.random.default_rng(zp.day_seed("JP225", int(d)) ^ 0x0BB0)
                if wlog is None:
                    # 絶対 10pt（現行）: 同一評価系で FPR を測る（era 間ドリフトの提示用）。
                    klo = int(np.floor(closes.min() / zp.GRID_W))
                    khi = int(np.floor(closes.max() / zp.GRID_W))
                    mean, var = zp.null_b_moments_abs(S, open_d, klo, khi, rng=rng, m_reps=m_null)
                    w_eff = np.log((klo + 1) * zp.GRID_W) - np.log(klo * zp.GRID_W)
                    res = _fpr_abs(S, open_d, klo, khi, mean, var, rng=rng, m_surr=m_surr,
                                   b_of_minute=zp._B_OF_MINUTE, grid_w=zp.GRID_W)
                else:
                    lg = np.log(closes)
                    klo = int(np.floor(lg.min() / wlog))
                    khi = int(np.floor(lg.max() / wlog))
                    mean, var = null_b_moments_log(S, open_d, klo, khi, wlog, rng=rng,
                                                   m_reps=m_null, b_of_minute=zp._B_OF_MINUTE)
                    res = fpr_of_surrogates(S, open_d, klo, khi, wlog, mean, var, rng=rng,
                                            m_surr=m_surr, b_of_minute=zp._B_OF_MINUTE)
                agg["exceed"] += res["exceed"]
                agg["cells"] += res["cells"]
                agg["def"].append(res["defined_share"])
                agg["mpc"].append(res["minutes_per_cell_median"])
            fpr = agg["exceed"] / agg["cells"] if agg["cells"] else float("nan")
            dsh = float(np.mean(agg["def"])) if agg["def"] else 0.0
            mpc = float(np.median(agg["mpc"])) if agg["mpc"] else 0.0
            if label.startswith("10pt"):
                baseline_fpr = fpr  # 同期間の現行基準（以降の幅はこれと比較）。
                ok = dsh >= pass_defined
            else:
                ok = (fpr <= rel_tol * baseline_fpr) and (dsh >= pass_defined)
            sec = (time.time() - t0) / max(1, len(dlist))
            lines.append(f"| {label} | {fpr:.5f}{'✓' if ok else '✗'} | {dsh:.3f} | {mpc:.1f} | {sec:.2f} |")
            verdict.setdefault(label, {})[era] = ok
            print(lines[-1], flush=True)
    both = [lbl for lbl, ok in verdict.items() if all(ok.values()) and lbl.endswith("bp")]
    finest = both[0] if both else None  # bp_list 昇順前提＝先頭が最細の合格。
    lines.append("")
    lines.append(f"## 判定: 両期合格の最細 bp = {finest}")
    Path(out_path).write_text("\n".join(lines))
    print(f"written: {out_path}", flush=True)


def _fpr_abs(S, open_d, klo, khi, mean, var, *, rng, m_surr, b_of_minute, grid_w, z_thr=3.0):
    """絶対格子（現行 GRID_W）での FPR（比較基準用・fpr_of_surrogates の絶対版）。"""
    L, G = S.shape
    b = np.asarray(b_of_minute)
    kb = int(b.max()) + 1
    C = int(khi) - int(klo) + 1
    log_open = math.log(float(open_d))
    col = np.arange(G)[None, :]
    sd = np.sqrt(var)
    defined = sd > 0
    exceed = cells = 0
    occ_all = []
    done = 0
    while done < m_surr:
        m = min(_CHUNK, m_surr - done)
        days = rng.integers(0, L, size=(m, kb))
        prices = np.exp(log_open + np.cumsum(S[days[:, b], col], axis=1))
        idx = np.floor(prices / grid_w).astype(np.int64) - int(klo)
        valid = (idx >= 0) & (idx < C)
        flat = (idx + np.arange(m)[:, None] * C)[valid]
        counts = np.bincount(flat, minlength=m * C).reshape(m, C).astype(float)
        with np.errstate(invalid="ignore", divide="ignore"):
            z = (counts - mean[None, :]) / sd[None, :]
        z = z[:, defined]
        exceed += int((z >= z_thr).sum())
        cells += int(z.size)
        occ = counts[:, defined]
        occ_all.append(occ[occ > 0])
        done += m
    occ_cat = np.concatenate(occ_all) if occ_all else np.array([0.0])
    return {"fpr": (exceed / cells) if cells else float("nan"), "exceed": exceed, "cells": cells,
            "defined_share": float(defined.mean()), "minutes_per_cell_median": float(np.median(occ_cat))}


if __name__ == "__main__":
    import argparse
    from pathlib import Path

    ap = argparse.ArgumentParser(description="zp bp 相対格子の無次元校正スキャン（ISSUE-079）")
    ap.add_argument("--days", type=int, default=60)
    ap.add_argument("--m-null", type=int, default=800)
    ap.add_argument("--m-surr", type=int, default=200)
    ap.add_argument("--bp", type=float, nargs="*", default=[1.5, 2, 3, 5, 8, 12])
    ap.add_argument("--out", default=str(Path(__file__).parent / "out" / "zp_grid_scan.md"))
    a = ap.parse_args()
    _run_real_scan(a.days, a.m_null, a.m_surr, sorted(a.bp), a.out)
