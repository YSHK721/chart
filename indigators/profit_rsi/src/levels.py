"""profit_rsi levels — RSI の正常帯（因果ローリング分位）と外れ値水準（POT / GPD）。

①層名/責務:
    core（純粋計算層・numpy のみ）。RSI に対し「今の過熱はどれほど異常か」を測る水準を、
    **同一の観測集合**から 2 通りの推定量（経験的分位 / GPD 外挿）で出す。上下両側。

②構造（既存の参照実装を無改変で組み合わせる。計算式を写さない）:
    1. 正常帯（＝POT の閾値 u_t）
       当該バー除外の因果ローリング分位 :func:`common.marod_bands.quantile_bands`。
    2. POT（peaks over threshold）＋エピソード畳み込み
       閾値超過が続く区間を 1 エピソードへ畳み、その極値を 1 観測とする
       （:func:`common.event_quantiles.step_events` の ``event_agg="episode"`` と同規約）。
    3. 経験的分位水準 :func:`common.event_quantiles.levels_at`（直近 k_events 件の q_out 分位）
    4. GPD 水準       :func:`common.gpd.gpd_excess_quantile`（同じ観測集合の同じ分位）
    3 と 4 の差は「標本内で数えた値」と「裾の分布形から外挿した値」の差そのものになる。

③**超過は「余地割合」で測る**（RSI 固有・実測 2026-08-02 で確定）:
    RSI は [0,100] の有界量である。tickvol と同じ生スケール（``RSI − u``）で水準を出すと、
    「現在の閾値 ＋ 過去の超過量」が境界を越え、**実測で全バーの 26〜35% が [0,100] の外**へ
    出た（5m〜1D・上下両側・jp225_tick）。そこで超過を

        excess = (RSI − u) / (100 − u)     上側（下側は (u − RSI) / u）

    すなわち「閾値から境界までの残り余地の何割まで到達したか」で測る。値域は (0,1] で、
    水準は ``u + 分位 × (100 − u)`` となり**構成上 [0,100] を出ない**。実測で範囲外は
    0〜0.7% へ縮小し（残りは GPD 外挿が単位終端をわずかに超える分。台の上限 1.0 で抑える）、
    GPD 適合度は全 10 条件で非棄却（AD p = 0.475〜0.960）になった。

④なぜこの構造でなければならないか（実測 2026-08-02・jp225_tick・5m/15m/1h/4h/1D）:
    - **エピソード畳み込みが必須**: 生の閾値超過は θ̂ = 0.206〜0.295 しかない（ISSUE-227 で
      RSI 系列の θ̂ = 0.107〜0.269 を実測済み・Wilder 平滑による強いクラスタ化）。
      :mod:`common.gpd` は超過の独立を前提にする。エピソード極値へ畳むと
      **θ̂ = 0.859〜0.947** へ改善し、リポジトリのゲート（θ >= 0.2）を満たす。
    - **ローリング窓が必須**: 全履歴当てはめは ForwardStop が大半の閾値を棄却する（非定常）。
      運用と同じ直近 k_events 件の当てはめでは棄却率 0〜20%（名目 5%・窓 10 本）で成立する。
    - **ξ̂ は全条件で負**（−0.20〜−1.08）＝有限終端。終端は上側 94.9〜107.0（理論境界 100）・
      下側 −3.2〜+7.4（同 0）で、**境界を与えずに RSI の有界性を復元**している。
    - **上下両側**: RSI は買われ過ぎ・売られ過ぎが対称に意味を持つ（下側が有界でないという
      tickvol の非対称性は無い）。

⑤依存: 外部 numpy / 共有 :mod:`common.marod_bands`・:mod:`common.event_quantiles`・
        :mod:`common.gpd`（いずれも無改変参照）。描画ライブラリは import しない。
"""

from __future__ import annotations

import numpy as np

from common import event_quantiles as _evq
from common import gpd as _gpd
from common import marod_bands as _bands

#: RSI の上限・下限（元 indicator_maximum / indicator_minimum）。余地割合の基準になる。
RSI_MAX: float = 100.0
RSI_MIN: float = 0.0

#: 正常帯（＝POT 閾値）の因果ローリング窓。tickvol・MAROD 系の window_n と同 order。
DEFAULT_WINDOW_N: int = 500
#: 正常帯の上側分位＝上側 POT の閾値分位。ForwardStop（全履歴）の採択は時間足で 0.80〜0.95 に
#: 散り、運用と同じ直近 k 件の当てはめでは 0.80〜0.95 のいずれでも棄却率が名目 5% と整合する
#: （実測 2026-08-02）。全時間足で観測数を確保できる中央値として 0.90 を既定にする（tickvol と同値）。
DEFAULT_Q_HIGH: float = 0.90
#: 正常帯の下側分位。上側と対称（0.90 の裏＝0.10）。RSI は下側も裾なので POT の対象にする。
DEFAULT_Q_LOW: float = 0.10

