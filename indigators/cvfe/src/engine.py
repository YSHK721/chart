"""CVFE のオーケストレーション（仕様 §4 全体）。

層名/責務:
    純粋ロジック層の最上位。段階 0〜7 の**順序**のみを持ち、各段階の数式は
    quality / measures / jumps / har / gap の各モジュールへ委譲する。

一括経路と逐次経路の同一性（仕様 §6「Look-ahead 不在」）:
    ``compute_cvfe`` は ``fit_state ▶ [measure_bar]* ▶ CvfeSequential.push*`` の
    合成そのものであり、逐次経路と同一の関数を同一の順序で呼ぶ。両経路の bit 一致は
    実装の性質ではなく構成の帰結である。

    ただしこの構成だけでは「``fit_state`` が全期間ティックを受け取ること」に由来する
    将来情報の混入は検出できない。それは**切詰め不変性**（入力を ``bar_edges[T]`` で
    切り詰めて再計算しても ``sigma_hat[:T]`` が bit 一致する）で別途検証する
    （tests/test_causality.py）。

依存: 外部 numpy / プロジェクト内 dto, errors, logs, sampling, quality, measures, jumps, har, gap。
"""

from __future__ import annotations

import numpy as np

from .dto import (
    HAR_LAG_MONTH,
    WARMUP_LAGS,
    BarMeasure,
    CvfeParams,
    CvfeResult,
    CvfeState,
    QualityReport,
)
from .errors import (
    E01_INSUFFICIENT_BARS,
    E06_EMPTY_BAR,
    E08_HAR_SINGULAR,
    W03_GAP_INIT_LOOKAHEAD,
    W05_HAR_TRAINING_ROWS_DROPPED,
    W06_NO_AVAILABLE_BARS,
    CvfeError,
)
from .gap import GAP_INIT_BARS, GapEwma, initial_gap_variance, is_gap_bar
from .har import C_FLOOR, har_feature_row, har_fit, har_predict, sigma_oc_from_log_variance
from .jumps import jump_test
from .logs import Logger, resolve
from .measures import parkinson, realized_range, realized_variance, two_scale_rv
from .quality import diagnose_quality
from .sampling import previous_tick_sample, split_bars, validate_edges, validate_ticks


def measure_bar(index: int, bar_times: np.ndarray, bar_logp: np.ndarray,
                edge_start: float, edge_end: float,
                measure_id: str, delta_star_sec: int, jump_alpha: float, *,
                logger: Logger | None = None) -> BarMeasure:
    """仕様 §4.3（測定量）と §4.4（ジャンプ分離）を 1 バーに適用する純関数。

    ティックが 2 本未満のバーは ``E06_EMPTY_BAR`` を WARN 出力し ``valid=False`` を返す
    （例外は送出しない。仕様 §3.3）。
    """
    log = resolve(logger)
    k = bar_times.size
    if k < 2:
        log.emit("WARN", E06_EMPTY_BAR, index, f"バー内ティック数 {k} < 2")
        nan = float("nan")
        return BarMeasure(index, 0, nan, nan, 0.0, False, nan, nan, nan, nan,
                          nan, nan, float(edge_start), False)

    p_open = float(bar_logp[0])
    p_close = float(bar_logp[-1])
    p_high = float(bar_logp.max())
    p_low = float(bar_logp.min())
    t_first = float(bar_times[0])
    t_last = float(bar_times[-1])

    if measure_id in ("RV", "TSRV"):
        s = previous_tick_sample(bar_times, bar_logp, float(edge_start), float(edge_end),
                                 float(delta_star_sec))
        r = np.diff(s) if s.size >= 2 else np.empty(0, dtype=np.float64)
        v = (realized_variance(s) if measure_id == "RV"
             else two_scale_rv(s, logger=logger, bar_index=index))
        jr = jump_test(v, r, jump_alpha, logger=logger, bar_index=index)
        c, j, flag = jr.c, jr.j, jr.flag
        n = int(r.size)
    elif measure_id == "RRANGE":
        v = realized_range(bar_times, bar_logp, float(edge_start), float(edge_end))
        c, j, flag, n = v, 0.0, False, k
    elif measure_id == "PARK":
        v = parkinson(p_high, p_low)
        c, j, flag, n = v, 0.0, False, k
    else:  # pragma: no cover - measure_id は quality.py が生成する 4 値に限られる
        raise CvfeError("E05_PARAM_RANGE", f"未知の measure_id: {measure_id!r}")

    return BarMeasure(index, n, float(v), float(c), float(j), bool(flag),
                      p_open, p_close, p_high, p_low, t_first, t_last,
                      float(edge_start), True)


