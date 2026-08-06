"""btlm_trail の増分器（ISSUE-233 S2/S3/S4・内部設計_latest増分計算.md §5.2）。

真因の除去（実測・窓 1386 本 / maxbars=115 / empirical_n=495 / n_cov=495）:

| 処理 | 従来（窓全体） | 増分（末尾 1 点） |
|---|---|---|
| 窓末尾 OLS（mean/β/σ/pred_sd） | 28.9ms | 0.021ms |
| 経験分位 1 本ぶん | 51.4ms | 0.037ms |
| 被覆率（band_hit_rate） | 5.0ms | O(n_cov) の numpy 和 |

いずれも「各バーで同じ計算を繰り返すループ」であり、末尾 1 点だけが要る latest 経路が
ループ全体を走る必要はない。本増分器は確定バーまでの各系列を状態として保持し、形成中バー
1 本ぶんだけを src の **1 バー入口** で計算する。

参照実装（無改変・計算式を写さない）:
    ``window_end_scalar`` / ``empirical_quantile_latest`` / ``coverage_latest`` /
    ``deviation_ratio`` / ``ols_band`` / ``empirical_band``（いずれも btlm_trail src の公開
    関数。ローリング版が同じ関数を各バーで呼ぶ構成にしてあり、定義は 1 箇所しかない）。
    系列名・色・描画ヒントも書き写さず、``incremental_state`` が実計算から採取した骨格を使う。

対象外（``prepare`` が None を返し従来経路へ落ちる＝挙動不変）:
    パラメータ不正（分位ペア・band_method・maxbars<3）、close 列が無い経験分位、
    本数が ``maxbars + 2`` 未満（回帰窓が満たない区間）、``time_column`` 指定あり。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import numpy as np
import pandas as pd

from common_view.lwc_adapter import resolve_times
from adapter.compute.incremental._emit import tail_points

from adapter.compute.call_binding import indicator_src

# 固定名の系列（分位バンド 2 本だけは q_low/q_high 依存名のため骨格の出現順で解決する）。
_NAME_MEAN = "btlm_trail_mean"
_NAME_OFF_HI = "btlm_trail_off_hi"
_NAME_OFF_LO = "btlm_trail_off_lo"
_NAME_BETA = "btlm_trail_beta"
_NAME_SIGMA = "btlm_trail_sigma"
_NAME_COV = "btlm_trail_band_hit_rate"
_FIXED_NAMES = (_NAME_MEAN, _NAME_OFF_HI, _NAME_OFF_LO, _NAME_BETA, _NAME_SIGMA, _NAME_COV)


@dataclass(frozen=True)
class _Request:
    df: Any                 # 入力 DataFrame（状態の初回構築で参照実装へそのまま渡す）
    source: str
    prices: np.ndarray      # 回帰対象のソース系列（長さ n）
    close: np.ndarray       # close 列（乖離率・被覆率用。無い場合は None）
    times: np.ndarray       # UNIX 秒（int64・長さ n）
    n: int
    maxbars: int
    q_low: float
    q_high: float
    q_out: "float | None"   # 無効化後の値（None＝補助線なし）
    method: str             # "ols" / "empirical"
    empirical_n: int
    n_cov: int


@dataclass(frozen=True)
class _State:
    """確定プレフィクスの各系列（不変・emit では書き換えない）。長さは全て m。"""

    prices: np.ndarray
    mean: np.ndarray
    beta: np.ndarray
    sigma: np.ndarray
    band_low: np.ndarray
    band_high: np.ndarray
    off_low: "np.ndarray | None"
    off_high: "np.ndarray | None"
    cov: "np.ndarray | None"
    deviations: "np.ndarray | None"
    close: "np.ndarray | None"
    m: int


@dataclass(frozen=True)
class _Bar:
    """形成中バー 1 本ぶんの算出値。"""

    mean: float
    beta: float
    sigma: float
    band_low: float
    band_high: float
    off_low: float
    off_high: float
    cov: float


class BtlmTrailIncrementer:
    """``Incrementer`` 実装（btlm_trail）。"""

    _src: Any = None

    def _module(self) -> Any:
        if BtlmTrailIncrementer._src is None:
            BtlmTrailIncrementer._src = indicator_src("btlm_trail")
        return BtlmTrailIncrementer._src

    # ------------------------------------------------------------------ #
    # prepare
    # ------------------------------------------------------------------ #
    def prepare(self, df: Any, params: dict[str, Any]) -> "_Request | None":
        try:
            return self._prepare(df, params)
        except (KeyError, ValueError, TypeError):
            # 入力不正は従来経路（参照実装）にエラー翻訳を委ねる。増分器の不具合は握り潰さない。
            return None

    def _prepare(self, df: Any, params: dict[str, Any]) -> "_Request | None":
        src = self._module()
        if params.get("time_column") is not None:
            return None
        method = str(params.get("band_method", "ols")).lower()
        if method not in ("ols", "empirical"):
            return None
        ql, qh = float(params.get("q_low", 0.05)), float(params.get("q_high", 0.95))
        if not (0.0 < ql < qh < 1.0):
            return None
        maxbars = int(params.get("maxbars", 100))
        if maxbars < 3:
            return None
        # 外れ値分位の有効化条件は build_btlm_trail と同一（黙って無効化＝補助線なし）。
        qo = None
        raw_qo = params.get("q_out")
        try:
            if raw_qo is not None and qh < float(raw_qo) < 1.0:
                qo = float(raw_qo)
        except (TypeError, ValueError):
            qo = None

        prices = src.resolve_source(df, str(params.get("source", "close")))
        n = int(prices.size)
        # 回帰窓が満たない区間（先頭の w<maxbars）は増分の対象にしない。
        if n < maxbars + 2:
            return None

        lower = {str(c).lower(): c for c in df.columns}
        close = (
            df[lower["close"]].to_numpy(dtype=np.float64) if "close" in lower else None
        )
        if method == "empirical" and close is None:
            return None  # 参照実装が ValueError を出す経路へ委ねる。

        resolved = resolve_times(df, None)
        stamps = resolved.to_numpy()
        if not np.issubdtype(stamps.dtype, np.datetime64):
            stamps = pd.to_datetime(resolved).to_numpy()
        times = stamps.astype("datetime64[s]").astype("int64")

        return _Request(
            df=df, source=str(params.get("source", "close")),
            prices=prices, close=close, times=times, n=n, maxbars=maxbars,
            q_low=ql, q_high=qh, q_out=qo, method=method,
            empirical_n=int(params.get("empirical_n", 500)),
            n_cov=int(params.get("n_cov", 250)),
        )

    # ------------------------------------------------------------------ #
    # 1 バーぶんの算出（src の 1 バー入口だけで組む）
    # ------------------------------------------------------------------ #
    def _bar(self, req: "_Request", state: "_State", i: int) -> "_Bar":
        """バー ``i`` の各系列値を、確定済み ``state``（長さ i）から求める。"""
        src = self._module()
        w = min(req.maxbars, i + 1)
        mean, pred_sd, beta, sigma = src.window_end_scalar(req.prices[i - w + 1: i + 1])

        if req.method == "ols":
            low = src.ols_band(mean, pred_sd, req.q_low)
            high = src.ols_band(mean, pred_sd, req.q_high)
            off_hi = src.ols_band(mean, pred_sd, req.q_out) if req.q_out else np.nan
            off_lo = src.ols_band(mean, pred_sd, 1.0 - req.q_out) if req.q_out else np.nan
        else:
            # 経験分位は当該バーを除く直近 emp_n 本（＝確定済みの乖離率）だけを使う（因果）。
            prior = state.deviations[:i]
            emp = lambda q: src.empirical_quantile_latest(prior, req.empirical_n, q)  # noqa: E731
            low = src.empirical_band(mean, emp(req.q_low))
            high = src.empirical_band(mean, emp(req.q_high))
            off_hi = src.empirical_band(mean, emp(req.q_out)) if req.q_out else np.nan
            off_lo = src.empirical_band(mean, emp(1.0 - req.q_out)) if req.q_out else np.nan

        cov = np.nan
        if req.close is not None:
            start = max(0, i + 1 - req.n_cov)
            cov = src.coverage_latest(
                req.close[start: i + 1],
                np.append(state.band_low[start:i], low),
                np.append(state.band_high[start:i], high),
                req.n_cov,
            )
        return _Bar(
            mean=mean, beta=beta, sigma=sigma, band_low=low, band_high=high,
            off_low=off_lo, off_high=off_hi, cov=cov,
        )

    # ------------------------------------------------------------------ #
    # build / adapt
    # ------------------------------------------------------------------ #
    def build(self, req: "_Request") -> "_State":
        """確定プレフィクス（末尾 1 本を除く全件）を参照実装で 1 回だけ組む。"""
        src = self._module()
        m = req.n - 1
        # 確定プレフィクスは参照実装へ **入力 df をそのまま** 渡して組む（合成しない）。
        res = src.build_btlm_trail(
            req.df.iloc[:m], source=req.source, maxbars=req.maxbars,
            q_low=req.q_low, q_high=req.q_high, band_method=req.method,
            empirical_n=req.empirical_n, q_out=req.q_out,
        )
        cov = None
        if req.close is not None:
            cov = src.rolling_coverage(
                req.close[:m], res.band_low, res.band_high, req.n_cov
            )
        return _State(
            prices=req.prices[:m], mean=res.mean, beta=res.beta, sigma=res.sigma,
            band_low=res.band_low, band_high=res.band_high,
            off_low=res.off_low, off_high=res.off_high, cov=cov,
            deviations=res.deviations, close=req.close[:m] if req.close is not None else None,
            m=m,
        )

    def _truncate(self, state: "_State", m: int) -> "_State":
        """確定プレフィクスを m 本へ切り詰める（全系列が因果＝過去のみ依存のため妥当）。"""
        cut = lambda a: None if a is None else a[:m]  # noqa: E731
        return _State(
            prices=state.prices[:m], mean=state.mean[:m], beta=state.beta[:m],
            sigma=state.sigma[:m], band_low=state.band_low[:m], band_high=state.band_high[:m],
            off_low=cut(state.off_low), off_high=cut(state.off_high), cov=cut(state.cov),
            deviations=cut(state.deviations), close=cut(state.close), m=m,
        )

    def _extend(self, state: "_State", req: "_Request", target: int) -> "_State":
        """確定した分（``state.m`` → ``target``）だけ前進した新しい状態を返す。"""
        src = self._module()
        cur = state
        for i in range(state.m, target):
            bar = self._bar(req, cur, i)
            put = lambda a, v: None if a is None else np.append(a, v)  # noqa: E731
            dev = None
            if cur.deviations is not None:
                dev = np.append(
                    cur.deviations, src.deviation_ratio(req.close[i], bar.mean)
                )
            cur = _State(
                prices=req.prices[:i + 1],
                mean=np.append(cur.mean, bar.mean),
                beta=np.append(cur.beta, bar.beta),
                sigma=np.append(cur.sigma, bar.sigma),
                band_low=np.append(cur.band_low, bar.band_low),
                band_high=np.append(cur.band_high, bar.band_high),
                off_low=put(cur.off_low, bar.off_low),
                off_high=put(cur.off_high, bar.off_high),
                cov=put(cur.cov, bar.cov),
                deviations=dev,
                close=None if cur.close is None else req.close[:i + 1],
                m=i + 1,
            )
        return cur

    def adapt(self, state: "_State", req: "_Request") -> "_State | None":
        m_conf = req.n - 1
        if state.m == m_conf:
            return state if np.array_equal(state.prices, req.prices[:m_conf]) else None
        if state.m > m_conf:
            if m_conf < req.maxbars + 1:
                return None
            if not np.array_equal(state.prices[:m_conf], req.prices[:m_conf]):
                return None
            return self._truncate(state, m_conf)
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

        # 骨格の系列名 → (確定配列, 形成中バーの値)。分位バンド 2 本は q_low/q_high 依存名の
        # ため、固定名以外の 2 件を出現順（lwc_chart の emit 順＝low, high）で割り当てる。
        table: dict[str, tuple[Any, float]] = {
            _NAME_MEAN: (state.mean, bar.mean),
            _NAME_OFF_HI: (state.off_high, bar.off_high),
            _NAME_OFF_LO: (state.off_low, bar.off_low),
            _NAME_BETA: (state.beta, bar.beta),
            _NAME_SIGMA: (state.sigma, bar.sigma),
            _NAME_COV: (state.cov, bar.cov),
        }
        quantile_names = [s.get("name") for s in skeleton if s.get("name") not in _FIXED_NAMES]
        if len(quantile_names) != 2 or len(set(quantile_names)) != 2:
            return None
        table[quantile_names[0]] = (state.band_low, bar.band_low)
        table[quantile_names[1]] = (state.band_high, bar.band_high)

        out: list[dict[str, Any]] = []
        for entry in skeleton:
            found = table.get(entry.get("name"))
            if found is None or found[0] is None:
                return None  # 骨格と状態の形が食い違う＝従来経路へ委ねる。
            confirmed, last = found
            out.append({**entry, "data": tail_points(confirmed, last, req.times, req.n, k)})
        return out


