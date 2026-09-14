"""§5.3 配色の基準＝因果ローリング分位 `p` の**唯一の定義**。

段の名前（帯内 / 上帯超 / ext 超 …）は廃止した（依頼者裁定 2026-08-29）。セルも第 1 表の
価格セル背景も、連続量 1 つで塗る（1 冊に配色の基準は 1 つ・§5.5.5）。

    窓   = values[max(0, t - window_n) : t]     ← 当該バー除外（common.marod_bands と同一）
    帯内 : p = 窓内で v 未満の割合                                  ∈ [0, q_high]
    帯外 : p = q_high + (1 - q_high) * F_GPD(v - u ; xi, beta)      ∈ (q_high, 1]

帯外を GPD で解像する理由（§5.3.1・実測）: 経験順位だけでは正常帯を超えたバーの 3〜50% が
`p = 1.0` に張り付き、「わずかに超えた」と「極端」が同じ色になる＝**最も見たい所で色が止まる**。
窓 `window_n` を伸ばす案は採らない（水準は非定常で、伸ばすほど当てはまらなくなる）。

当てはめは既存規約どおり**エピソード極値へ畳んだ超過分の直近 k_events 件**へ行い、観測が
:data:`MIN_GPD_EVENTS` 未満なら当てはめない（§5.3.2）。そのセルは `tail_unscaled=True` を
返し、「ここから先は目盛りが無い」ことを呼び出し側へ伝える。**濃淡でごまかさない。**

参照実装: `tools/measure/issue449/probe_tailscale.py`。
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Callable, Sequence

import numpy as np

from common import event_quantiles as _evq
from common import gpd as _gpd
from common import marod_bands as _bands

#: GPD を当てはめる最小観測数（`_gpd.MIN_GPD_EVENTS` をそのまま使う＝第 2 定義を作らない）。
MIN_GPD_EVENTS: int = _gpd.MIN_GPD_EVENTS

#: 経験順位を出せる最小観測数（`_bands.MIN_STAT_OBS` をそのまま使う＝第 2 定義を作らない）。
#: 比較集合の本数の下限は因果窓の規約と同じ量であり、呼び出し側がリテラルで書き直すと、
#: 片方だけを直したときに「窓は足りているのに比較集合だけ足りない」食い違いが無言で生まれる。
MIN_STAT_OBS: int = _bands.MIN_STAT_OBS

#: 超過分の既定の定義。指標ごとの差（RSI の `(v-u)/(100-u)`）は呼び出し側が渡す
#: （指標名での分岐を domain に持ち込まない・§8 OCP）。
ExcessDefinition = Callable[[float, float], float]


def _default_excess(value: float, band_high: float) -> float:
    return value - band_high


@dataclass(frozen=True)
class TailFit:
    """帯外の目盛り（超過分へ当てはめた GPD）。"""

    xi: float
    beta: float
    n_events: int


@dataclass(frozen=True)
class QuantileReading:
    """1 セル分の読み。

    Attributes:
        p: 連続量 `p ∈ [0, 1]`。定義できないときは None（無言で 0.5 や 1.0 を埋めない）。
        tail_unscaled: 帯の外に居るが GPD を当てはめられない（§5.3.2 の単一色セル）。
    """

    p: "float | None"
    tail_unscaled: bool


def empirical_rank(window: np.ndarray, current: float) -> float:
    """窓内で `current` 未満の割合（§5.3 の `p` の**唯一の式**）。

    比較集合が確定足の分布か「同じ経過まで進んだ過去の足」の分布か（§5.3.3）は呼び出し側が
    決める。式そのものはどちらでも同じなので、ここ 1 箇所だけが持つ。
    """
    return float(np.count_nonzero(window < current)) / window.size


def in_band_ranks(values: "np.ndarray | Sequence[float]", window_n: int) -> np.ndarray:
    """帯内 `p` の系列（各バーの経験順位）。

    窓規則は :func:`common.marod_bands.rolling_causal_pointwise` へ委譲する（ISSUE-449 T-3）。
    ここで窓を書き直すと因果窓の第 2 定義が生まれるため、**自分では窓を作らない**。
    """
    return _bands.rolling_causal_pointwise(values, window_n, empirical_rank)


def in_band_rank_latest(
    values: "np.ndarray | Sequence[float]", window_n: int
) -> float:
    """**末尾 1 点**の帯内 `p`（`in_band_ranks(values, window_n)[-1]` と同値）。

    セルの `p` に要るのは当該バー 1 点だけである（§5.3）。系列版を呼ぶと n−1 個の順位を
    作って捨てることになり、ISSUE-450 と同型の「作ってから捨てる」欠陥になる
    （レビュー 🔴-1・実測 1 リクエストあたり約 278ms の破棄）。

    窓規則は :func:`common.marod_bands.causal_pointwise_latest` へ委譲する（系列版と
    **同一の**窓規則の唯一の定義。ここで窓を書き直すと因果窓の第 2 定義が生まれる）。

    Returns:
        当該バーの経験順位。値が無い・窓が足りない・当該値が非有限なら NaN。
    """
    vals = np.asarray(values, dtype=np.float64).ravel()
    if vals.size == 0:
        return float("nan")
    return _bands.causal_pointwise_latest(
        vals[:-1], float(vals[-1]), window_n, empirical_rank
    )


def fit_tail(events: Sequence[float], *, k_events: int) -> "TailFit | None":
    """超過分の観測列（エピソード極値）の直近 `k_events` 件へ GPD を当てはめる。

    観測が :data:`MIN_GPD_EVENTS` 未満、または当てはめが成立しないときは None
    （＝帯外に目盛りが無い）。`MIN_GPD_EVENTS` を下げる案は採らない（30 未満では推定量の
    変動係数が 0.71〜0.95 で、色が意味を持たなくなる・§5.3.2）。
    """
    window = np.asarray(list(events)[-int(k_events):], dtype=np.float64)
    window = window[np.isfinite(window)]
    if window.size < MIN_GPD_EVENTS:
        return None
    fit = _gpd.gpd_fit(window)
    if not (math.isfinite(fit.xi) and math.isfinite(fit.beta) and fit.beta > 0.0):
        return None
    return TailFit(xi=float(fit.xi), beta=float(fit.beta), n_events=int(window.size))


def _tail_cdf(excess: float, tail: TailFit) -> float:
    """`F_GPD(excess; xi, beta)`。有限終端（xi<0）以上は分布関数の定義どおり 1。

    `common.gpd.gpd_cdf` は台の外を NaN で返す（数値計算上の表現）。分布関数としては
    終端以上で 1 であり、ここで NaN を素通しすると「最も極端な観測だけ色が消える」。
    """
    if tail.xi < 0.0:
        endpoint = -tail.beta / tail.xi
        if excess >= endpoint:
            return 1.0
    value = float(np.asarray(_gpd.gpd_cdf(np.asarray([excess], dtype=np.float64),
                                          tail.xi, tail.beta))[0])
    return value


def p_at(
    *,
    value: float,
    band_high: float,
    q_high: float,
    in_band_rank: float,
    tail: "TailFit | None",
    excess: ExcessDefinition = _default_excess,
) -> QuantileReading:
    """1 点の `p` を求める（帯内＝経験順位／帯外＝GPD 接合）。

    Args:
        value: 観測値 `v`。
        band_high: 正常帯上端 `u`（＝POT 閾値）。NaN なら帯外判定ができないため帯内扱い。
        q_high: 正常帯の上側分位（接合点の高さ）。
        in_band_rank: 当該バーの帯内経験順位（:func:`in_band_ranks` の値。NaN 可）。
        tail: 帯外の目盛り。None なら帯外は解像できない。
        excess: 超過分の定義（既定は `v - u`）。

    Raises:
        ValueError: `q_high` が (0, 1) の外のとき。
    """
    if not (0.0 < float(q_high) < 1.0):
        raise ValueError(f"q_high は 0 < q_high < 1 が必要です: {q_high!r}")

    is_outside = (
        math.isfinite(value) and math.isfinite(band_high) and value > band_high
    )
    if not is_outside:
        rank = float(in_band_rank)
        return QuantileReading(
            p=None if not math.isfinite(rank) else rank, tail_unscaled=False
        )
    if tail is None:
        return QuantileReading(p=None, tail_unscaled=True)

    over = float(excess(float(value), float(band_high)))
    if not math.isfinite(over):
        return QuantileReading(p=None, tail_unscaled=True)
    cdf = _tail_cdf(over, tail)
    if not math.isfinite(cdf):
        return QuantileReading(p=None, tail_unscaled=True)
    return QuantileReading(p=float(q_high) + (1.0 - float(q_high)) * cdf,
                           tail_unscaled=False)


@dataclass(frozen=True)
class QuantileScale:
    """1 instance ぶんの `p` の目盛り。**仮定の指標値**に対して同じ目盛りを当てる。

    用途（§5.5）: 読み方は 1 つに固定される — 「**この価格で引けたら、各地平の `p` は
    どこになるか**」。:func:`p_at` が「当該バーの `p`」を答えるのに対し、本クラスは
    `PriceValueMap.value_at(price)` が返す**仮定の値**を同じ目盛りへ載せる。
    式は :func:`empirical_rank` / :func:`p_at` を使い回す（第 2 定義を作らない）。

    Attributes:
        window_values: 当該バーを除く因果窓の値（比較集合）。
        band_high: 正常帯上端 `u`。
        q_high: 接合点の高さ。
        tail: 帯外の目盛り（無ければ帯外は解像できない）。
        excess: 超過分の定義。
    """

    window_values: np.ndarray
    band_high: float
    q_high: float
    tail: "TailFit | None"
    excess: ExcessDefinition = _default_excess

    def p_of(self, value: float) -> QuantileReading:
        """仮定の指標値 `value` に対する `p`。"""
        number = float(value)
        if not math.isfinite(number):
            return QuantileReading(p=None, tail_unscaled=False)
        window = np.asarray(self.window_values, dtype=np.float64).ravel()
        window = window[np.isfinite(window)]
        rank = (
            empirical_rank(window, number)
            if window.size >= MIN_STAT_OBS
            else float("nan")
        )
        return p_at(
            value=number,
            band_high=self.band_high,
            q_high=self.q_high,
            in_band_rank=rank,
            tail=self.tail,
            excess=self.excess,
        )


@dataclass(frozen=True)
class BandObservations:
    """値系列と上帯系列を時刻で突き合わせた観測。**因果境界の唯一の所有者**。

    同じ観測を 2 人が使う（§5.2 / §5.3 の第 2 表のセルと、§5.5.5 の背景色の目盛り）。
    突き合わせと因果境界（当該バーを観測に含めない）を 2 か所へ手書きすると、片方だけを
    直したときにセルの色と背景の色が**別々の窓**で決まる。出力はどちらも「それらしい色」の
    ままなので、状態検証では原理的に落ちない（ISSUE-450 と同型）。

    Attributes:
        times: 値系列の時刻（UNIX 秒・古い順）。
        values: 値。
        bands: 同時刻の上帯（供給が無い時刻は NaN。長さは values と必ず一致する）。
    """

    times: "tuple[int, ...]"
    values: np.ndarray
    bands: np.ndarray

    @classmethod
    def of(
        cls,
        value_points: "Sequence[tuple[int, float]]",
        band_points: "Sequence[tuple[int, float]]",
    ) -> "BandObservations":
        """(時刻, 値) の 2 系列を時刻で突き合わせる（帯は値系列の時刻へ揃える）。"""
        times = tuple(int(time) for time, _value in value_points)
        values = np.asarray([float(value) for _time, value in value_points],
                            dtype=np.float64)
        band_by_time = {int(time): float(value) for time, value in band_points}
        bands = np.asarray([band_by_time.get(time, np.nan) for time in times],
                           dtype=np.float64)
        return cls(times=times, values=values, bands=bands)

    @property
    def history(self) -> "tuple[np.ndarray, np.ndarray]":
        """当該バーを除いた観測（因果境界: 当該バーの水準は当該バーより前だけで決まる）。"""
        return self.values[:-1], self.bands[:-1]


def excess_event_fold(
    values: "np.ndarray | Sequence[float]",
    band_highs: "np.ndarray | Sequence[float]",
    *,
    excess: ExcessDefinition = _default_excess,
) -> "tuple[list[float], tuple[int, ...]]":
    """確定した超過エピソードの極値列と、**各バー直前までに確定した件数**を返す。

    イベント検出・エピソード確定は :func:`common.event_quantiles.step_events`（唯一の定義）へ
    委譲する。参照実装 `probe_tailscale.py:139` と同一の呼び方（帯を「超過分 > 0」へ写して
    上側だけを見る）。**閉じていないエピソードは観測にしない**（＝当てはめを増やさない・§7）。

    第 2 戻り値 `counts` は `counts[i] = バー i を処理する**前**に確定していた件数`。
    畳み込みは前方逐次なので、バー i の因果観測列は `events[:counts[i]]` と一致する
    （参照実装 `probe_tailscale.py:113-121` が帯超バーごとに `up` の当時の長さを読むのと
    同じ量）。直近区間の読み（§5.2 背景ストリップ）が各バーの当てはめ窓をこのプレフィックス
    で引くために使う——**畳み込みは 1 回**で、系列版とプレフィックス版の第 2 定義を作らない。

    Raises:
        ValueError: 2 系列の長さが揃っていないとき。
    """
    v = np.asarray(values, dtype=np.float64).ravel()
    u = np.asarray(band_highs, dtype=np.float64).ravel()
    if v.size != u.size:
        raise ValueError(f"values と band_highs は同一長が必要です: {v.size} / {u.size}")

    up: "list[float]" = []
    run_up: "list[float]" = []
    counts: "list[int]" = []
    for value, band in zip(v, u):
        counts.append(len(up))
        if not (math.isfinite(value) and math.isfinite(band)):
            continue
        _evq.step_events(
            float(excess(float(value), float(band))),
            float("-inf"), 0.0, "episode", up, [], run_up, [],
        )
    return [float(x) for x in up], tuple(counts)


def excess_event_history(
    values: "np.ndarray | Sequence[float]",
    band_highs: "np.ndarray | Sequence[float]",
    *,
    excess: ExcessDefinition = _default_excess,
) -> "list[float]":
    """確定した超過エピソードの極値列を返す（古い順）。実体は :func:`excess_event_fold`。"""
    return excess_event_fold(values, band_highs, excess=excess)[0]


#: 当てはめの注入点。既定は :func:`fit_tail`（呼び出し側が epoch 持ち越しの当てはめ
#: キャッシュを差せるようにするための口。式の唯一源は fit_tail のまま動かない）。
TailFitter = Callable[[Sequence[float]], "TailFit | None"]


def trailing_readings(
    values: "np.ndarray | Sequence[float]",
    bands: "np.ndarray | Sequence[float]",
    *,
    window_n: int,
    q_high: float,
    events: "Sequence[float]",
    event_counts: "Sequence[int]",
    k_events: int,
    n_bars: int,
    excess: ExcessDefinition = _default_excess,
    fit: "TailFitter | None" = None,
) -> "tuple[QuantileReading, ...]":
    """直近 `n_bars` 本（**確定バーのみ**を渡すこと）の `p` の読み（古い順）。

    各バー i の読みは当該バーのセルと**同じ式・同じ因果境界**で決める（第 2 定義を作らない）:
      - 帯内順位: :func:`common.marod_bands.causal_pointwise_latest`（窓 = 当該バー除外）。
      - 帯外の目盛り: バー i より**前**に確定したイベント（`events[:event_counts[i]]`）の
        直近 `k_events` 件へ当てはめた GPD。
    当てはめは**帯外のバーだけ**発行する（帯内のバーへ当てはめても :func:`p_at` は使わない＝
    「作ってから捨てる」を作らない・CLAUDE.md 絶対命令 §4.1）。

    Raises:
        ValueError: 系列とプレフィックス列の長さが揃っていないとき。
    """
    v = np.asarray(values, dtype=np.float64).ravel()
    u = np.asarray(bands, dtype=np.float64).ravel()
    if v.size != u.size or v.size != len(event_counts):
        raise ValueError(
            f"values / bands / event_counts は同一長が必要です: "
            f"{v.size} / {u.size} / {len(event_counts)}"
        )
    fitter: TailFitter = (
        fit if fit is not None
        else (lambda observed: fit_tail(observed, k_events=k_events))
    )
    all_events = list(events)
    readings: "list[QuantileReading]" = []
    for index in range(max(0, v.size - int(n_bars)), v.size):
        rank = _bands.causal_pointwise_latest(
            v[:index], float(v[index]), window_n, empirical_rank
        )
        outside = (
            math.isfinite(float(v[index])) and math.isfinite(float(u[index]))
            and float(v[index]) > float(u[index])
        )
        tail = fitter(all_events[: int(event_counts[index])]) if outside else None
        readings.append(p_at(
            value=float(v[index]),
            band_high=float(u[index]),
            q_high=q_high,
            in_band_rank=rank,
            tail=tail,
            excess=excess,
        ))
    return tuple(readings)


def trailing_ranks(
    values: "np.ndarray | Sequence[float]",
    *,
    window_n: int,
    n_bars: int,
) -> "tuple[QuantileReading, ...]":
    """積み上がる量（§5.3.3）の直近 `n_bars` 本（**確定バーのみ**）の読み（古い順）。

    確定バーの全量は「経過 100% の部分和」なので、比較集合は**先行する確定バーの全量**で
    よい（§5.3.3 のバイアスは形成中バーだけに生じる）。窓規則は
    :func:`common.marod_bands.causal_pointwise_latest` へ委譲する（因果窓の唯一の定義。
    有限観測が :data:`MIN_STAT_OBS` 未満のバーは NaN → p None＝無言で 0.5 を埋めない）。
    """
    v = np.asarray(values, dtype=np.float64).ravel()
    readings: "list[QuantileReading]" = []
    for index in range(max(0, v.size - int(n_bars)), v.size):
        rank = _bands.causal_pointwise_latest(
            v[:index], float(v[index]), window_n, empirical_rank
        )
        readings.append(QuantileReading(
            p=None if not math.isfinite(rank) else float(rank),
            tail_unscaled=False,
        ))
    return tuple(readings)