def measure_all_bars(times: np.ndarray, logp: np.ndarray, bar_edges: np.ndarray,
                     quality: QualityReport, params: CvfeParams, *,
                     logger: Logger | None = None) -> list[BarMeasure]:
    """全バーへ :func:`measure_bar` を適用する（一括経路・逐次経路の共通前段）。"""
    starts, ends = split_bars(times, bar_edges)
    out: list[BarMeasure] = []
    for i in range(bar_edges.size - 1):
        lo, hi = int(starts[i]), int(ends[i])
        out.append(measure_bar(i, times[lo:hi], logp[lo:hi],
                               float(bar_edges[i]), float(bar_edges[i + 1]),
                               quality.measure_id, quality.delta_star_sec,
                               params.jump_alpha, logger=logger))
    return out


def gap_flags_and_squares(measures: list[BarMeasure], params: CvfeParams,
                          delta_star_sec: int) -> tuple[np.ndarray, np.ndarray]:
    """各バーのギャップ保有フラグと ``g_t``（非保有は ``nan``）を返す（仕様 §4.7-1,2）。"""
    n = len(measures)
    flags = np.zeros(n, dtype=bool)
    g = np.full(n, np.nan, dtype=np.float64)
    for t in range(1, n):
        cur, prev = measures[t], measures[t - 1]
        flags[t] = is_gap_bar(cur.edge_start, prev.edge_start, float(params.bar_interval_sec),
                              cur.t_first, prev.t_last, float(delta_star_sec))
        if flags[t]:
            g[t] = cur.p_open - prev.p_close
    return flags, g



def build_training_sample(c: np.ndarray, j: np.ndarray, p_close: np.ndarray,
                          lo: int, hi: int, *,
                          logger: Logger | None = None) -> tuple[np.ndarray, np.ndarray]:
    """仕様 §4.5-1〜3 の学習標本 ``(X, y)`` を ``t ∈ [lo, hi]`` について構成する。

    無効バー（仕様 §3.3 E06 でティック < 2 だったバー）は ``C_t = nan`` を持ち、
    それを参照する行・目的変数は ``nan`` になる。仕様 §4.5 は当該行の扱いを規定して
    いないが、§3.3 E06 は「処理は継続」を明示しているため、**非有限な行を学習標本から
    除外**して残りで推定する（除外しないと必ず E08 で停止し E06 の保証が破れる。
    ISSUE-205）。除外が発生した場合は本数を WARN 出力する。

    一括経路（:func:`fit_state`）と逐次経路（``CvfeSequential`` の再学習）は
    本関数のみで学習標本を作る（両経路で構成が分岐しない）。
    """
    n_rows = hi - lo + 1
    x_rows = np.empty((n_rows, 5), dtype=np.float64)
    y = np.empty(n_rows, dtype=np.float64)
    for k, t in enumerate(range(lo, hi + 1)):
        rho = p_close[t] - p_close[t - 1] if t >= 1 else float("nan")
        x_rows[k] = har_feature_row(c[t - HAR_LAG_MONTH + 1:t + 1], j[t], rho)
        y[k] = np.log(max(c[t + 1], C_FLOOR)) if np.isfinite(c[t + 1]) else np.nan

    keep = np.all(np.isfinite(x_rows), axis=1) & np.isfinite(y)
    dropped = int(n_rows - keep.sum())
    if dropped:
        resolve(logger).emit(
            "WARN", W05_HAR_TRAINING_ROWS_DROPPED, -1,
            f"学習標本 {n_rows} 本のうち無効バー由来の {dropped} 本を除外した")
    return x_rows[keep], y[keep]


def fit_state(measures: list[BarMeasure], quality: QualityReport, params: CvfeParams, *,
              logger: Logger | None = None) -> CvfeState:
    """段階 4 の学習（仕様 §4.5）とギャップ EWMA 初期値（§4.7-3）を確定する。"""
    log = resolve(logger)
    n = len(measures)
    c = np.array([m.c for m in measures], dtype=np.float64)
    j = np.array([m.j for m in measures], dtype=np.float64)
    p_close = np.array([m.p_close for m in measures], dtype=np.float64)

    t0 = params.first_available_index
    lo = t0 - params.n_har - 1          # 学習標本の先頭 t
    hi = t0 - 2                          # 学習標本の末尾 t（y の添字は t0 − 1 まで）

    x_rows, y = build_training_sample(c, j, p_close, lo, hi, logger=logger)
    beta, s2 = har_fit(x_rows, y, logger=logger)

    flags, g = gap_flags_and_squares(measures, params, quality.delta_star_sec)
    gap_idx = np.nonzero(flags)[0]
    # 仕様 §4.7-3 は「先頭 200 本のギャップ保有バー」とだけ述べ、対象を予測開始バーより
    # 前に限定していない。総数が 200 本未満だと t >= t0 のギャップが初期値へ混入し、
    # §4 柱書（t より前の情報のみ参照）に反する（＝切詰め不変性が破れる）。
    # ここでは因果性を優先して t0 未満に限定する。200 本以上が t0 より前に存在する
    # 通常のケースでは、限定してもしなくても先頭 200 本は同一であり結果は変わらない。
    causal_idx = gap_idx[gap_idx < t0]
    if causal_idx.size < GAP_INIT_BARS:
        log.emit("WARN", W03_GAP_INIT_LOOKAHEAD, -1,
                 f"予測開始バー t0={t0} より前のギャップ保有バーが {causal_idx.size} 本しかない"
                 f"（仕様 §4.7-3 の {GAP_INIT_BARS} 本に満たない）。存在する本数の平均を用いる")
    v_init = initial_gap_variance(g[causal_idx] ** 2, GAP_INIT_BARS)

    return CvfeState(quality, beta, s2, v_init, t0)


