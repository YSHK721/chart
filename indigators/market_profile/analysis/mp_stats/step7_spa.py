"""Step7: データスヌーピング補正 — H0「全ルールが無効」を SPA（Hansen 2005）で検定。

Step6 型のエッジ（VA 外寄り付き → 翌日 RV 分布が変わる）は、構成パラメータの選択自由度
そのものが偽エッジを生む。仕様の自由度 7 次元をルール格子として**すべて検定対象集合に含め**、
最良ルールの見かけの成績が選択効果で説明できるかを一括補正する。

ルール格子（各次元・計 216 ルール）:
  - ブラケット長: 30 分 / 60 分（隣接ブラケットのプール）
  - row size: 20 / 40 / 80 行（日レンジ適応）
  - VA%: 0.60 / 0.70 / 0.80
  - POC 同点処理: 日中間値寄り / 低価格側（既存 _value_area の 2 規約）
  - 窓長 n: 1 / 2 / 5 日（VA を直近 n 日プール TPO から作る）
  - セッション境界（参照価格）: 翌日 open / 当日 close（VA 外判定の被比較価格）

性能指標 f_{k,t}（ルール k・日 t）:
  ウォークフォワード 1 歩先の ln RV 予測損失差
    f_{k,t} = (y_t − ŷ^HAR_t)² − (y_t − ŷ^{HAR+dummy_k}_t)²
  （正 = ルールがベンチマーク HAR を改善）。推定はルックアヘッドなしの expanding
  window（ウォームアップ WARMUP 日・毎日再推定）。

判定: HansenSpa.spa_pvalue（studentized max・consistent 再センタリング・定常ブート）
  p < α → reject（選択効果を補正してもなお有効なルールが存在する）。
"""

from __future__ import annotations

from dataclasses import dataclass
from itertools import product

import numpy as np

from . import stats_core as sc  # noqa: F401  (sys.path 挿入の副作用・simulator 解決)
from .data_prep import DailyFeatures
from .report import StepResult
from .step3_incremental_r2 import HAR_LAGS, _rolling_mean

from simulator.adapter.validation.spa import HansenSpa  # noqa: E402

N_ROWS_CHOICES = (20, 40, 80)
VA_CHOICES = (0.60, 0.70, 0.80)
BRACKET_CHOICES = (30, 60)      # 分。60 は隣接 30 分ブラケットのプール。
WINDOW_CHOICES = (1, 2, 5)      # VA を作る直近 n 日（t 日の判定には t-n..t-1 日を使う＝因果）。
TIE_CHOICES = ("mid", "low")    # POC/VA 同点時の decision 規約。
BOUNDARY_CHOICES = ("open_next", "close_same")  # VA 外判定の被比較価格。
WARMUP = 500                    # ウォークフォワードのウォームアップ日数。


@dataclass(frozen=True)
class Rule:
    bracket_min: int
    n_rows: int
    va_pct: float
    tie: str
    window: int
    boundary: str

    @property
    def key(self) -> str:
        return (f"b{self.bracket_min}_r{self.n_rows}_va{int(self.va_pct * 100)}"
                f"_{self.tie}_w{self.window}_{self.boundary}")


def rule_grid() -> "list[Rule]":
    return [
        Rule(b, r, v, t, w, bd)
        for b, r, v, t, w, bd in product(
            BRACKET_CHOICES, N_ROWS_CHOICES, VA_CHOICES,
            TIE_CHOICES, WINDOW_CHOICES, BOUNDARY_CHOICES,
        )
    ]


# --------------------------------------------------------------------------- #
# ルール別 VA 帯（因果・t 日の VA は t-window..t-1 日の TPO から）
# --------------------------------------------------------------------------- #
def _pool_brackets(hi: "np.ndarray", lo: "np.ndarray", bracket_min: int):
    """(D,K) の 30 分ブラケット hi/lo を bracket_min（30/60）へプールする。"""
    if bracket_min == 30:
        return hi, lo
    K = hi.shape[1]
    k2 = K // 2
    h2 = np.fmax(hi[:, : 2 * k2 : 2], hi[:, 1 : 2 * k2 : 2])
    l2 = np.fmin(lo[:, : 2 * k2 : 2], lo[:, 1 : 2 * k2 : 2])
    if K % 2:  # 端数ブラケットはそのまま 1 本として付ける。
        h2 = np.concatenate([h2, hi[:, -1:]], axis=1)
        l2 = np.concatenate([l2, lo[:, -1:]], axis=1)
    return h2, l2


