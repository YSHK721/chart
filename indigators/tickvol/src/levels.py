"""tickvol levels — ティックボリュームの外れ値水準（経験的分位 / GPD-POT・純関数）。

①層名/責務:
    core（純粋計算層・numpy のみ）。ティックボリュームに対し「この足の tick 数はどれほど
    異常か」を測る水準を、**同一の観測集合**から 2 通りの推定量で出す。

②構造（既存の参照実装を無改変で組み合わせる。計算式を写さない）:
    1. 正常帯上端（＝POT の閾値 u_t）
       当該バー除外の因果ローリング分位 :func:`common.marod_bands.rolling_causal_fast`。
       下側 q_low の帯も同時に返す（:func:`common.marod_bands.quantile_bands`）。こちらは
       「普段より極端に静かな足」を示す**表示専用**で、POT/GPD には使わない（③の最終項）。
    2. POT（peaks over threshold）＋宣言クラスタリング
       ``tickvol_t − u_t > 0`` の連続超過を 1 エピソードへ畳み、その極値を 1 観測とする
       :func:`common.event_quantiles.step_events`（``event_agg="episode"``）。
    3. 経験的分位水準  :func:`common.event_quantiles.levels_at`（中央値・q_out 分位）
    4. GPD 水準        :func:`common.gpd.gpd_fit` による超過分への当てはめ → 同じ q_out 分位

    3 と 4 は **同じ超過分の集合の同じ分位**を、経験的／GPD の 2 通りで推定したものである。
    したがって 2 本の差は「標本内で数えた値」と「裾の分布形から外挿した値」の差そのものになる。

③なぜこの構造でなければならないか（実測 2026-08-01・jp225_tick・50,000 本）:
    - **宣言クラスタリングが必須**: 生の閾値超過は極端にクラスタ化し θ = 0.16〜0.27 しかない
      （5m/15m/1h・q=0.90）。:mod:`common.gpd` は超過の独立を前提にするため、生の超過を渡すと
      有効標本を数倍に過大評価する。エピソード極値に畳むと θ = 0.49〜0.89 へ改善し、
      リポジトリのゲート（θ >= 0.2）を満たす。
    - **ローリング窓が必須**: 水準は非定常で、履歴 4 分割の中央値が 5m で 170→489、
      1h で 666→2049 と 3 倍動く。全履歴の当てはめは AD 適合度検定で棄却される
      （p = 0.005〜0.255）が、直近 :data:`common.event_quantiles.DEFAULT_K_EVENTS` 件では
      棄却されない（p = 0.455〜0.720）＝ローリングでこそ GPD 近似が成立する。
    - **GPD の最小観測数は 30**: 窓をずらした 10 標本で GPD 水準の変動係数を測ると
      m=5 で 0.95、m=10〜20 で 0.71〜0.73、m=30 で 0.245、m>=50 で 0.14〜0.21。
      30 未満は推定量が自身の値と同じ大きさで揺れるため水準を出さない（NaN＝描画しない）。
    - **上側のみ**: tickvol は 1 tick 以上でしか足が立たない計数量で、下側は 0 で有界
      （実測 min=1・0 の足は 0 本）。下側は裾ではないため POT/GPD の対象にしない。

④依存: 外部 numpy / 共有 :mod:`common.marod_bands`・:mod:`common.event_quantiles`・
        :mod:`common.gpd`（いずれも無改変参照）。描画ライブラリは import しない。
"""

from __future__ import annotations

import numpy as np

from common import event_quantiles as _evq
from common import gpd as _gpd
from common import marod_bands as _bands

