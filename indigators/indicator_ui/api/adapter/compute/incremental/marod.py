"""MAROD 系オシレータの増分器（ISSUE-233 S5・ma_marod / btlm_trail_marod）。

両指標は構造が同一である（実測 ma_marod 118.1ms / btlm_trail_marod 148.5ms・窓 1386 本）:

    値系列（乖離率 %） → 因果ローリング分位バンド（当該バー除外） → 外れ値イベント分位水準

差は「基準線をどう引くか」だけ（ma_marod=移動平均 / btlm_trail_marod=OLS 窓末尾トレンド）。
本モジュールは共通部を 1 つの増分器に持ち、基準線だけを ``_Baseline`` 実装で差し替える。

真因の除去:
    分位バンドもイベント分位も **当該バーを除く** 因果統計であり（ISSUE-141 の規約）、
    形成中バーの水準は確定済みの観測だけで決まる。つまり足内更新のたびに窓全体を
    走り直す必要はない。確定バーまでの観測（値系列・イベント列）を状態として保持し、
    形成中バーは src の 1 バー入口で 1 点だけ求める。

参照実装（無改変・計算式を写さない）:
    ``common.marod_bands``: ``marod_percent`` / ``causal_stat_latest`` / ``stat_reducer``
    ``common.event_quantiles``: ``step_events`` / ``event_levels_latest``
    基準線: moving_averages の ``*_on_buffer``（ma_marod）/ btlm_trail の
    ``window_end_scalar``（btlm_trail_marod）。いずれもローリング版が同じ関数を各バーで
    呼ぶ構成であり、定義は 1 箇所しかない。

対象外（``prepare`` が None を返し従来経路へ落ちる＝挙動不変）:
    パラメータ不正（分位ペア・window_n・k_events・event_agg・length/maxbars）、
    本数不足（基準線の warm-up 区間）、``time_column`` 指定あり。
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

_EVQ_KEYS = ("med_hi", "ext_hi", "med_lo", "ext_lo")


@dataclass(frozen=True)
class _Request:
    df: Any
    prices: np.ndarray       # 解決済みソース系列（長さ n）
    times: np.ndarray        # UNIX 秒（int64・長さ n）
    n: int
    window_n: int
    q_low: float
    q_high: float
    q_out: Any
    k_events: int
    event_agg: str
    baseline: dict           # 基準線パラメータ（ma_type/length もしくは maxbars）
    prefix: str              # 系列名の接頭辞（compute_id と同じ）


@dataclass(frozen=True)
class _State:
    prices: np.ndarray
    values: np.ndarray                 # MAROD 系列（確定・長さ m）
    band_low: np.ndarray
    band_high: np.ndarray
    evq: dict                          # キー → 確定 4 系列（長さ m）
    up: tuple                          # 確定した上側イベント観測（時系列順）
    dn: tuple
    run_up: tuple                      # 進行中エピソード（上側）
    run_dn: tuple
    baseline_state: Any                # 基準線の内部状態（実装依存）
    m: int


class _MovingAverageBaseline:
    """ma_marod の基準線（moving_averages の ``*_on_buffer`` 契約で増分計算する）。"""

    name = "ma_marod"

    def __init__(self) -> None:
        self._src = None

    def _module(self):
        if self._src is None:
            self._src = indicator_src("moving_averages")
        return self._src

    def params(self, params: dict) -> "dict | None":
        src = self._module()
        ma_type = str(params.get("ma_type", "ema")).lower()
        if ma_type not in set(src.MA_TYPES):
            return None
        length = int(params.get("length", 50))
        if length < 2:
            return None
        return {"ma_type": ma_type, "length": length,
                "valid_from": 0 if ma_type in src.MA_FROM_ZERO else length - 1}

    def min_bars(self, baseline: dict) -> int:
        # ``prev_calculated`` 契約の継続開始位置がシード領域を越える本数。
        return baseline["length"] + 3

    def build(self, req: "_Request", m: int) -> tuple[np.ndarray, Any]:
        """確定プレフィクスの基準線と状態を返す。"""
        src = self._module()
        b = req.baseline
        buffer = np.zeros(m, dtype=np.float64)
        lwma = None
        if b["ma_type"] == "lwma":
            _, lwma = src.linear_weighted_ma_on_buffer_stateful(
                m, 0, b["length"], req.prices[:m], buffer
            )
        else:
            self._fn(b["ma_type"])(m, 0, 0, b["length"], req.prices[:m], buffer)
        return self._masked(buffer, b), (buffer, lwma)

    def advance(self, req: "_Request", state: Any, m_from: int, m_to: int) -> Any:
        """基準線の状態を ``m_from`` → ``m_to`` へ進める（値は latest が返す）。"""
        src = self._module()
        b = req.baseline
        buffer_prev, lwma = state
        buffer = np.zeros(m_to, dtype=np.float64)
        buffer[:m_from] = buffer_prev[:m_from]
        if b["ma_type"] == "lwma":
            _, lwma = src.linear_weighted_ma_on_buffer_stateful(
                m_to, 0, b["length"], req.prices[:m_to], buffer, lwma
            )
        else:
            self._fn(b["ma_type"])(m_to, m_from, 0, b["length"], req.prices[:m_to], buffer)
        return (buffer, lwma)

    def truncate(self, state: Any, m: int) -> "Any | None":
        buffer, lwma = state
        if lwma is not None:
            return None  # lwma の走行和は巻き戻せない（再構築する）。
        return (buffer[:m], None)

    def latest(self, req: "_Request", state: Any, i: int) -> float:
        """バー ``i``（確定済みは ``i`` 本）の基準線値を非破壊に求める。"""
        src = self._module()
        b = req.baseline
        buffer_prev, lwma = state
        buffer = np.empty(i + 1, dtype=np.float64)
        buffer[:i] = buffer_prev[:i]
        buffer[i] = 0.0
        if b["ma_type"] == "lwma":
            src.linear_weighted_ma_on_buffer_stateful(
                i + 1, 0, b["length"], req.prices[:i + 1], buffer, lwma
            )
        else:
            self._fn(b["ma_type"])(i + 1, i, 0, b["length"], req.prices[:i + 1], buffer)
        return np.nan if i < b["valid_from"] else float(buffer[i])

    def _fn(self, ma_type: str):
        src = self._module()
        return {"sma": src.simple_ma_on_buffer, "ema": src.exponential_ma_on_buffer,
                "smma": src.smoothed_ma_on_buffer}[ma_type]

    @staticmethod
    def _masked(buffer: np.ndarray, b: dict) -> np.ndarray:
        out = np.array(buffer, dtype=np.float64, copy=True)
        if b["valid_from"] > 0:
            out[:b["valid_from"]] = np.nan
        return out


class _TrendLineBaseline:
    """btlm_trail_marod の基準線（btlm_trail の窓末尾 OLS を 1 窓だけ計算する）。"""

    name = "btlm_trail_marod"

    def __init__(self) -> None:
        self._src = None

    def _module(self):
        if self._src is None:
            self._src = indicator_src("btlm_trail")
        return self._src

    def params(self, params: dict) -> "dict | None":
        maxbars = int(params.get("maxbars", 100))
        return None if maxbars < 3 else {"maxbars": maxbars}

    def min_bars(self, baseline: dict) -> int:
        return baseline["maxbars"] + 2

    def build(self, req: "_Request", m: int) -> tuple[np.ndarray, Any]:
        src = self._module()
        mean, _pred_sd, _beta, _sigma = src.rolling_ols_window_end(
            req.prices[:m], req.baseline["maxbars"]
        )
        return mean, None

    def advance(self, req: "_Request", state: Any, m_from: int, m_to: int) -> Any:
        return None  # 窓独立（状態を持たない）。

    def truncate(self, state: Any, m: int) -> Any:
        return None

    def latest(self, req: "_Request", state: Any, i: int) -> float:
        src = self._module()
        w = min(req.baseline["maxbars"], i + 1)
        return float(src.window_end_scalar(req.prices[i - w + 1: i + 1])[0])


class MarodIncrementer:
    """``Incrementer`` 実装（MAROD 系オシレータ・基準線だけを差し替えて共有する）。"""

    def __init__(self, baseline: Any, prefix: str) -> None:
        self._baseline = baseline
        self._prefix = prefix

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
        baseline = self._baseline.params(params)
        if baseline is None:
            return None
        window_n = int(params.get("window_n", 500))
        if window_n < _bands.MIN_STAT_OBS:
            return None
        ql, qh = float(params.get("q_low", 0.05)), float(params.get("q_high", 0.95))
        if not (0.0 < ql < qh < 1.0):
            return None
        k_events = int(params.get("k_events", 50))
        if k_events < 1:
            return None
        event_agg = str(params.get("event_agg", "episode")).lower()
        if event_agg not in ("episode", "bar"):
            return None

        prices = self._resolve_prices(df, str(params.get("source", "close")))
        n = int(prices.size)
        if n < self._baseline.min_bars(baseline) + 1:
            return None

        resolved = resolve_times(df, None)
        stamps = resolved.to_numpy()
        if not np.issubdtype(stamps.dtype, np.datetime64):
            stamps = pd.to_datetime(resolved).to_numpy()
        times = stamps.astype("datetime64[s]").astype("int64")

        return _Request(
            df=df, prices=prices, times=times, n=n, window_n=window_n,
            q_low=ql, q_high=qh, q_out=params.get("q_out", 0.99),
            k_events=k_events, event_agg=event_agg, baseline=baseline,
            prefix=self._prefix,
        )

    def _resolve_prices(self, df: Any, source: str) -> np.ndarray:
        """ソース解決は指標 src の公開関数へ委譲する（写像は両指標で同一）。"""
        src = indicator_src("btlm_trail")
        return np.asarray(src.resolve_source(df, source), dtype=np.float64).ravel()

    # ------------------------------------------------------------------ #
    # build / adapt
    # ------------------------------------------------------------------ #
    def build(self, req: "_Request") -> "_State":
        m = req.n - 1
        mean, baseline_state = self._baseline.build(req, m)
        values = _bands.marod_percent(req.prices[:m], mean)
        band_low, band_high = _bands.quantile_bands(
            values, window_n=req.window_n, q_low=req.q_low, q_high=req.q_high
        )
        evq_all = _evq.outlier_event_quantiles(
            values, band_low, band_high, q_high=req.q_high, q_out=req.q_out,
            k_events=req.k_events, event_agg=req.event_agg, include_all=False,
        )
        up, dn, run_up, run_dn = _replay_events(values, band_low, band_high, req.event_agg)
        return _State(
            prices=req.prices[:m], values=values, band_low=band_low, band_high=band_high,
            evq={key: evq_all[key] for key in _EVQ_KEYS},
            up=tuple(up), dn=tuple(dn), run_up=tuple(run_up), run_dn=tuple(run_dn),
            baseline_state=baseline_state, m=m,
        )

    def _bar(self, req: "_Request", state: "_State", i: int) -> dict:
        """バー ``i`` の値・バンド・イベント水準を確定済み ``state``（長さ i）から求める。"""
        mean = self._baseline.latest(req, state.baseline_state, i)
        value = float(_bands.marod_percent(np.array([req.prices[i]]), np.array([mean]))[0])
        low = _bands.causal_stat_latest(
            state.values, req.window_n, _bands.stat_reducer("quantile", req.q_low)
        )
        high = _bands.causal_stat_latest(
            state.values, req.window_n, _bands.stat_reducer("quantile", req.q_high)
        )
        levels = _evq.event_levels_latest(
            list(state.up), list(state.dn), q_high=req.q_high,
            q_out=req.q_out, k_events=req.k_events,
        )
        return {"value": value, "low": low, "high": high, "levels": levels}

    def _extend(self, state: "_State", req: "_Request", target: int) -> "_State":
        cur = state
        for i in range(state.m, target):
            bar = self._bar(req, cur, i)
            up, dn = list(cur.up), list(cur.dn)
            run_up, run_dn = list(cur.run_up), list(cur.run_dn)
            _evq.step_events(
                bar["value"], bar["low"], bar["high"], req.event_agg, up, dn, run_up, run_dn
            )
            baseline_state = self._baseline.advance(req, cur.baseline_state, cur.m, i + 1)
            cur = _State(
                prices=req.prices[:i + 1],
                values=np.append(cur.values, bar["value"]),
                band_low=np.append(cur.band_low, bar["low"]),
                band_high=np.append(cur.band_high, bar["high"]),
                evq={k: np.append(cur.evq[k], bar["levels"][k]) for k in _EVQ_KEYS},
                up=tuple(up), dn=tuple(dn), run_up=tuple(run_up), run_dn=tuple(run_dn),
                baseline_state=baseline_state, m=i + 1,
            )
        return cur

    def adapt(self, state: "_State", req: "_Request") -> "_State | None":
        m_conf = req.n - 1
        if state.m == m_conf:
            return state if np.array_equal(state.prices, req.prices[:m_conf]) else None
        if state.m > m_conf:
            if m_conf < self._baseline.min_bars(req.baseline):
                return None
            if not np.array_equal(state.prices[:m_conf], req.prices[:m_conf]):
                return None
            baseline_state = self._baseline.truncate(state.baseline_state, m_conf)
            if state.baseline_state is not None and baseline_state is None:
                return None  # 巻き戻せない状態（lwma の走行和）＝再構築する。
            up, dn, run_up, run_dn = _replay_events(
                state.values[:m_conf], state.band_low[:m_conf], state.band_high[:m_conf],
                req.event_agg,
            )
            return _State(
                prices=state.prices[:m_conf], values=state.values[:m_conf],
                band_low=state.band_low[:m_conf], band_high=state.band_high[:m_conf],
                evq={k: state.evq[k][:m_conf] for k in _EVQ_KEYS},
                up=tuple(up), dn=tuple(dn), run_up=tuple(run_up), run_dn=tuple(run_dn),
                baseline_state=baseline_state, m=m_conf,
            )
        if not np.array_equal(state.prices, req.prices[:state.m]):
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
        bar = self._bar(req, state, req.n - 1)

        fixed = {
            req.prefix: (state.values, bar["value"]),
            f"{req.prefix}_evq_med_hi": (state.evq["med_hi"], bar["levels"]["med_hi"]),
            f"{req.prefix}_evq_med_lo": (state.evq["med_lo"], bar["levels"]["med_lo"]),
            f"{req.prefix}_evq_ext_hi": (state.evq["ext_hi"], bar["levels"]["ext_hi"]),
            f"{req.prefix}_evq_ext_lo": (state.evq["ext_lo"], bar["levels"]["ext_lo"]),
        }
        # 分位バンド 2 本は q_low/q_high 依存名。data を持つ系列のうち固定名以外を
        # 出現順（lwc_chart の emit 順＝low, high）で割り当てる。
        quantile_names = [
            s.get("name") for s in skeleton
            if "data" in s and s.get("name") not in fixed
        ]
        if len(quantile_names) != 2 or len(set(quantile_names)) != 2:
            return None
        fixed[quantile_names[0]] = (state.band_low, bar["low"])
        fixed[quantile_names[1]] = (state.band_high, bar["high"])

        out: list[dict[str, Any]] = []
        for entry in skeleton:
            if "data" not in entry:
                out.append(dict(entry))  # 水平線（0% 基準線）はデータを持たない＝そのまま。
                continue
            found = fixed.get(entry.get("name"))
            if found is None:
                return None
            confirmed, last = found
            out.append({**entry, "data": _tail_points(confirmed, last, req.times, req.n, k)})
        return out


def _replay_events(
    values: np.ndarray, band_low: np.ndarray, band_high: np.ndarray, event_agg: str
) -> tuple[list, list, list, list]:
    """確定系列からイベント観測列・進行中エピソードを再生する（参照実装の 1 バー入口を使う）。"""
    up: list = []
    dn: list = []
    run_up: list = []
    run_dn: list = []
    for t in range(int(values.size)):
        _evq.step_events(
            values[t], band_low[t], band_high[t], event_agg, up, dn, run_up, run_dn
        )
    return up, dn, run_up, run_dn


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