def rule_va_bounds(f: DailyFeatures, rule: Rule) -> "tuple[np.ndarray, np.ndarray]":
    """日 t の判定に使う VA 帯 (va_lo[t], va_hi[t])。t-window..t-1 日の TPO プール（因果）。

    タイ規約 tie='mid' は VA 選択の同点で日中間値寄りの行、'low' は低価格側の行を優先する
    （既存 _value_area の index 昇順規約＝low と、mid 寄り規約の 2 択）。
    """
    assert f.br_hi is not None and f.br_lo is not None
    hi_all, lo_all = _pool_brackets(f.br_hi, f.br_lo, rule.bracket_min)
    D = f.n_days
    va_lo = np.full(D, np.nan)
    va_hi = np.full(D, np.nan)
    for t in range(rule.window, D):
        days = slice(t - rule.window, t)
        hi = hi_all[days].ravel()
        lo = lo_all[days].ravel()
        ok = np.isfinite(hi) & np.isfinite(lo)
        if not np.any(ok):
            continue
        hi, lo = hi[ok], lo[ok]
        w_hi = float(np.max(f.day_high[days]))
        w_lo = float(np.min(f.day_low[days]))
        span = w_hi - w_lo
        row_w = span / rule.n_rows if span > 0 else 1.0
        row_lo = w_lo + np.arange(rule.n_rows) * row_w
        row_hi = row_lo + row_w
        cover = (lo[:, None] <= row_hi[None, :]) & (hi[:, None] >= row_lo[None, :])
        tpo = cover.sum(axis=0).astype(float)
        total = tpo.sum()
        if total <= 0:
            continue
        centers = row_lo + row_w / 2.0
        mid = (w_hi + w_lo) / 2.0
        if rule.tie == "mid":
            order = sorted(range(rule.n_rows), key=lambda i: (-tpo[i], abs(centers[i] - mid)))
        else:
            order = sorted(range(rule.n_rows), key=lambda i: (-tpo[i], i))
        cum = 0.0
        chosen = []
        for i in order:
            chosen.append(centers[i])
            cum += tpo[i]
            if cum >= total * rule.va_pct:
                break
        va_lo[t] = min(chosen)
        va_hi[t] = max(chosen)
    return va_lo, va_hi


def rule_dummy(f: DailyFeatures, rule: Rule) -> "np.ndarray":
    """1{参照価格が VA_t 外}（(D,) float・VA 未定義日は NaN）。ルックアヘッドなし。

    boundary='open_next': 日 t の open が t-window..t-1 の VA 外か（t 朝に判定可能）。
    boundary='close_same': 日 t-1 の close が同 VA 外か（t 朝に判定可能・因果）。
    """
    va_lo, va_hi = rule_va_bounds(f, rule)
    D = f.n_days
    ref = np.full(D, np.nan)
    if rule.boundary == "open_next":
        ref = f.o
    else:
        ref[1:] = f.c[:-1]
    out = ((ref < va_lo) | (ref > va_hi)).astype(float)
    out[~(np.isfinite(ref) & np.isfinite(va_lo))] = np.nan
    return out


# --------------------------------------------------------------------------- #
# ウォークフォワード損失差
# --------------------------------------------------------------------------- #
REFIT_EVERY = 5  # 係数の再推定間隔（日）。損失は毎日評価する（係数は最大 4 日 stale の近似）。