class CvfeSequential:
    """1 バーずつ ``σ̂`` を確定する逐次経路（仕様 §4.5-6・§4.6・§4.7・§4.8）。

    ``push`` は必ずバー番号 0 から連番で呼ぶ。一括経路 :func:`compute_cvfe` も
    本クラスを経由するため、両経路は同一の演算・同一の順序を通る。
    """

    __slots__ = ("_params", "_log", "_beta", "_s2", "_gap", "_t0", "_delta_star",
                 "_c", "_j", "_pclose", "_prev", "_t")

    def __init__(self, state: CvfeState, params: CvfeParams, *,
                 logger: Logger | None = None) -> None:
        self._params = params
        self._log = resolve(logger)
        self._beta = np.array(state.har_coef, dtype=np.float64, copy=True)
        self._s2 = float(state.har_resid_var)
        self._gap = GapEwma(state.gap_v_init, params.lam_gap)
        self._t0 = int(state.first_available_index)
        self._delta_star = int(state.delta_star_sec)
        self._c: list[float] = []
        self._j: list[float] = []
        self._pclose: list[float] = []
        self._prev: BarMeasure | None = None
        self._t = 0

    @property
    def har_coef(self) -> np.ndarray:
        return self._beta

    @property
    def har_resid_var(self) -> float:
        return self._s2

    def push(self, bm: BarMeasure) -> tuple[float, float, float, bool]:
        """バー ``t`` の ``(sigma_hat, sigma_oc, sigma_co, available)`` を返す。"""
        t = self._t
        if bm.index != t:
            raise CvfeError(E01_INSUFFICIENT_BARS,
                            f"バーは 0 から連番で push する必要がある: {bm.index} != {t}")

        # 仕様 §4.5-6：再学習は当該バーの予測より前に、t−1 以前のみを用いて行う。
        self._maybe_refit(t)

        sigma_oc = self._forecast_oc(t)
        sigma_co, g_t, is_gap = self._gap_component(bm)

        sigma = float(np.sqrt(sigma_oc * sigma_oc + sigma_co * sigma_co))
        # 仕様 §3.3 E06：ティック < 2 のバーは（予測自体は t−1 までの情報で算出できるが）
        # 当該バーを available=False / sigma_hat=nan とする。
        # 仕様 §3.3 E09：σ̂ が非有限または <= 0 のバーも同様。
        available = bool(t >= self._t0 and bm.valid and np.isfinite(sigma) and sigma > 0.0)
        if not available:
            sigma = float("nan")

        # 仕様 §4.7-3：EWMA の更新は σ̂_CO,t を確定した「後」に行う（因果性）。
        if is_gap and np.isfinite(g_t):
            self._gap.update(g_t)

        self._c.append(bm.c)
        self._j.append(bm.j)
        self._pclose.append(bm.p_close)
        self._prev = bm
        self._t += 1
        return sigma, sigma_oc, sigma_co, available

    # -- 内部 ---------------------------------------------------------------------

    def _forecast_oc(self, t: int) -> float:
        """仕様 §4.6：``x_{t−1}`` のみから ``σ̂_OC,t`` を得る。"""
        if t < HAR_LAG_MONTH:
            return float("nan")
        c_win = np.array(self._c[t - HAR_LAG_MONTH:t], dtype=np.float64)
        rho = (self._pclose[t - 1] - self._pclose[t - 2]) if t >= 2 else float("nan")
        x_prev = har_feature_row(c_win, self._j[t - 1], rho)
        return sigma_oc_from_log_variance(har_predict(self._beta, x_prev), self._s2)

    def _gap_component(self, bm: BarMeasure) -> tuple[float, float, bool]:
        """仕様 §4.7-1,2,4：``σ̂_CO,t`` と当該バーの ``g_t``・ギャップ保有可否。"""
        prev = self._prev
        if prev is None:
            return 0.0, float("nan"), False
        is_gap = is_gap_bar(bm.edge_start, prev.edge_start,
                            float(self._params.bar_interval_sec),
                            bm.t_first, prev.t_last, float(self._delta_star))
        if not is_gap:
            return 0.0, float("nan"), False
        g_t = bm.p_open - prev.p_close
        v_prev = self._gap.current()
        sigma_co = float(np.sqrt(v_prev)) if np.isfinite(v_prev) and v_prev >= 0.0 else float("nan")
        return sigma_co, g_t, True

    def _maybe_refit(self, t: int) -> None:
        """仕様 §4.5-6：``refit_every = q > 0`` のとき、``q`` の倍数バーで再推定する。"""
        q = int(self._params.refit_every)
        if q <= 0 or t <= 0 or t % q != 0:
            return
        n_har = int(self._params.n_har)
        lo = t - n_har - 1
        hi = t - 2
        if lo < HAR_LAG_MONTH - 1 or hi < lo:
            return
        c = np.asarray(self._c, dtype=np.float64)
        j = np.asarray(self._j, dtype=np.float64)
        pc = np.asarray(self._pclose, dtype=np.float64)
        rows, y = build_training_sample(c, j, pc, lo, hi, logger=self._log)
        try:
            self._beta, self._s2 = har_fit(rows, y, logger=self._log)
        except CvfeError as exc:
            # 再学習に失敗した場合は直近の係数を保持する（凍結時と同じ挙動へ縮退）。
            # 仕様 §3.3 E08 は初回学習について ValueError を規定するが、§4.5-6 の
            # 再学習が失敗した場合の扱いを定めていない。無音で縮退させず記録する。
            self._log.emit("WARN", E08_HAR_SINGULAR, t,
                           f"再学習に失敗したため直近の係数を保持する: {exc.detail}")
            return


