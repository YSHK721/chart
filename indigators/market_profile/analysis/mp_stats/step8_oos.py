"""Step8: OOS・校正 — IS で選抜した最良ルールを held-out で検証（Kupiec / Christoffersen）。

既存フロー（simulator/usecase/validate_strategy.py の S1-S6・IS/OOS floor 0.7 分割）と同じ枠組み:
  1. Step7 の損失差行列 F を時系列で IS（先頭 70%）/ OOS（末尾 30%）へ分割。
  2. IS 平均損失差が最大のルールを 1 本選抜（選択は IS のみ＝OOS は非接触）。
  3. OOS 評価:
     (a) 損失改善: OOS 平均損失差 > 0 か（HAC t・片側）。
     (b) 校正: 選抜ルール込みモデルの 1 歩先 ln RV 予測に対し、超過イベント
         1{ln RV_t > μ̂_t + q̂_{1−α_tail}}（期待発生率 α_tail=0.05）の OOS ヒット系列を
         Kupiec POF / Christoffersen 独立性（simulator VarBacktests 再利用）で検定。
         q̂ は IS 残差の**経験分位**（因果・分布仮定なし。ln RV は正規より右尾が軽く、
         正規分位 z·σ̂ ではバンドが過大→無ヒットで Kupiec が偽棄却するため）。
  判定: (a) の片側 p < α かつ Kupiec 非棄却（校正が壊れていない）→ reject（H0「OOS で無効」を棄却）。
"""

from __future__ import annotations

import math

import numpy as np

from . import stats_core as sc
from .data_prep import DailyFeatures
from .report import StepResult
from .step7_spa import Rule, build_har_arrays, rule_dummy

from simulator.adapter.validation.var_backtests import VarBacktests  # noqa: E402

IS_FRAC = 0.70       # validate_strategy と同じ floor 0.7 分割。
TAIL_ALPHA = 0.05    # 超過イベントの名目発生率（Kupiec の α）。


def run_step8(
    f: DailyFeatures,
    spa_out: dict,
    *,
    alpha: float = 0.05,
) -> StepResult:
    """Step8 実行。spa_out は run_step7 の返り値（F/t_index/rules）。"""
    F = spa_out["F"]
    t_index = spa_out["t_index"]
    rules: "list[Rule]" = spa_out["rules"]
    n = F.shape[0]
    if n < 300:
        return StepResult(
            step=8, name="oos_calibration", decision="fail_to_reject",
            statistics={"error": "insufficient_days", "n": int(n)},
            notes="有効日数不足のため保守的に fail_to_reject。",
        )
    split = int(math.floor(n * IS_FRAC))
    is_slice, oos_slice = slice(0, split), slice(split, n)

    # 1) IS 選抜（OOS 非接触）。
    is_mean = F[is_slice].mean(axis=0)
    best_k = int(np.argmax(is_mean))
    best = rules[best_k]

    # 2a) OOS 損失改善（HAC t・片側）。
    f_oos = F[oos_slice, best_k]
    lag = sc.newey_west_lag(f_oos.size)
    X = np.ones((f_oos.size, 1))
    beta, se = sc.ols_hac(X, f_oos, lag)
    t_stat = float(beta[0] / se[0]) if se[0] > 0 else 0.0
    p_one = 1.0 - sc.norm_cdf(t_stat)  # 片側: 平均損失差 > 0

    # 2b) 校正: 選抜ルール込みモデルの OOS 超過ヒット系列 → Kupiec / Christoffersen。
    y, X0 = build_har_arrays(f)
    dk = rule_dummy(f, best)
    ok = np.isfinite(y) & np.all(np.isfinite(X0), axis=1) & np.isfinite(dk)
    # F の行 index（t_index）は日次配列の添字 → IS/OOS 境界日で分割する。
    t_split = int(t_index[split])
    is_rows = np.flatnonzero(ok & (np.arange(f.n_days) < t_split))
    oos_rows = np.flatnonzero(ok & (np.arange(f.n_days) >= t_split))
    X1_is = np.column_stack([X0[is_rows], dk[is_rows]])
    beta1, *_ = np.linalg.lstsq(X1_is, y[is_rows], rcond=None)
    resid_is = y[is_rows] - X1_is @ beta1
    q_tail = float(np.quantile(resid_is, 1.0 - TAIL_ALPHA))  # 経験分位（分布仮定なし）
    mu_oos = np.column_stack([X0[oos_rows], dk[oos_rows]]) @ beta1
    hits = (y[oos_rows] > mu_oos + q_tail).astype(int).tolist()
    vb = VarBacktests()
    p_kupiec = float(vb.kupiec(hits, alpha=TAIL_ALPHA))
    p_christ = float(vb.christoffersen_independence(hits))

    loss_improved = bool(p_one < alpha)
    calibrated = bool(p_kupiec >= alpha)  # 校正が「壊れていない」= 非棄却。
    reject = loss_improved and calibrated

    flags: "list[str]" = []
    if loss_improved and not calibrated:
        flags.append("miscalibrated_despite_improvement")
    if p_christ < alpha:
        flags.append("hit_clustering")  # 超過が連鎖（独立性棄却）＝リスクの塊に注意。

    stats: "dict[str, object]" = {
        "best_rule": best.key,
        "n_is": int(split),
        "n_oos": int(n - split),
        "is_mean_loss_diff": float(is_mean[best_k]),
        "oos_mean_loss_diff": float(f_oos.mean()),
        "oos_t_hac": t_stat,
        "oos_p_one_sided": float(p_one),
        "tail_alpha": TAIL_ALPHA,
        "oos_hit_rate": float(np.mean(hits)) if hits else float("nan"),
        "n_hits": int(sum(hits)),
        "p_kupiec": p_kupiec,
        "p_christoffersen": p_christ,
        "alpha": alpha,
    }
    notes = (
        "IS（先頭70%）で最良ルールを選抜し OOS（末尾30%）のみで評価（validate_strategy と同じ分割規約）。"
        "reject = OOS 損失改善（片側 HAC）が有意 かつ Kupiec 校正が壊れていない。"
        "Christoffersen はヒットの独立性（連鎖の有無）の参考判定。"
    )
    return StepResult(
        step=8, name="oos_calibration",
        decision="reject" if reject else "fail_to_reject",
        statistics=stats, flags=tuple(flags), notes=notes,
    )
