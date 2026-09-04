"""profit_rsi の増分器（ISSUE-249・内部設計_latest増分計算.md §5.2/§5.3 と同一設計）。

真因の除去:
    従来の ``latest`` は末尾 1 点を得るために ``adapter.compute`` を全件で呼び、窓全体を
    再計算していた（実測 1386 本で **152.8ms**。うち水準 152.3ms＝ほぼ全部）。本増分器は
    確定バーまでの状態（Wilder 平滑・分位窓・POT エピソード）を保持し、形成中バー 1 本ぶん
    だけ進める。所要は窓長に依らず一定になる。

参照実装（無改変・計算式を写さない）:
    すべて profit_rsi / 共有 src の **公開 1 バー入口**へ委譲する。
      - RSI 本体   : ``mql_builtins.compute_rsi_stateful``（ISSUE-249 で追加。漸化式は
                     ``_rsi_seed`` / ``_rsi_advance`` の 1 箇所のみ。``compute_rsi`` も同じ
                     部品を通るため bit 一致）。
      - 閾値（帯） : ``common.marod_bands.causal_stat_latest``（当該バー除外の因果分位）。
      - 超過       : ``levels.excess_fraction`` / ``levels.headroom``（余地割合）。
      - イベント   : ``levels.step_excess_event``（1 バーぶんのエピソード確定・唯一の定義）。
      - 水準       : ``levels.levels_latest``（確定観測列の次バーに適用する水準・唯一の定義）。
    系列 metadata（名前・色・描画ヒント）も書き写さず、``incremental_state`` が実計算から
    採取した骨格を使う。系列名は src の ``RSI_COLUMN`` / ``quantile_column`` /
    ``LEVEL_COLUMNS`` から引く（名前の二重定義を作らない）。

対象外（``prepare`` が None を返し従来経路へ落ちる＝挙動不変）:
    - 実効本数が ``rsi_period + 2`` 未満（seed 未達）。
    - ``k_events < 1`` など参照実装が例外にする入力（翻訳は参照実装へ委ねる）。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import numpy as np

from common.marod_bands import causal_stat_latest, stat_reducer
from common_view.lwc_adapter import resolve_times
from adapter.compute.incremental._emit import tail_points

from adapter.compute.fake_chart import _to_unix_seconds

from common.event_quantiles import _MIN_EVENTS

from adapter.compute.call_binding import indicator_src

#: seed 成立に要する最小本数マージン（``compute_rsi`` は n <= period で全 0）。
_MIN_MARGIN = 2


def _cap_events(events: "list[float] | tuple", k_events: int) -> tuple:
    """POT 観測列を直近 ``max(k_events, _MIN_EVENTS)`` 件へ有界化する（値は不変）。

    水準の算出（``levels_at``）は ``arr[max(0, m - k_events):m]`` しか読まず、``m`` は
    ``m < _MIN_EVENTS`` の可否判定にしか使わない。したがって上限以上を保持しても結果は
    変わらない一方、保持し続けると 1 ステップの所要が本数に比例して伸びる（実測 1386 本で
    5.4ms＝通過条件 5ms 超過）。上限で切ると **窓長非依存**になり、値は構成上不変のまま。
    """
    cap = max(int(k_events), _MIN_EVENTS)
    ev = tuple(events)
    return ev[-cap:] if len(ev) > cap else ev


@dataclass(frozen=True)
class _Request:
    df: Any
    prices: np.ndarray          # 適用価格（昇順・長さ n）
    times: np.ndarray           # UNIX 秒（int64・長さ n）
    n: int
    rsi_period: int
    apply: int
    window_n: int
    q_low: float
    q_high: float
    q_out: "float | None"
    k_events: int


@dataclass(frozen=True)
class _State:
    """確定プレフィクス（長さ m）の各系列と継続状態（不変・emit では書き換えない）。"""

    prices: np.ndarray
    rsi: np.ndarray
    rsi_state: Any              # mql_builtins RsiState（m 本消費後）
    band_low: np.ndarray
    band_high: np.ndarray
    ext_hi: np.ndarray
    gpd_hi: np.ndarray
    ext_lo: np.ndarray
    gpd_lo: np.ndarray
    events_hi: tuple
    events_lo: tuple
    run_hi: tuple
    run_lo: tuple
    m: int


@dataclass(frozen=True)
class _Bar:
    """形成中バー 1 本ぶんの算出値。"""

    rsi: float
    band_low: float
    band_high: float
    ext_hi: float
    gpd_hi: float
    ext_lo: float
    gpd_lo: float
    frac_hi: float
    frac_lo: float


class ProfitRsiIncrementer:
    """``Incrementer`` 実装（profit_rsi）。"""

    _src: Any = None
    _builtins: Any = None

    def _module(self) -> Any:
        if ProfitRsiIncrementer._src is None:
            ProfitRsiIncrementer._src = indicator_src("profit_rsi")
        return ProfitRsiIncrementer._src

    def _mql(self) -> Any:
        if ProfitRsiIncrementer._builtins is None:
            import mql_builtins

            ProfitRsiIncrementer._builtins = mql_builtins
        return ProfitRsiIncrementer._builtins

    # ------------------------------------------------------------------ #
    # prepare
    # ------------------------------------------------------------------ #
    def prepare(self, df: Any, params: dict[str, Any]) -> "_Request | None":
        try:
            return self._prepare(df, params)
        except (KeyError, ValueError, TypeError):
            return None

    def _prepare(self, df: Any, params: dict[str, Any]) -> "_Request | None":
        src = self._module()
        core = src.core if hasattr(src, "core") else None
        levels = src.levels if hasattr(src, "levels") else None
        rsi_mod = src.rsi if hasattr(src, "rsi") else None
        if core is None or levels is None or rsi_mod is None:
            return None

        rsi_period = int(params.get("rsi_period", core.DEFAULT_RSI_PERIOD))
        apply_v = int(params.get("apply", core.DEFAULT_APPLY))
        window_n = int(params.get("window_n", levels.DEFAULT_WINDOW_N))
        q_low = float(params.get("q_low", levels.DEFAULT_Q_LOW))
        q_high = float(params.get("q_high", levels.DEFAULT_Q_HIGH))
        k_events = int(params.get("k_events", 50))
        q_out_raw = params.get("q_out", None)
        if rsi_period < 2 or k_events < 1:
            return None

        o, h, low_, c = rsi_mod._extract_ohlc(df)
        n = int(c.size)
        if n < rsi_period + _MIN_MARGIN:
            return None
        from common import applied_price

        prices = applied_price(core.APPLY_TO_PRICE(apply_v), o, h, low_, c)
        # q_out の無効化規約は参照実装（levels.rsi_levels）と同一の判定へ委譲する。
        from common import event_quantiles as _evq

        q_out = float(q_out_raw) if _evq.q_out_valid(q_out_raw, q_high) else None
        return _Request(
            df=df, prices=np.asarray(prices, dtype=np.float64),
            times=np.asarray(resolve_times(df, params.get("time_column"))), n=n,
            rsi_period=rsi_period, apply=apply_v, window_n=window_n,
            q_low=q_low, q_high=q_high, q_out=q_out, k_events=k_events,
        )

    # ------------------------------------------------------------------ #
    # 1 バーぶんの算出（非破壊）
    # ------------------------------------------------------------------ #
    def _bar(self, req: "_Request", state: "_State", i: int) -> "_Bar":
        src = self._module()
        levels = src.levels
        mql = self._mql()

        # RSI: 確定状態から 1 本だけ進める（状態は書き換えない）。
        rsi_arr, _ = mql.compute_rsi_stateful(
            req.prices[i:i + 1], period=req.rsi_period, state=state.rsi_state
        )
        rsi_v = float(rsi_arr[0])

        # 閾値（正常帯）: 当該バー除外の因果ローリング分位。
        prior = state.rsi[:i]
        lo = causal_stat_latest(prior, req.window_n, stat_reducer("quantile", req.q_low))
        hi = causal_stat_latest(prior, req.window_n, stat_reducer("quantile", req.q_high))

        # 水準: 確定観測（バー i より前）だけから決まる（levels_latest の契約）。
        lv = levels.levels_latest(
            list(state.events_hi), list(state.events_lo),
            q_out=req.q_out, k_events=req.k_events,
        )
        head_hi = levels.headroom(np.asarray([hi], dtype=np.float64), upper=True)[0]
        head_lo = levels.headroom(np.asarray([lo], dtype=np.float64), upper=False)[0]
        ext_hi = hi + lv["ext_hi"] * head_hi
        gpd_hi = hi + lv["gpd_hi"] * head_hi
        ext_lo = lo - lv["ext_lo"] * head_lo
        gpd_lo = lo - lv["gpd_lo"] * head_lo

        frac_hi = float(levels.excess_fraction([rsi_v], [hi], upper=True)[0])
        frac_lo = float(levels.excess_fraction([rsi_v], [lo], upper=False)[0])
        return _Bar(
            rsi=rsi_v, band_low=lo, band_high=hi,
            ext_hi=ext_hi, gpd_hi=gpd_hi, ext_lo=ext_lo, gpd_lo=gpd_lo,
            frac_hi=frac_hi, frac_lo=frac_lo,
        )

    # ------------------------------------------------------------------ #
    # build / adapt
    # ------------------------------------------------------------------ #
    def build(self, req: "_Request") -> "_State":
        """確定プレフィクス（末尾 1 本を除く全件）を参照実装で 1 回だけ組む。"""
        src = self._module()
        levels = src.levels
        mql = self._mql()
        m = req.n - 1

        rsi_arr, rsi_state = mql.compute_rsi_stateful(
            req.prices[:m], period=req.rsi_period
        )
        res = src.rsi_levels(
            rsi_arr, window_n=req.window_n, q_low=req.q_low, q_high=req.q_high,
            q_out=req.q_out, k_events=req.k_events,
        )
        # POT のイベント状態（確定分）を 1 バー入口で組み直す（唯一の定義を通る）。
        ev_hi: list[float] = []
        ev_lo: list[float] = []
        run_hi: list[float] = []
        run_lo: list[float] = []
        f_hi = levels.excess_fraction(rsi_arr, res["band_high"], upper=True)
        f_lo = levels.excess_fraction(rsi_arr, res["band_low"], upper=False)
        for t in range(m):
            levels.step_excess_event(f_hi[t], ev_hi, run_hi)
            levels.step_excess_event(f_lo[t], ev_lo, run_lo)
        return _State(
            prices=req.prices[:m], rsi=rsi_arr, rsi_state=rsi_state,
            band_low=res["band_low"], band_high=res["band_high"],
            ext_hi=res["ext_hi"], gpd_hi=res["gpd_hi"],
            ext_lo=res["ext_lo"], gpd_lo=res["gpd_lo"],
            events_hi=_cap_events(ev_hi, req.k_events),
            events_lo=_cap_events(ev_lo, req.k_events),
            run_hi=tuple(run_hi), run_lo=tuple(run_lo), m=m,
        )

    def _extend(self, state: "_State", req: "_Request", target: int) -> "_State":
        """確定した分（``state.m`` → ``target``）だけ前進した新しい状態を返す。"""
        src = self._module()
        levels = src.levels
        mql = self._mql()
        cur = state
        for i in range(state.m, target):
            bar = self._bar(req, cur, i)
            ev_hi, ev_lo = list(cur.events_hi), list(cur.events_lo)
            run_hi, run_lo = list(cur.run_hi), list(cur.run_lo)
            levels.step_excess_event(bar.frac_hi, ev_hi, run_hi)
            levels.step_excess_event(bar.frac_lo, ev_lo, run_lo)
            _, rsi_state = mql.compute_rsi_stateful(
                req.prices[i:i + 1], period=req.rsi_period, state=cur.rsi_state
            )
            app = np.append
            cur = _State(
                prices=req.prices[:i + 1],
                rsi=app(cur.rsi, bar.rsi), rsi_state=rsi_state,
                band_low=app(cur.band_low, bar.band_low),
                band_high=app(cur.band_high, bar.band_high),
                ext_hi=app(cur.ext_hi, bar.ext_hi), gpd_hi=app(cur.gpd_hi, bar.gpd_hi),
                ext_lo=app(cur.ext_lo, bar.ext_lo), gpd_lo=app(cur.gpd_lo, bar.gpd_lo),
                events_hi=_cap_events(ev_hi, req.k_events),
                events_lo=_cap_events(ev_lo, req.k_events),
                run_hi=tuple(run_hi), run_lo=tuple(run_lo), m=i + 1,
            )
        return cur

    def adapt(self, state: "_State", req: "_Request") -> "_State | None":
        m_conf = req.n - 1
        if state.m == m_conf:
            return state if np.array_equal(state.prices, req.prices[:m_conf]) else None
        if state.m > m_conf:
            return None   # 窓の縮小は状態（POT エピソード）を巻き戻せないため再構築へ落とす。
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
        src = self._module()
        rsi_mod = src.rsi
        bar = self._bar(req, state, req.n - 1)

        cols = rsi_mod.LEVEL_COLUMNS
        table: dict[str, tuple[Any, float]] = {
            rsi_mod.RSI_COLUMN: (state.rsi, bar.rsi),
            rsi_mod.quantile_column(req.q_low): (state.band_low, bar.band_low),
            rsi_mod.quantile_column(req.q_high): (state.band_high, bar.band_high),
            cols["ext_hi"]: (state.ext_hi, bar.ext_hi),
            cols["gpd_hi"]: (state.gpd_hi, bar.gpd_hi),
            cols["ext_lo"]: (state.ext_lo, bar.ext_lo),
            cols["gpd_lo"]: (state.gpd_lo, bar.gpd_lo),
        }
        out: list[dict[str, Any]] = []
        for entry in skeleton:
            found = table.get(entry.get("name"))
            if found is None or found[0] is None:
                return None
            confirmed, last = found
            out.append({**entry, "data": tail_points(confirmed, last, req.times, req.n, k)})
        return out