def walkforward_loss_diff(
    y: "np.ndarray", X0: "np.ndarray", dummies: "np.ndarray",
    *, warmup: int = WARMUP, refit_every: int = REFIT_EVERY,
) -> "tuple[np.ndarray, np.ndarray]":
    """(F, valid_t) — F[t,k] = ベンチ損失 − ルール k 損失（1 歩先・expanding）。

    y[t] は t 時点の目的（ln RV_t）、X0[t] は t の予測に使える HAR 特徴（t-1 まで由来）、
    dummies[t,k] はルール k の t 朝ダミー。行 t の推定は行 < t のみ使用（ルックアヘッドなし）。
    係数は refit_every 日ごとに再推定し（計算量削減・係数は緩慢にしか動かない）、
    予測誤差・損失は毎日評価する。NaN を含む行は学習・評価から除外する。
    """
    T, K = dummies.shape
    F = np.full((T, K), np.nan)
    base_ok = np.isfinite(y) & np.all(np.isfinite(X0), axis=1)
    beta0 = None
    beta1s: "list[np.ndarray | None]" = [None] * K
    for t in range(warmup, T):
        if beta0 is None or (t - warmup) % refit_every == 0:
            hist = np.flatnonzero(base_ok[: t])
            if hist.size < 100:
                continue
            beta0, *_ = np.linalg.lstsq(X0[hist], y[hist], rcond=None)
            for k in range(K):
                dk = dummies[:, k]
                ok = np.flatnonzero((base_ok & np.isfinite(dk))[: t])
                if ok.size < 100:
                    beta1s[k] = None
                    continue
                X1 = np.column_stack([X0[ok], dk[ok]])
                beta1s[k], *_ = np.linalg.lstsq(X1, y[ok], rcond=None)
        if beta0 is None or not base_ok[t]:
            continue
        e0 = y[t] - X0[t] @ beta0
        l0 = e0 * e0
        for k in range(K):
            b1 = beta1s[k]
            dk_t = dummies[t, k]
            if b1 is None or not np.isfinite(dk_t):
                continue
            e1 = y[t] - (X0[t] @ b1[:-1] + dk_t * b1[-1])
            F[t, k] = l0 - e1 * e1
    valid = np.all(np.isfinite(F), axis=1)
    return F[valid], np.flatnonzero(valid)


def build_har_arrays(f: DailyFeatures) -> "tuple[np.ndarray, np.ndarray]":
    """(y, X0) — y[t]=ln RV_t、X0[t]=HAR 特徴（t-1 までの y のみ・因果シフト済み）。"""
    y = np.where(f.rv_oc > 0, np.log(np.maximum(f.rv_oc, 1e-300)), np.nan)
    y5 = _rolling_mean(y, HAR_LAGS[1])
    y22 = _rolling_mean(y, HAR_LAGS[2])
    D = f.n_days
    X0 = np.full((D, 4), np.nan)
    X0[1:, 0] = 1.0
    X0[1:, 1] = y[:-1]
    X0[1:, 2] = y5[:-1]
    X0[1:, 3] = y22[:-1]
    return y, X0


def run_step7(
    f: DailyFeatures, *, seed: int, B: int = 5000, alpha: float = 0.05,
    rules: "list[Rule] | None" = None, warmup: int = WARMUP,
) -> "tuple[StepResult, dict]":
    """Step7 実行。Returns (StepResult, {"F", "t_index", "rules"})（Step8 が再利用）。"""
    rules = rule_grid() if rules is None else rules
    y, X0 = build_har_arrays(f)
    dummies = np.column_stack([rule_dummy(f, r) for r in rules])
    F, t_index = walkforward_loss_diff(y, X0, dummies, warmup=warmup)
    if F.shape[0] < 200:
        result = StepResult(
            step=7, name="spa_multi_rule", decision="fail_to_reject",
            statistics={"error": "insufficient_oos_days", "n": int(F.shape[0])},
            notes="ウォークフォワード有効日数が不足のため保守的に fail_to_reject。",
        )
        return result, {"F": F, "t_index": t_index, "rules": rules}

    p_spa = HansenSpa().spa_pvalue(F, seed=seed, B=B)
    fbar = F.mean(axis=0)
    best_k = int(np.argmax(fbar))
    reject = bool(p_spa < alpha)
    stats: "dict[str, object]" = {
        "p_spa": float(p_spa),
        "n_rules": len(rules),
        "n_days_evaluated": int(F.shape[0]),
        "B": B,
        "warmup": warmup,
        "best_rule": rules[best_k].key,
        "best_mean_loss_diff": float(fbar[best_k]),
        "n_rules_positive_mean": int(np.sum(fbar > 0)),
        "alpha": alpha,
    }
    notes = (
        "H0: 全ルール無効（自由度 7 次元・216 ルールを検定対象集合に包含）。"
        "f = ウォークフォワード 1 歩先の ln RV 予測損失差（HAR 基準・因果）。"
        "reject = 選択効果（データスヌーピング）を補正してもなお有効なルールが存在。"
        "fail_to_reject = 見かけのエッジは選択効果で説明可能 → Step8 は skipped。"
    )
    result = StepResult(
        step=7, name="spa_multi_rule",
        decision="reject" if reject else "fail_to_reject",
        statistics=stats, notes=notes,
    )
    return result, {"F": F, "t_index": t_index, "rules": rules}