#: 水準キー。``ext``＝経験的極端分位 / ``gpd``＝GPD 外挿。``_hi``＝上側 / ``_lo``＝下側。
LEVEL_KEYS: tuple[str, ...] = ("ext_hi", "gpd_hi", "ext_lo", "gpd_lo")
#: 正常帯のキー（POT 閾値そのもの）。
BAND_KEYS: tuple[str, ...] = ("band_low", "band_high")


def headroom(threshold: np.ndarray, *, upper: bool) -> np.ndarray:
    """閾値から境界までの余地（上側 ``100 − u`` / 下側 ``u − 0``）。非正・非有限は NaN。"""
    u = np.asarray(threshold, dtype=np.float64)
    head = (RSI_MAX - u) if upper else (u - RSI_MIN)
    return np.where(np.isfinite(head) & (head > 0.0), head, np.nan)


def excess_fraction(values, threshold, *, upper: bool) -> np.ndarray:
    """超過の余地割合（③）。閾値未達・余地なしは NaN（＝イベント判定外）。"""
    v = np.asarray(values, dtype=np.float64).ravel()
    u = np.asarray(threshold, dtype=np.float64).ravel()
    head = headroom(u, upper=upper)
    raw = (v - u) if upper else (u - v)
    with np.errstate(invalid="ignore", divide="ignore"):
        return raw / head


def step_excess_event(excess_value: float, events: list, run: list) -> None:
    """1 バーぶんのイベント確定（**唯一の定義**）。

    超過分（余地割合）に対し「0 を超えたか」でエピソードを判定する。判定・エピソード確定は
    :func:`common.event_quantiles.step_events` へそのまま委譲する（``band_lo`` を非有限に
    すると下側判定は常に偽＝上側だけの走査になる。上下側それぞれの超過分系列に対して呼ぶ）。
    """
    _evq.step_events(excess_value, float("-inf"), 0.0, "episode", events, [], run, [])


def levels_at(events, m: int, k_events: int, q_out: "float | None") -> "dict[str, float]":
    """確定観測が m 件ある時点の水準（**余地割合スケール**）を返す（**唯一の定義**）。

    経験的側（ext）は :func:`common.event_quantiles.levels_at` へ、GPD 側は
    :func:`common.gpd.gpd_excess_quantile` へ委譲する。集計範囲は両者とも直近
    ``k_events`` 件で揃える（同じ観測集合の同じ分位を 2 通りで推定する前提を保つため）。

    ``q_out`` は**上下どちらの側でも同じ値**（既定 0.99）を使う。本モジュールの観測は
    符号付きの RSI 値ではなく**超過の大きさ（余地割合・常に正）**なので、下側でも「深い」
    ＝大きい側の分位だからである（:mod:`common.event_quantiles` の ``ext_lo`` が
    ``1−q_out`` を使うのは、あちらの観測が符号付きの値そのものだからで、規約が異なる）。

    GPD 側は台の上限 1.0 で抑える。余地割合の台は定義から (0,1] であり、当てはめの
    標本変動で外挿が 1 を超えることがあるためである（③の残り 0〜0.7% の由来）。
    """
    _med, ext = _evq.levels_at(events, m, k_events, q_out)
    arr = np.asarray(events, dtype=np.float64)[:m]
    window = arr[max(0, m - int(k_events)):]
    gpd = _gpd.gpd_excess_quantile(window, q_out)
    if np.isfinite(gpd):
        gpd = min(float(gpd), 1.0)          # 余地割合の台（=1.0）を超えない
    return {"ext": float(ext), "gpd": float(gpd)}


def levels_latest(
    events_hi: list, events_lo: list, *, q_out: "float | None", k_events: int
) -> "dict[str, float]":
    """確定観測列の **次のバー** に適用する水準（余地割合スケール）。

    :func:`rsi_levels` がバー t に与える値と同値（バー t の水準は t より前に確定した観測
    のみから決まるため）。増分計算が末尾 1 点だけを求めるための公開入口。
    """
    hi = levels_at(events_hi, len(events_hi), k_events, q_out)
    lo = levels_at(events_lo, len(events_lo), k_events, q_out)   # 大きさスケール＝上下同じ分位
    return {"ext_hi": hi["ext"], "gpd_hi": hi["gpd"],
            "ext_lo": lo["ext"], "gpd_lo": lo["gpd"]}


