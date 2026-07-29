"""OHLC バー列からの CVFE 実行経路（仕様 §4.1 の FAIL 縮退を明示的に用いる）。

層名/責務:
    純粋ロジック層。チャート UI（`indicator_ui`）の計算経路はティックではなく
    **OHLC の DataFrame** を渡す。仕様 §3.1 が要求する `ticks` が存在しないため、
    §4.1 の気配品質診断は実行できない。

    仕様 §4.1-6 は「高頻度データを使用しない」場合の縮退先を
    `quality_gate = "FAIL"` / `measure_id = "PARK"` と定めており、§4.3 の `PARK`
    （`PK_t = (ln H_t − ln L_t)² / (4 ln 2)`）はバーの高値・安値のみで算出できる。
    本モジュールはこの**仕様が定めた縮退経路**をそのまま用いる。

    測定量より下流（§4.4 ジャンプ分離・§4.5 HAR-CJ-L・§4.6 予測・§4.7 ギャップ・
    §4.8 合成）は測定量に依存しないため、`engine` の関数をそのまま再利用する
    （UI 用の別実装を持たない）。

精度についての注意（仕様 §7-6）:
    `quality_gate = "FAIL"` では本エンジンの出力精度は CEB v1.0 と同等まで低下する。
    附録 A の実測では `Var(ln σ̂)` が `PARK` 0.08575 に対し `RV`（288 本）0.00174 であり、
    **49 倍の効率差**がある。ティックが利用できる呼び出し元は :func:`~.engine.compute_cvfe`
    を使うこと。本経路は「ティックが無い環境でも同じ予測構造を動かす」ためのものである。

依存: 外部 numpy / プロジェクト内 dto, engine, measures。
"""

from __future__ import annotations

import numpy as np

from .dto import BarMeasure, CvfeParams, CvfeResult, QualityReport, WARMUP_LAGS
from .engine import CvfeSequential, fit_state
from .errors import E01_INSUFFICIENT_BARS, CvfeError
from .logs import Logger, resolve
from .measures import parkinson

#: OHLC 経路で用いる測定量（仕様 §4.1-6 の FAIL 行）。
OHLC_MEASURE_ID: str = "PARK"

#: 仕様 §3.1 の下限。OHLC 経路の既定 `n_har` はこの値に合わせる
#: （日足で 1,500 本を要求すると 6 年分のバーが無いと 1 本も出力できないため）。
DEFAULT_OHLC_N_HAR: int = 500


def infer_bar_interval_sec(times_sec: np.ndarray) -> int:
    """バー公称長（秒）を時刻列の差分の中央値から推定する。

    仕様 §3.1 の `bar_interval_sec` は呼び出し側が与える前提だが、UI 経路では
    時間足がユーザー操作で変わるため実データから求める（§7-3 の「期間中に
    バー境界は変更されない」は 1 回の計算内で成立する）。
    """
    t = np.asarray(times_sec, dtype=np.float64)
    if t.size < 2:
        return 60
    d = np.diff(t)
    d = d[np.isfinite(d) & (d > 0.0)]
    if d.size == 0:
        return 60
    return max(60, int(round(float(np.median(d)))))


def bar_edges_from_times(times_sec: np.ndarray, bar_interval_sec: int) -> np.ndarray:
    """バー開始時刻の列から `bar_edges`（`N+1` 要素）を作る。

    末尾の境界は最終バーの開始時刻 + 公称長とする。仕様 §4.7-1 の第 1 条件
    （`bar_edges[t] − bar_edges[t−1] > 1.5 × bar_interval_sec`）は、この構成では
    「実データ上の欠測（週末・休場）」を検出する条件として機能する。
    """
    t = np.asarray(times_sec, dtype=np.float64)
    return np.concatenate([t, [t[-1] + float(bar_interval_sec)]])