#: 正常帯（＝POT 閾値）の因果窓。MAROD 系の window_n と同 order。
DEFAULT_WINDOW_N: int = 500
#: 正常帯の上側分位＝POT の閾値分位。ForwardStop（common.gpd.select_threshold）の採択は
#: 実測で 5m q=0.95 / 15m q=0.90 / 1h q=0.85 と時間足で動くため、その中央にあたる 0.90 を
#: 既定にする（採択域の内側で、かつ観測件数が最も確保できる点）。
DEFAULT_Q_HIGH: float = 0.90
#: 正常帯の下側分位。上側の既定と対称に取る（0.90 の裏＝0.10）。下側は「普段より極端に静かな足」
#: を示す表示用の分位であり、POT/GPD の対象ではない（tick 数は最小 1 の計数量で下側は裾でない）。
DEFAULT_Q_LOW: float = 0.10
#: GPD を当てはめる最小観測数（③の変動係数の実測に基づく）。未満は NaN。
MIN_GPD_EVENTS: int = 30

#: 水準キー（med=典型深度 / ext=経験的極端分位 / gpd=GPD 外挿）。いずれも「超過分」の水準。
LEVEL_KEYS: tuple[str, ...] = ("med", "ext", "gpd")


def gpd_excess_quantile(excesses, q: "float | None") -> float:
    """超過分へ GPD を当てはめ、その **q 分位**（超過分のスケール）を返す。

    ``level = β/ξ · ((1−q)^(−ξ) − 1)``（ξ→0 は指数分布の極限 ``−β·ln(1−q)``）。
    観測が :data:`MIN_GPD_EVENTS` 未満、q 無効、当てはめ失敗はいずれも NaN。

    当てはめ自体は :func:`common.gpd.gpd_fit`（最尤・scipy 非依存）へ委譲する。
    """
    if q is None:
        return float("nan")
    y = np.asarray(excesses, dtype=np.float64).ravel()
    y = y[np.isfinite(y) & (y > 0.0)]
    if y.size < MIN_GPD_EVENTS:
        return float("nan")
    fit = _gpd.gpd_fit(y)
    if not np.isfinite(fit.xi) or not np.isfinite(fit.beta) or fit.beta <= 0.0:
        return float("nan")
    tail = 1.0 - float(q)
    if tail <= 0.0:
        return float("nan")
    if abs(fit.xi) < 1e-8:
        return float(-fit.beta * np.log(tail))
    return float(fit.beta / fit.xi * (tail ** (-fit.xi) - 1.0))


def step_excess_event(excess_value: float, up: list, run_up: list) -> None:
    """1 バーぶんのイベント確定（上側のみ・**唯一の定義**）。

    超過分系列（``tickvol − u``）に対し「0 を超えたか」でエピソードを判定する。判定・
    エピソード確定は :func:`common.event_quantiles.step_events` へそのまま委譲する
    （``band_lo`` を非有限にすると下側判定は常に偽になる＝上側のみの走査になる）。
    """
    _evq.step_events(excess_value, float("-inf"), 0.0, "episode", up, [], run_up, [])


def levels_at(excesses, m: int, k_events: int, q_out: "float | None") -> "dict[str, float]":
    """確定観測が m 件ある時点の水準（超過分スケール）を返す（**唯一の定義**）。

    経験的側（med / ext）は :func:`common.event_quantiles.levels_at` へ委譲し、GPD 側だけを
    本モジュールが足す。集計範囲は両者とも直近 ``k_events`` 件で揃える（同じ観測集合の
    同じ分位を 2 通りで推定する、という本モジュールの前提を保つため）。
    """
    med, ext = _evq.levels_at(excesses, m, k_events, q_out)
    arr = np.asarray(excesses, dtype=np.float64)[:m]
    window = arr[max(0, m - int(k_events)):]
    return {"med": med, "ext": ext, "gpd": gpd_excess_quantile(window, q_out)}


def levels_latest(up: list, *, q_out: "float | None", k_events: int) -> "dict[str, float]":
    """確定観測列の **次のバー** に適用する水準（超過分スケール）。

    :func:`tickvol_levels` がバー t に与える値と同値（バー t の水準は t より前に確定した
    観測のみから決まるため）。増分計算が末尾 1 点だけを求めるための公開入口。
    """
    return levels_at(up, len(up), k_events, q_out)