def causal_bands(values, *, window_n: int, q_low: float, q_high: float):
    """正常帯（下側 q_low・上側 q_high）を返す。両端が POT の閾値そのもの。

    算出は :func:`common.marod_bands.quantile_bands` へ委譲する（分位ペアの検証込み・
    当該バー除外＝非リペイント。MAROD / tickvol と同一実装）。
    """
    return _bands.quantile_bands(
        np.asarray(values, dtype=np.float64).ravel(),
        window_n=int(window_n), q_low=float(q_low), q_high=float(q_high),
    )


def _side_levels(
    values: np.ndarray, threshold: np.ndarray, *, upper: bool,
    q_out: "float | None", k_events: int,
) -> "tuple[np.ndarray, np.ndarray]":
    """片側の (ext, gpd) 水準（**RSI スケール**）を返す。水準の意味は ``u ± 割合 × 余地``。"""
    n = values.size
    ext_line = np.full(n, np.nan)
    gpd_line = np.full(n, np.nan)
    if n == 0:
        return ext_line, gpd_line

    frac = excess_fraction(values, threshold, upper=upper)
    events: list[float] = []
    run: list[float] = []
    cnt = np.zeros(n, dtype=np.int64)
    for t in range(n):
        cnt[t] = len(events)        # バー t の水準は t より前に確定した観測のみで決まる
        step_excess_event(frac[t], events, run)
    # 末尾で進行中のエピソードは未確定のまま捨てる（非リペイント）。

    m_max = len(events)
    if m_max == 0:
        return ext_line, gpd_line
    # 水準は確定観測数 m だけで決まるため、m ごとに 1 回計算してバーへ写像する
    #   （tickvol.levels / common.event_quantiles.outlier_event_quantiles と同じ最適化）。
    table_ext = np.full(m_max + 1, np.nan)
    table_gpd = np.full(m_max + 1, np.nan)
    for m in range(1, m_max + 1):
        lv = levels_at(events, m, k_events, q_out)   # 大きさスケール＝上下とも q_out
        table_ext[m] = lv["ext"]
        table_gpd[m] = lv["gpd"]

    head = headroom(threshold, upper=upper)
    sign = 1.0 if upper else -1.0
    ext_line = threshold + sign * table_ext[cnt] * head
    gpd_line = threshold + sign * table_gpd[cnt] * head
    return ext_line, gpd_line


def rsi_levels(
    values,
    *,
    window_n: int = DEFAULT_WINDOW_N,
    q_low: float = DEFAULT_Q_LOW,
    q_high: float = DEFAULT_Q_HIGH,
    q_out: "float | None" = _evq.DEFAULT_Q_OUT,
    k_events: int = _evq.DEFAULT_K_EVENTS,
) -> "dict[str, np.ndarray]":
    """RSI の正常帯と外れ値水準（**RSI スケール = [0,100]**）を返す。

    Args:
        values: RSI 系列（昇順・warm-up 0 を含んでよい）。
        window_n: 正常帯（POT 閾値）の因果ローリング窓。
        q_low / q_high: 正常帯の下側・上側分位（= 下側・上側 POT の閾値分位）。
        q_out: 超過エピソードの極端分位（上側 q_out・下側 1−q_out）。無効値は水準オフ（NaN）。
        k_events: 水準を出す直近イベント件数。

    Returns:
        ``{"band_low","band_high","ext_hi","gpd_hi","ext_lo","gpd_lo"}``。長さ n・未定義は NaN。
        ``band_high`` / ``band_low`` が POT の閾値 u_t で、外れ値水準は
        ``u_t ± 分位 × 余地`` ＝現在の閾値を基準に読める形にしてある（③より [0,100] の内側）。

    Raises:
        ValueError: ``k_events < 1`` または ``window_n`` / 分位ペアが不正なとき
            （検証は :func:`common.marod_bands.validate_window_qpair` と同規約）。
    """
    k = int(k_events)
    if k < 1:
        raise ValueError(f"k_events は 1 以上が必要です: k_events={k_events}")
    v = np.asarray(values, dtype=np.float64).ravel()
    low, high = causal_bands(v, window_n=window_n, q_low=q_low, q_high=q_high)
    qo = float(q_out) if _evq.q_out_valid(q_out, q_high) else None

    out: dict[str, np.ndarray] = {"band_low": low, "band_high": high}
    ext_hi, gpd_hi = _side_levels(v, high, upper=True, q_out=qo, k_events=k)
    ext_lo, gpd_lo = _side_levels(v, low, upper=False, q_out=qo, k_events=k)
    out["ext_hi"], out["gpd_hi"] = ext_hi, gpd_hi
    out["ext_lo"], out["gpd_lo"] = ext_lo, gpd_lo
    return out
