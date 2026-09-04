"""気配品質診断とサンプリング間隔の決定（仕様 §4.1・§4.2）。

層名/責務:
    純粋ロジック層。診断対象は仕様 §4.1-1 に従い**先頭 ``n_har`` 本のバー**に限定する。
    出力は :class:`~.dto.QualityReport` のみで、以降の段階はこの 1 個の値に従う
    （仕様 §7-5「measure_id は 1 回の実行内で不変」）。

副作用は :class:`~.logs.Logger` プロトコル越しの注入のみ（既定は無出力）。
仕様 §3.3 E07 の ERROR ログを 1 回だけ出す。

依存: 外部 numpy / プロジェクト内 dto, logs, errors, measures, sampling。
"""

from __future__ import annotations

import numpy as np

from .dto import QualityReport
from .errors import E07_QUALITY_FAIL
from .logs import Logger, resolve
from .measures import realized_variance
from .sampling import previous_tick_sample

#: 仕様 §4.1-2 のサンプリング間隔集合 D（秒）。
SAMPLING_GRID: tuple[int, ...] = (5, 10, 15, 30, 60, 120, 300, 600, 900, 1800)

#: 仕様 §4.1-5・§4.2 の基準間隔。
REFERENCE_DELTA_SEC: int = 300

#: 仕様 §4.1-4 の「連続して不変」とみなす最小継続秒数。
FREEZE_MIN_SEC: float = 60.0

#: 仕様 §4.2 の一致許容幅。
DELTA_TOLERANCE: float = 0.05

#: 仕様 §4.1-6 のシグネチャ勾配しきい値。
#:
#: ISSUE-207 の裁定（2026-07-30・§4.1-6 改訂）: v1.0 の `S > 0.50` は §9 段階 2 の
#: 「``ω/σ ≥ 0.5`` のとき DEGRADED 以上」という要求を**構造的に満たさなかった**。
#: ``S`` は ``S = (2 − 1/30)r² / (1 + r²/30)``（``r = ω/σ``）であり、``r = 0.5`` での
#: 理論値は 0.4877・実測は 0.4718 で、いずれも 0.50 に届かず PASS と判定されていた。
#: 閾値 0.50 は仕様が根拠を示していない固定値であり、`S(r=0.5)` を計算せずに置かれた
#: 可能性が高い一方、§9 の要求は設計意図（ノイズが信号の半分に達したら警告する）である。
#: 意図を正とし、理論値 0.4877 を下回る丸い値として **0.45** を採る。
S_DEGRADED: float = 0.45
S_PASS_FIXED: float = 0.10