def compute_cvfe(ticks: np.ndarray, bar_edges: np.ndarray, bar_interval_sec: int, *,
                 n_har: int = 1500, lam_gap: float = 0.97, jump_alpha: float = 0.999,
                 freeze_thresh: float = 0.05, refit_every: int = 0,
                 logger: Logger | None = None) -> CvfeResult:
    """仕様 §4 の全段階を実行し :class:`~.dto.CvfeResult` を返す（一括経路）。"""
    params = CvfeParams(bar_interval_sec=bar_interval_sec, n_har=n_har, lam_gap=lam_gap,
                        jump_alpha=jump_alpha, freeze_thresh=freeze_thresh,
                        refit_every=refit_every)
    times, logp = validate_ticks(ticks)
    edges = validate_edges(bar_edges)

    n_bars = edges.size - 1
    if n_bars < params.n_har + WARMUP_LAGS:
        raise CvfeError(E01_INSUFFICIENT_BARS,
                        f"バー数 {n_bars} < n_har + {WARMUP_LAGS} = {params.n_har + WARMUP_LAGS}")

    quality = diagnose_quality(times, logp, edges, params.n_har, params.freeze_thresh,
                               logger=logger)
    measures = measure_all_bars(times, logp, edges, quality, params, logger=logger)
    state = fit_state(measures, quality, params, logger=logger)

    seq = CvfeSequential(state, params, logger=logger)
    sigma_hat = np.full(n_bars, np.nan, dtype=np.float64)
    sigma_oc = np.full(n_bars, np.nan, dtype=np.float64)
    sigma_co = np.zeros(n_bars, dtype=np.float64)
    available = np.zeros(n_bars, dtype=bool)
    for m in measures:
        s, oc, co, ok = seq.push(m)
        sigma_hat[m.index] = s
        sigma_oc[m.index] = oc
        sigma_co[m.index] = co
        available[m.index] = ok

    if not available.any():
        resolve(logger).emit(
            "WARN", W06_NO_AVAILABLE_BARS, -1,
            f"有効な σ̂ が 1 本も得られなかった（N={n_bars}, n_har={params.n_har}）。"
            f"1 本以上を得るには N >= n_har + {WARMUP_LAGS + 1} が必要")

    return CvfeResult(
        sigma_hat=sigma_hat,
        sigma_oc=sigma_oc,
        sigma_co=sigma_co,
        measure_id=quality.measure_id,
        delta_star_sec=int(quality.delta_star_sec),
        omega2_hat=float(quality.omega2_hat),
        freeze_ratio=float(quality.freeze_ratio),
        jump_flag=np.array([m.jump_flag for m in measures], dtype=bool),
        # refit_every > 0 では最終的に使われた係数を返す（凍結時は初回学習と同一）。
        har_coef=np.array(seq.har_coef, dtype=np.float64, copy=True),
        har_resid_var=float(seq.har_resid_var),
        quality_gate=quality.quality_gate,
        available=available,
        signature_slope=float(quality.signature_slope),
    )
