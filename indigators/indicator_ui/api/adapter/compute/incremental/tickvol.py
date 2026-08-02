"""ティックボリューム（本体＋外れ値水準）の増分器（ISSUE-233 と同型・ISSUE-239）。

真因の除去:
    水準（正常帯上端＝POT 閾値・経験的分位・GPD 外挿）はいずれも **当該バーを除く** 因果統計
    であり、形成中バーの水準は確定済みの観測だけで決まる。よって足内更新のたびに窓全体を
    走り直す必要はない。確定バーまでの系列・イベント観測列を状態として保持し、形成中バーは
    「本体の値を差し替えるだけ」にする。

    水準を full 再計算に任せられない理由は tail が取れないことにある: 水準は確定イベント
    すべてに依存し、直近 k_events 件を得るのに必要な履歴長はイベント頻度（データ依存）で
    決まる（実測 5m で 1 件 / 35.7 バー＝50 件に 1,800 バー）。有限 tail を宣言すると
    データ次第で黙って水準が変わる。

足内の費用がゼロである理由:
    形成中バーの水準は ``len(up)``（確定イベント数）と確定系列だけで決まり、足内では
    どちらも動かない。よって水準（GPD 当てはめ 1 回を含む）は **状態遷移時に 1 度だけ**
    求めて状態へ持たせ、``emit`` は読むだけにする。足内更新は本体ヒストグラムの末尾値の
    差し替えに縮退する。

参照実装（無改変・計算式を写さない）:
    ``tickvol`` src の ``causal_threshold`` / ``step_excess_event`` / ``levels_latest`` /
    ``tickvol_levels``。いずれもローリング版が同じ 1 バー入口を各バーで呼ぶ構成であり、
    定義は 1 箇所しかない。

対象外（``prepare`` が None を返し従来経路へ落ちる＝挙動不変）:
    パラメータ不正（window_n / 分位ペア / k_events）、``time_column`` 指定あり、volume 列なし、
    本数不足。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import numpy as np
import pandas as pd

from common import event_quantiles as _evq
from common import marod_bands as _bands
from common_view.lwc_adapter import resolve_times

from adapter.compute.call_binding import indicator_src

#: 系列名（lwc_chart の emit 順と同じ。skeleton との突合はこの名前で行う）。
_HIST_NAME = "tickvol"
_LEVEL_NAMES = {
    "med": "tickvol_evq_med_hi",
    "ext": "tickvol_evq_ext_hi",
    "gpd": "tickvol_gpd_hi",
}
#: 回帰トレンド（btlm_trail 仕様）の固定名系列。帯 2 本は分位依存の動的名（別扱い）。
_TREND_FIXED_NAMES = {
    "mean": "tickvol_trend_mean",
    "off_high": "tickvol_trend_off_hi",
    "off_low": "tickvol_trend_off_lo",
    "beta": "tickvol_trend_beta",
    "sigma": "tickvol_trend_sigma",
    "band_hit_rate": "tickvol_trend_band_hit_rate",
}
#: トレンド成果の全キー（確定配列として状態が持つもの）。
_TREND_KEYS = ("mean", "band_low", "band_high", "off_low", "off_high",
               "beta", "sigma", "band_hit_rate")


@dataclass(frozen=True)
class _Request:
    values: np.ndarray       # tickvol 系列（長さ n）
    times: np.ndarray        # UNIX 秒（int64・長さ n）
    n: int
    window_n: int
    q_low: float
    q_high: float
    q_out: Any
    k_events: int
    maxbars: int
    band_method: str
    empirical_n: int
    show_metrics: bool
    n_cov: int


@dataclass(frozen=True)
class _State:
    values: np.ndarray                 # 確定 tickvol（長さ m）
    band_low: np.ndarray               # 確定 正常帯下端（長さ m・表示専用）
    threshold: np.ndarray              # 確定 正常帯上端＝POT 閾値（長さ m）
    levels: dict                       # キー → 確定水準（長さ m・価格軸スケール）
    up: tuple                          # 確定したイベント観測（超過分・時系列順）
    run_up: tuple                      # 進行中エピソード（超過分）
    next_band_low: float               # 次バーへ適用する帯下端（確定系列のみから決まる）
    next_threshold: float              # 次バーへ適用する閾値（同上）
    next_levels: dict                  # 次バーへ適用する水準（超過分スケール）
    deviations: np.ndarray             # 確定 乖離率 (v-mean)/mean（長さ m・経験分位帯の材料）
    trend: dict                        # キー → 確定トレンド系列（長さ m。未算出は None）
    m: int


class TickvolIncrementer:
    """``Incrementer`` 実装（ティックボリューム本体＋外れ値水準）。"""

    def __init__(self) -> None:
        self._src = None

    def _module(self):
        if self._src is None:
            self._src = indicator_src("tickvol")
        return self._src

    # ------------------------------------------------------------------ #
    # prepare
    # ------------------------------------------------------------------ #
    def prepare(self, df: Any, params: dict[str, Any]) -> "_Request | None":
        try:
            return self._prepare(df, params)
        except (KeyError, ValueError, TypeError):
            return None

    def _prepare(self, df: Any, params: dict[str, Any]) -> "_Request | None":
        if params.get("time_column") is not None:
            return None
        src = self._module()
        window_n = int(params.get("window_n", src.DEFAULT_WINDOW_N))
        if window_n < _bands.MIN_STAT_OBS:
            return None
        q_low = float(params.get("q_low", src.DEFAULT_Q_LOW))
        q_high = float(params.get("q_high", src.DEFAULT_Q_HIGH))
        if not (0.0 < q_low < q_high < 1.0):
            return None
        k_events = int(params.get("k_events", _evq.DEFAULT_K_EVENTS))
        if k_events < 1:
            return None
        maxbars = int(params.get("maxbars", src.DEFAULT_MAXBARS))
        if maxbars < 3:
            return None
        band_method = str(params.get("band_method", src.DEFAULT_BAND_METHOD)).lower()
        if band_method not in src.BAND_METHODS:
            return None
        empirical_n = int(params.get("empirical_n", src.DEFAULT_EMP_N))
        if empirical_n < 2:
            return None
        n_cov = int(params.get("n_cov", src.DEFAULT_N_COV))
        if n_cov < 2:
            return None

        values = np.asarray(src.build_tickvol(df), dtype=np.float64).ravel()
        n = int(values.size)
        if n < _bands.MIN_STAT_OBS + 1:
            return None

        resolved = resolve_times(df, None)
        stamps = resolved.to_numpy()
        if not np.issubdtype(stamps.dtype, np.datetime64):
            stamps = pd.to_datetime(resolved).to_numpy()
        times = stamps.astype("datetime64[s]").astype("int64")

        return _Request(
            values=values, times=times, n=n, window_n=window_n,
            q_low=q_low, q_high=q_high,
            q_out=params.get("q_out", _evq.DEFAULT_Q_OUT), k_events=k_events,
            maxbars=maxbars, band_method=band_method, empirical_n=empirical_n,
            show_metrics=bool(params.get("show_metrics", True)), n_cov=n_cov,
        )

    def _qo(self, req: "_Request") -> "float | None":
        """有効な極端分位（無効は None＝ext / gpd を NaN にする。共有規約に従う）。"""
        return float(req.q_out) if _evq.q_out_valid(req.q_out, req.q_high) else None

    # ------------------------------------------------------------------ #
    # build / adapt
    # ------------------------------------------------------------------ #
    def build(self, req: "_Request") -> "_State":
        src = self._module()
        m = req.n - 1
        confirmed = req.values[:m]
        levels = src.tickvol_levels(
            confirmed, window_n=req.window_n, q_low=req.q_low, q_high=req.q_high,
            q_out=req.q_out, k_events=req.k_events,
        )
        thr = levels["band_high"]
        up, run_up = _replay_events(confirmed, thr, src)
        trend = src.tickvol_trend(
            confirmed, maxbars=req.maxbars, q_low=req.q_low, q_high=req.q_high,
            band_method=req.band_method, empirical_n=req.empirical_n, q_out=req.q_out,
            n_cov=req.n_cov, with_metrics=req.show_metrics,
        )
        deviations = _deviation(confirmed, trend["mean"], src)
        return self._with_next(
            req, values=confirmed, band_low=levels["band_low"], threshold=thr,
            levels={key: levels[key] for key in _LEVEL_NAMES},
            up=up, run_up=run_up, deviations=deviations, trend=trend, m=m,
        )

    def _with_next(
        self, req: "_Request", *, values, band_low, threshold, levels, up, run_up,
        deviations, trend, m: int
    ) -> "_State":
        """状態を組み、**次バーへ適用する閾値・水準を 1 度だけ**求めて持たせる。

        足内更新（emit）はここで求めた値を読むだけになる（GPD 当てはめは状態遷移時のみ）。
        """
        src = self._module()
        next_low = _bands.causal_stat_latest(
            values, req.window_n, _bands.stat_reducer("quantile", req.q_low)
        )
        next_thr = _bands.causal_stat_latest(
            values, req.window_n, _bands.stat_reducer("quantile", req.q_high)
        )
        next_levels = src.levels_latest(
            list(up), q_out=self._qo(req), k_events=req.k_events
        )
        return _State(
            values=values, band_low=band_low, threshold=threshold, levels=levels,
            up=tuple(up), run_up=tuple(run_up),
            next_band_low=next_low, next_threshold=next_thr,
            next_levels=next_levels, deviations=deviations, trend=trend, m=m,
        )

    def _extend(self, state: "_State", req: "_Request", target: int) -> "_State":
        src = self._module()
        cur = state
        for i in range(state.m, target):
            low, thr = cur.next_band_low, cur.next_threshold
            value = float(req.values[i])
            lv = cur.next_levels
            up, run_up = list(cur.up), list(cur.run_up)
            src.step_excess_event(value - thr, up, run_up)
            bar = self._trend_at(req, cur, i, value)
            cur = self._with_next(
                req,
                values=np.append(cur.values, value),
                band_low=np.append(cur.band_low, low),
                threshold=np.append(cur.threshold, thr),
                levels={k: np.append(cur.levels[k], thr + lv[k]) for k in _LEVEL_NAMES},
                up=up, run_up=run_up,
                deviations=np.append(cur.deviations, bar["deviation"]),
                trend=_append_trend(cur.trend, bar),
                m=i + 1,
            )
        return cur

    def adapt(self, state: "_State", req: "_Request") -> "_State | None":
        m_conf = req.n - 1
        if state.m == m_conf:
            return state if np.array_equal(state.values, req.values[:m_conf]) else None
        if state.m > m_conf:
            if m_conf < _bands.MIN_STAT_OBS:
                return None
            if not np.array_equal(state.values[:m_conf], req.values[:m_conf]):
                return None
            src = self._module()
            up, run_up = _replay_events(
                state.values[:m_conf], state.threshold[:m_conf], src
            )
            return self._with_next(
                req, values=state.values[:m_conf], band_low=state.band_low[:m_conf],
                threshold=state.threshold[:m_conf],
                levels={k: state.levels[k][:m_conf] for k in _LEVEL_NAMES},
                up=up, run_up=run_up,
                deviations=state.deviations[:m_conf],
                trend={k: (None if v is None else v[:m_conf]) for k, v in state.trend.items()},
                m=m_conf,
            )
        if not np.array_equal(state.values, req.values[:state.m]):
            return None
        return self._extend(state, req, m_conf)

    def _trend_at(self, req: "_Request", state: "_State", i: int, value: float) -> dict:
        """バー ``i`` の回帰トレンド 1 点を確定済み ``state``（長さ i）から求める。

        トレンド（mean/β/σ/pred_sd）は **当該バーを含む** 窓の OLS 窓末尾値であり、形成中バーの
        値が変われば動く（btlm_trail F-01 の定義そのもの）。帯（経験分位）は **当該バー除外** の
        乖離率から決まるため確定状態だけで足りる。いずれも btlm_trail の 1 バー入口へ委譲する
        （``window_end_scalar`` / ``empirical_quantile_latest`` / ``ols_band`` / ``empirical_band`` /
        ``deviation_ratio`` / ``coverage_latest``）＝計算式を写さない。
        """
        trail = _trail_src()
        w = min(int(req.maxbars), i + 1)
        window = np.append(state.values[i - w + 1:i], value) if w > 1 else np.array([value])
        mean, pred_sd, beta, sigma = trail.window_end_scalar(window)

        qo = _trend_q_out(req)
        if req.band_method == "ols":
            low = trail.ols_band(mean, pred_sd, req.q_low)
            high = trail.ols_band(mean, pred_sd, req.q_high)
            off_hi = trail.ols_band(mean, pred_sd, qo) if qo is not None else None
            off_lo = trail.ols_band(mean, pred_sd, 1.0 - qo) if qo is not None else None
        else:
            prior = state.deviations
            emp = lambda q: trail.empirical_quantile_latest(prior, req.empirical_n, q)  # noqa: E731
            low = trail.empirical_band(mean, emp(req.q_low))
            high = trail.empirical_band(mean, emp(req.q_high))
            off_hi = trail.empirical_band(mean, emp(qo)) if qo is not None else None
            off_lo = trail.empirical_band(mean, emp(1.0 - qo)) if qo is not None else None

        cov = None
        if req.show_metrics:
            n_cov = int(req.n_cov)
            start = max(0, state.m - n_cov + 1)
            cov = trail.coverage_latest(
                np.append(state.values[start:], value),
                np.append(state.trend["band_low"][start:], low),
                np.append(state.trend["band_high"][start:], high),
                n_cov,
            )
        return {
            "mean": mean, "band_low": low, "band_high": high,
            "off_low": off_lo, "off_high": off_hi,
            "beta": beta if req.show_metrics else None,
            "sigma": sigma if req.show_metrics else None,
            "band_hit_rate": cov,
            "deviation": float(trail.deviation_ratio(value, mean)),
        }

    # ------------------------------------------------------------------ #
    # emit（非破壊・末尾 K 点）
    # ------------------------------------------------------------------ #
    def emit(
        self, state: "_State", req: "_Request", skeleton: list, k: "int | None"
    ) -> "list[dict] | None":
        if k is None or k <= 0 or state.m != req.n - 1:
            return None
        thr = state.next_threshold
        value = float(req.values[req.n - 1])
        fixed = {_HIST_NAME: (state.values, value)}
        for key, name in _LEVEL_NAMES.items():
            fixed[name] = (state.levels[key], thr + state.next_levels[key])
        # 回帰トレンドは形成中バーの値に依存する（当該バーを含む窓の OLS）＝ここで 1 点求める。
        bar = self._trend_at(req, state, req.n - 1, value)
        for key, name in _TREND_FIXED_NAMES.items():
            confirmed = state.trend.get(key)
            if confirmed is None:
                continue          # q_out 無効 / show_metrics=False＝系列自体が無い
            fixed[name] = (confirmed, bar[key])
        # 帯は分位依存の動的名。水準帯（tickvol_q{pct}）とトレンド帯（tickvol_trend_q{pct}）の
        #   4 本を出現順（lwc_chart の emit 順＝水準 下,上 → トレンド 下,上）で割り当てる。
        band_names = [
            s.get("name") for s in skeleton
            if "data" in s and s.get("name") not in fixed
        ]
        if len(band_names) != 4 or len(set(band_names)) != 4:
            return None
        fixed[band_names[0]] = (state.band_low, state.next_band_low)
        fixed[band_names[1]] = (state.threshold, thr)
        fixed[band_names[2]] = (state.trend["band_low"], bar["band_low"])
        fixed[band_names[3]] = (state.trend["band_high"], bar["band_high"])

        out: list[dict[str, Any]] = []
        for entry in skeleton:
            if "data" not in entry:
                out.append(dict(entry))  # データを持たない payload はそのまま。
                continue
            found = fixed.get(entry.get("name"))
            if found is None:
                return None
            confirmed, last = found
            out.append({**entry, "data": _tail_points(confirmed, last, req.times, req.n, k)})
        return out


def _replay_events(values: np.ndarray, threshold: np.ndarray, src: Any) -> tuple[list, list]:
    """確定系列からイベント観測列・進行中エピソードを再生する（src の 1 バー入口を使う）。"""
    up: list = []
    run_up: list = []
    for t in range(int(values.size)):
        src.step_excess_event(values[t] - threshold[t], up, run_up)
    return up, run_up


def _tail_points(
    confirmed: np.ndarray, last: float, times: np.ndarray, n: int, k: int
) -> list[dict[str, Any]]:
    """末尾から k 点（NaN は出さない）を系列 JSON の data 形式で返す（emit 規約と同一）。"""
    points: list[dict[str, Any]] = []
    i = n - 1
    while i >= 0 and len(points) < k:
        v = last if i == n - 1 else confirmed[i]
        if v == v:  # NaN 除外
            points.append({"time": int(times[i]), "value": float(v)})
        i -= 1
    points.reverse()
    return points


def _trail_src() -> Any:
    """btlm_trail src（1 バー入口の提供元）を一意名でロードする（read-only）。"""
    return indicator_src("btlm_trail")


def _trend_q_out(req: "_Request") -> "float | None":
    """外れ値分位の有効性（btlm_trail F-08 と同一規約: q_high < q_out < 1）。"""
    try:
        qo = float(req.q_out)
    except (TypeError, ValueError):
        return None
    return qo if req.q_high < qo < 1.0 else None


def _deviation(values: np.ndarray, mean: np.ndarray, src: Any) -> np.ndarray:
    """確定系列の乖離率（経験分位帯の材料）。定義は btlm_trail の deviation_ratio。"""
    del src
    return np.asarray(_trail_src().deviation_ratio(values, mean), dtype=np.float64)


def _append_trend(trend: dict, bar: dict) -> dict:
    """確定トレンド配列へ 1 点追記した新しい dict を返す（None のキーは None のまま）。"""
    out = {}
    for key in _TREND_KEYS:
        cur = trend.get(key)
        out[key] = None if cur is None else np.append(cur, bar[key])
    return out