def diagnose_quality(times: np.ndarray, logp: np.ndarray, bar_edges: np.ndarray,
                     n_har: int, freeze_thresh: float, *,
                     logger: Logger | None = None) -> QualityReport:
    """仕様 §4.1（段階 0）と §4.2（段階 1）を実行し診断結果を返す。"""
    log = resolve(logger)
    n_diag = int(min(n_har, bar_edges.size - 1))
    starts = np.searchsorted(times, bar_edges[:n_diag], side="left")
    ends = np.searchsorted(times, bar_edges[1:n_diag + 1], side="left")

    # 手順 2：各 Δ について全期間平均の実現分散 RV̄(Δ) を算出する。
    rv_total: dict[int, float] = {}
    n_samples_5: int = 0
    for delta in SAMPLING_GRID:
        total = 0.0
        for b in range(n_diag):
            lo, hi = int(starts[b]), int(ends[b])
            if hi - lo < 2:
                continue
            s = previous_tick_sample(times[lo:hi], logp[lo:hi],
                                     float(bar_edges[b]), float(bar_edges[b + 1]), float(delta))
            total += realized_variance(s)
            if delta == 5:
                n_samples_5 += int(s.size)
        rv_total[delta] = total
    rv_mean = {d: (rv_total[d] / n_diag if n_diag > 0 else float("nan")) for d in SAMPLING_GRID}

    # 手順 3：ノイズ分散 ω̂² = RV_total(5 秒) / (2 · n_min)。
    omega2 = rv_total[5] / (2.0 * n_samples_5) if n_samples_5 > 0 else float("nan")

    # 手順 4：気配凍結率。
    freeze_ratio = _freeze_ratio(times, logp, bar_edges, n_diag)

    # 手順 5：シグネチャ勾配。
    ref = rv_mean[REFERENCE_DELTA_SEC]
    slope = (rv_mean[5] - ref) / ref if ref not in (0.0,) and np.isfinite(ref) else float("nan")

    # 手順 6：判定（この順に評価し、最初に成立した分岐を採用する）。
    if freeze_ratio > freeze_thresh:
        log.emit("ERROR", E07_QUALITY_FAIL, -1,
                 f"freeze_ratio={freeze_ratio!r} > freeze_thresh={freeze_thresh!r} のため PARK へ縮退")
        return QualityReport(rv_mean, omega2, freeze_ratio, slope, "FAIL", "PARK", 0)
    if not np.isfinite(slope):
        # 診断不能（RV̄(300) が 0 または非有限）。高頻度データを信頼できないため縮退する。
        log.emit("ERROR", E07_QUALITY_FAIL, -1, f"シグネチャ勾配が算出不能: RV̄(300)={ref!r}")
        return QualityReport(rv_mean, omega2, freeze_ratio, slope, "FAIL", "PARK", 0)
    if slope > S_DEGRADED:
        # 仕様 §4.1-6 の DEGRADED 行は measure_id="TSRV" のみを定め delta_star_sec を
        # 定義していない（§4.2 は measure_id="RV" かつ S <= 0.10 の場合にのみ適用される）。
        # 一方 §3.2 は当該フィールドの値を要求する。ここでは基準間隔 300 秒を用いる
        # ＝**実装側の選択**であり仕様の規定ではない（ISSUE-215 と併せて裁定を要する）。
        return QualityReport(rv_mean, omega2, freeze_ratio, slope, "DEGRADED", "TSRV",
                             REFERENCE_DELTA_SEC)
    if slope > S_PASS_FIXED:
        return QualityReport(rv_mean, omega2, freeze_ratio, slope, "PASS", "RV",
                             REFERENCE_DELTA_SEC)
    return QualityReport(rv_mean, omega2, freeze_ratio, slope, "PASS", "RV",
                         select_delta_star(rv_mean))


def select_delta_star(rv_mean: dict[int, float]) -> int:
    """仕様 §4.2：昇順に走査し、以降すべての Δ' が基準と 5% 未満で一致する最小の Δ。

    該当する Δ が存在しない場合は ``REFERENCE_DELTA_SEC``（300）を返す。
    """
    ref = rv_mean.get(REFERENCE_DELTA_SEC, float("nan"))
    if not np.isfinite(ref) or ref == 0.0:
        return REFERENCE_DELTA_SEC
    for i, delta in enumerate(SAMPLING_GRID):
        ok = True
        for other in SAMPLING_GRID[i:]:
            val = rv_mean.get(other, float("nan"))
            if not np.isfinite(val) or abs(val - ref) / ref >= DELTA_TOLERANCE:
                ok = False
                break
        if ok:
            return int(delta)
    return REFERENCE_DELTA_SEC


def _freeze_ratio(times: np.ndarray, logp: np.ndarray, bar_edges: np.ndarray,
                  n_diag: int) -> float:
    """仕様 §4.1-4：mid が 60 秒以上連続して不変であった時間の総和 / 診断期間の総時間。

    「不変であった時間」は、値が等しい連続ティック列の**先頭ティック時刻から
    末尾ティック時刻まで**の長さとする（次の値変化の時刻は当該ティックで観測されるため、
    不変が保証される区間はこの閉区間に限られる）。
    """
    if n_diag <= 0:
        return float("nan")
    t_lo = float(bar_edges[0])
    t_hi = float(bar_edges[n_diag])
    total_time = t_hi - t_lo
    if total_time <= 0.0:
        return float("nan")

    lo = int(np.searchsorted(times, t_lo, side="left"))
    hi = int(np.searchsorted(times, t_hi, side="left"))
    t = times[lo:hi]
    p = logp[lo:hi]
    if t.size < 2:
        return 0.0

    # 値が変化する位置で区切り、各連続不変ランの継続時間を求める。
    changed = np.nonzero(p[1:] != p[:-1])[0] + 1
    run_starts = np.concatenate([[0], changed])
    run_ends = np.concatenate([changed - 1, [t.size - 1]])
    durations = t[run_ends] - t[run_starts]
    frozen = float(durations[durations >= FREEZE_MIN_SEC].sum())
    return frozen / total_time
