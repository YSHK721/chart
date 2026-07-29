"""ギャップ成分の推定（仕様 §4.7）。

層名/責務:
    純粋ロジック層。バーが「ギャップ保有バー」かを判定し、ギャップ二乗の EWMA を保持する。

因果性（仕様 §4 柱書）:
    ``σ̂_CO,t = sqrt(v_{t−1})`` である。呼び出し側は **先に** :meth:`GapEwma.current` を
    読み、そのあとで :meth:`GapEwma.update` を呼ぶ。この順序が逆転すると当該バー自身の
    ギャップが自分の予測に混入する（look-ahead）。

依存: 外部 numpy のみ / プロジェクト内なし。
"""

from __future__ import annotations

import numpy as np

#: 仕様 §4.7-1 のギャップ判定係数（1.5 × バー公称長、1.5 × Δ*）。
#: 仕様 §10 TBD-7（適用時間足）の決定はギャップ定義に及ぶが、その影響は本定数に閉じる。
GAP_FACTOR: float = 1.5

#: 仕様 §4.7-3 の EWMA 初期値に用いるギャップ保有バーの本数。
GAP_INIT_BARS: int = 200


def is_gap_bar(edge_t: float, edge_prev: float, bar_interval_sec: float,
               t_first: float, t_last_prev: float, delta_star_sec: float) -> bool:
    """仕様 §4.7-1 のギャップ保有判定。

    ``bar_edges[t] − bar_edges[t−1] > 1.5 × bar_interval_sec``
    または ``(バー t の最初のティック時刻 − バー t−1 の最後のティック時刻) > 1.5 × delta_star_sec``。

    注意（仕様の未定義域）: ``measure_id`` が ``RRANGE`` / ``PARK`` のとき仕様 §3.2 は
    ``delta_star_sec = 0`` と定める。このとき第 2 条件は「ティック間隔 > 0」となり、
    ティック時刻が狭義単調増加である以上つねに成立する（＝全バーがギャップ保有）。
    仕様はこの帰結を明示していない。本実装は仕様の式をそのまま適用する。
    """
    if not np.isfinite(edge_t) or not np.isfinite(edge_prev):
        return False
    if (edge_t - edge_prev) > GAP_FACTOR * bar_interval_sec:
        return True
    if np.isfinite(t_first) and np.isfinite(t_last_prev):
        return bool((t_first - t_last_prev) > GAP_FACTOR * delta_star_sec)
    return False


class GapEwma:
    """仕様 §4.7-3 の EWMA ``v_t = λ v_{t−1} + (1 − λ) g_t²``（ギャップ保有バーのみ更新）。"""

    __slots__ = ("_v", "_lam")

    def __init__(self, v_init: float, lam: float) -> None:
        self._v = float(v_init)
        self._lam = float(lam)

    def current(self) -> float:
        """``v_{t−1}``（当該バーのギャップを取り込む前の値）。"""
        return self._v

    def update(self, g: float) -> None:
        """ギャップ保有バーの ``g_t`` で状態を更新する。"""
        self._v = self._lam * self._v + (1.0 - self._lam) * float(g) * float(g)

    def copy(self) -> "GapEwma":
        return GapEwma(self._v, self._lam)


def initial_gap_variance(gap_squares: np.ndarray, n_init: int = GAP_INIT_BARS) -> float:
    """仕様 §4.7-3：先頭 ``n_init`` 本のギャップ保有バーの ``g²`` の平均。

    ギャップ保有バーが ``n_init`` 本未満の場合は存在する全本数の平均とする。
    1 本も存在しない場合は 0.0 を返す（ギャップ成分が常に 0 になる）。

    非有限な ``g²`` は平均から除外する。仕様 §4.7-2 の ``g_t = p_open,t − p_close,t−1`` は
    直前バーが無効（E06・``p_close = nan``）だと ``nan`` になり、1 本混じるだけで
    EWMA 初期値が恒久的に ``nan`` 化して全ギャップ成分が死ぬ。仕様はこの条件を
    規定していない（ISSUE-207）。
    """
    g2 = np.asarray(gap_squares, dtype=np.float64)
    g2 = g2[np.isfinite(g2)]
    if g2.size == 0:
        return 0.0
    return float(g2[:n_init].mean())