def causal_threshold(values, window_n: int, q_high: float) -> np.ndarray:
    """正常帯上端（＝POT 閾値）u_t を返す。当該バー除外の因果ローリング分位。"""
    return _bands.rolling_causal_fast(
        np.asarray(values, dtype=np.float64).ravel(), int(window_n), "quantile", float(q_high)
    )


def causal_bands(values, *, window_n: int, q_low: float, q_high: float):
    """正常帯（下側 q_low・上側 q_high）を返す。上側は POT の閾値そのもの。

    算出は :func:`common.marod_bands.quantile_bands` へ委譲する（分位ペアの検証込み・
    MAROD 系と同一実装）。下側は表示専用で、POT/GPD には使わない。
    """
    return _bands.quantile_bands(
        np.asarray(values, dtype=np.float64).ravel(),
        window_n=int(window_n), q_low=float(q_low), q_high=float(q_high),
    )


def tickvol_levels(
    values,
    *,
    window_n: int = DEFAULT_WINDOW_N,
    q_low: float = DEFAULT_Q_LOW,
    q_high: float = DEFAULT_Q_HIGH,
    q_out: "float | None" = _evq.DEFAULT_Q_OUT,
    k_events: int = _evq.DEFAULT_K_EVENTS,
) -> "dict[str, np.ndarray]":
    """ティックボリュームの正常帯と外れ値水準（**価格軸＝tick 数のスケール**）を返す。

    Returns:
        ``{"band_low", "band_high", "med", "ext", "gpd"}``。いずれも長さ n・未定義は NaN。
        ``band_high`` が POT の閾値 u_t で、外れ値水準は ``u_t + 超過分の水準``＝現在の閾値を
        基準に読める形にしてある。``band_low`` は表示専用（POT/GPD には使わない）。

    Raises:
        ValueError: ``k_events < 1`` または ``window_n`` / 分位ペアが不正なとき
            （検証は :func:`common.marod_bands.validate_window_qpair` と同規約）。
    """
    k = int(k_events)
    if k < 1:
        raise ValueError(f"k_events は 1 以上が必要です: k_events={k_events}")
    v = np.asarray(values, dtype=np.float64).ravel()
    n = v.size
    low, thr = causal_bands(v, window_n=window_n, q_low=q_low, q_high=q_high)
    qo = float(q_out) if _evq.q_out_valid(q_out, q_high) else None

    out = {key: np.full(n, np.nan) for key in LEVEL_KEYS}
    out["band_low"] = low
    out["band_high"] = thr
    if n == 0:
        return out

    # 超過分（当該バー除外の因果閾値に対する）。閾値 warm-up 区間は NaN＝イベント判定外。
    excess = v - thr

    # 水準は「その時点で確定済みの観測数 m」だけで決まる（観測列は単調追記）。バー走査で
    #   m を先に確定し、m ごとの水準テーブルを 1 回だけ作ってバーへ写像する
    #   （common.event_quantiles.outlier_event_quantiles と同じ最適化・出力は同値）。
    #   GPD の当てはめ回数もここで観測数ぶん（実測 1500 バー表示で 42〜85 回）に収まる。
    up: list[float] = []
    run_up: list[float] = []
    cnt = np.zeros(n, dtype=np.int64)
    for t in range(n):
        cnt[t] = len(up)
        step_excess_event(excess[t], up, run_up)
    # 末尾で進行中のエピソードは未確定のまま捨てる（非リペイント）。

    m_max = len(up)
    if m_max == 0:
        return out
    table = {key: np.full(m_max + 1, np.nan) for key in LEVEL_KEYS}
    for m in range(1, m_max + 1):
        lv = levels_at(up, m, k, qo)
        for key in LEVEL_KEYS:
            table[key][m] = lv[key]
    for key in LEVEL_KEYS:
        out[key] = thr + table[key][cnt]
    return out
