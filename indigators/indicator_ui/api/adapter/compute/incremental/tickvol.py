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
from adapter.compute.incremental._emit import tail_points

from adapter.compute.call_binding import indicator_src

#: 系列名（lwc_chart の emit 順と同じ。skeleton との突合はこの名前で行う）。
_HIST_NAME = "tickvol"
_LEVEL_NAMES = {
    "med": "tickvol_evq_med_hi",
    "ext": "tickvol_evq_ext_hi",
    "gpd": "tickvol_gpd_hi",
}


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
        return self._with_next(
            req, values=confirmed, band_low=levels["band_low"], threshold=thr,
            levels={key: levels[key] for key in _LEVEL_NAMES},
            up=up, run_up=run_up, m=m,
        )

    def _with_next(
        self, req: "_Request", *, values, band_low, threshold, levels, up, run_up,
        m: int
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
            next_levels=next_levels, m=m,
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
            cur = self._with_next(
                req,
                values=np.append(cur.values, value),
                band_low=np.append(cur.band_low, low),
                threshold=np.append(cur.threshold, thr),
                levels={k: np.append(cur.levels[k], thr + lv[k]) for k in _LEVEL_NAMES},
                up=up, run_up=run_up,
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
                m=m_conf,
            )
        if not np.array_equal(state.values, req.values[:state.m]):
            return None
        return self._extend(state, req, m_conf)

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
        # 正常帯は分位依存の動的名（tickvol_q{pct}）。出現順（lwc_chart の emit 順＝下,上）で割り当てる。
        band_names = [
            s.get("name") for s in skeleton
            if "data" in s and s.get("name") not in fixed
        ]
        if len(band_names) != 2 or len(set(band_names)) != 2:
            return None
        fixed[band_names[0]] = (state.band_low, state.next_band_low)
        fixed[band_names[1]] = (state.threshold, thr)

        out: list[dict[str, Any]] = []
        for entry in skeleton:
            if "data" not in entry:
                out.append(dict(entry))  # データを持たない payload はそのまま。
                continue
            found = fixed.get(entry.get("name"))
            if found is None:
                return None
            confirmed, last = found
            out.append({**entry, "data": tail_points(confirmed, last, req.times, req.n, k)})
        return out


def _replay_events(values: np.ndarray, threshold: np.ndarray, src: Any) -> tuple[list, list]:
    """確定系列からイベント観測列・進行中エピソードを再生する（src の 1 バー入口を使う）。"""
    up: list = []
    run_up: list = []
    for t in range(int(values.size)):
        src.step_excess_event(values[t] - threshold[t], up, run_up)
    return up, run_up