def measures_from_ohlc(open_: np.ndarray, high: np.ndarray, low: np.ndarray,
                       close: np.ndarray, bar_edges: np.ndarray) -> list[BarMeasure]:
    """OHLC から `PARK` の :class:`~.dto.BarMeasure` 列を作る（仕様 §4.3 "PARK"）。

    `t_first` / `t_last` は `nan` とする。ティック時刻が存在しないため、
    仕様 §4.7-1 の第 2 条件（ティック間隔による判定）を適用できない
    ＝第 1 条件（バー長）のみでギャップを判定する。
    """
    o = np.asarray(open_, dtype=np.float64)
    h = np.asarray(high, dtype=np.float64)
    lo_ = np.asarray(low, dtype=np.float64)
    c = np.asarray(close, dtype=np.float64)
    n = c.size

    out: list[BarMeasure] = []
    nan = float("nan")
    for i in range(n):
        ok = bool(np.isfinite(o[i]) and np.isfinite(h[i]) and np.isfinite(lo_[i])
                  and np.isfinite(c[i]) and o[i] > 0 and h[i] > 0 and lo_[i] > 0 and c[i] > 0)
        if not ok:
            out.append(BarMeasure(i, 0, nan, nan, 0.0, False, nan, nan, nan, nan,
                                  nan, nan, float(bar_edges[i]), False))
            continue
        p_open, p_high = float(np.log(o[i])), float(np.log(h[i]))
        p_low, p_close = float(np.log(lo_[i])), float(np.log(c[i]))
        v = parkinson(p_high, p_low)
        out.append(BarMeasure(i, 1, v, v, 0.0, False,
                              p_open, p_close, p_high, p_low,
                              nan, nan, float(bar_edges[i]), True))
    return out


def ohlc_quality_report() -> QualityReport:
    """ティック不在時の診断結果（仕様 §4.1-6 の FAIL 行そのもの）。

    §4.1 の 5 つの診断量（`RV̄(Δ)` / `ω̂²` / `freeze_ratio` / `S`）はいずれも
    ティックを要するため算出できない。**測定できない量を 0 で埋めず `nan` を返す**。
    """
    nan = float("nan")
    return QualityReport(rv_mean={}, omega2_hat=nan, freeze_ratio=nan,
                         signature_slope=nan, quality_gate="FAIL",
                         measure_id=OHLC_MEASURE_ID, delta_star_sec=0)


def compute_cvfe_from_ohlc(open_: np.ndarray, high: np.ndarray, low: np.ndarray,
                           close: np.ndarray, times_sec: np.ndarray, *,
                           n_har: int = DEFAULT_OHLC_N_HAR,
                           lam_gap: float = 0.97,
                           refit_every: int = 0,
                           bar_interval_sec: int | None = None,
                           logger: Logger | None = None) -> CvfeResult:
    """OHLC バー列に対し §4.3 `PARK` → §4.5〜§4.8 を実行し :class:`~.dto.CvfeResult` を返す。"""
    times = np.asarray(times_sec, dtype=np.float64)
    if times.size < 2:
        raise CvfeError(E01_INSUFFICIENT_BARS, f"バー数が不足する: {times.size}")

    interval = int(bar_interval_sec) if bar_interval_sec else infer_bar_interval_sec(times)
    params = CvfeParams(bar_interval_sec=interval, n_har=int(n_har),
                        lam_gap=float(lam_gap), refit_every=int(refit_every))

    n_bars = times.size
    if n_bars < params.n_har + WARMUP_LAGS + 1:
        raise CvfeError(
            E01_INSUFFICIENT_BARS,
            f"バー数 {n_bars} では σ̂ を 1 本も出力できない"
            f"（n_har + {WARMUP_LAGS + 1} = {params.n_har + WARMUP_LAGS + 1} 本以上が必要）")

    edges = bar_edges_from_times(times, interval)
    quality = ohlc_quality_report()
    measures = measures_from_ohlc(open_, high, low, close, edges)
    state = fit_state(measures, quality, params, logger=logger)

    seq = CvfeSequential(state, params, logger=resolve(logger) and logger)
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

    return CvfeResult(
        sigma_hat=sigma_hat, sigma_oc=sigma_oc, sigma_co=sigma_co,
        measure_id=quality.measure_id, delta_star_sec=quality.delta_star_sec,
        omega2_hat=quality.omega2_hat, freeze_ratio=quality.freeze_ratio,
        jump_flag=np.zeros(n_bars, dtype=bool),
        har_coef=np.array(seq.har_coef, dtype=np.float64, copy=True),
        har_resid_var=float(seq.har_resid_var),
        quality_gate=quality.quality_gate, available=available,
        signature_slope=quality.signature_slope,
    )
