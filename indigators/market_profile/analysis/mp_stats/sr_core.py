"""S/R 反応計測の高速中核 — 参照実装 :mod:`step9_naked_revisit` の定義を保存したまま
（接触判定・跳ね返り判定・行単位）ベクトル化したもの（ISSUE-248）。

参照実装との関係（厳守事項）:
    接触・跳ね返り・行単位の**定義は step9 が唯一の正解**であり、本モジュールは
    同じ判定を O(log M) 検索で与える等価実装にすぎない。等価性は
    :mod:`tests.test_sr_core` の突合テスト（全水準・全日で index 完全一致）で担保する。

等価性の根拠（接触判定）:
    step9 の接触は ``near[i] = |c[i]−lv| <= tol`` **または** ``cross[i] =`` 符号反転。
    経路の出発点 c[0] を基準に場合分けすると初回接触 index は次と一致する:
      - ``|c[0] − lv| <= tol``            → 0
      - ``c[0] < lv − tol``（下から接近） → 最初に ``c[i] >= lv − tol`` となる i
      - ``c[0] > lv + tol``（上から接近） → 最初に ``c[i] <= lv + tol`` となる i
    ``c[i] >= lv − tol`` の初回 index は累積最大 running-max（非減少）への
    searchsorted で、``c[i] <= lv + tol`` は累積最小（非増加）への searchsorted で得る。
    それ以前の分では near も cross も成立し得ない（全分が同符号側にある）ため、
    step9 の ``argmin(near|cross)`` と厳密に同じ index になる。
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

#: 行 → 価格の写像の対数格子（step9 と同一の単一情報源）。
_LOG_UNIT = 1e-4

#: 反応距離の単位となる行数（step9 = Step5/Step6 と同一。日レンジ / 40）。
N_ROWS_DAILY = 40


@dataclass(frozen=True)
class DayPath:
    """1 営業日の分足経路と、接触判定用の前計算配列。"""

    day: int
    closes: "np.ndarray"      # (M,)
    run_max: "np.ndarray"     # (M,) 累積最大（非減少）
    run_min: "np.ndarray"     # (M,) 累積最小（非増加）
    cell_width: float
    row_width: float
    lo: float
    hi: float


def make_path(closes: "np.ndarray", cell_width: float, row_width: float, day: int) -> DayPath:
    c = np.asarray(closes, dtype=np.float64)
    return DayPath(
        day=day,
        closes=c,
        run_max=np.maximum.accumulate(c),
        run_min=np.minimum.accumulate(c),
        cell_width=float(cell_width),
        row_width=float(row_width),
        lo=float(c.min()),
        hi=float(c.max()),
    )


def first_touch_many(path: DayPath, levels: "np.ndarray") -> "np.ndarray":
    """各水準の初回接触分 index（未接触は -1）。step9 ``_first_touch`` と等価。"""
    lv = np.asarray(levels, dtype=np.float64)
    M = path.closes.size
    tol = path.cell_width / 2.0
    c0 = path.closes[0]

    idx = np.full(lv.size, -1, dtype=np.int64)
    # 出発点が既に水準上（tol 以内）
    at_start = np.abs(c0 - lv) <= tol
    idx[at_start] = 0

    # 下から接近: 最初に c[i] >= lv - tol
    below = (~at_start) & (c0 < lv - tol)
    if np.any(below):
        j = np.searchsorted(path.run_max, lv[below] - tol, side="left")
        j = np.where(j < M, j, -1)
        idx[below] = j

    # 上から接近: 最初に c[i] <= lv + tol（非増加列 → 符号反転して searchsorted）
    above = (~at_start) & (c0 > lv + tol)
    if np.any(above):
        j = np.searchsorted(-path.run_min, -(lv[above] + tol), side="left")
        j = np.where(j < M, j, -1)
        idx[above] = j
    return idx


def window_extremes(closes: "np.ndarray", k: int) -> "tuple[np.ndarray, np.ndarray]":
    """``closes[i : i+k+1]`` の (最大, 最小) を全 i について返す（末尾は打ち切り窓）。"""
    c = np.asarray(closes, dtype=np.float64)
    M = c.size
    pad_hi = np.concatenate([c, np.full(k, -np.inf)])
    pad_lo = np.concatenate([c, np.full(k, np.inf)])
    sw = np.lib.stride_tricks.sliding_window_view
    return sw(pad_hi, k + 1)[:M].max(axis=1), sw(pad_lo, k + 1)[:M].min(axis=1)


@dataclass(frozen=True)
class Reaction:
    """接触事件の反応量（すべて水準に対する符号付き・行単位）。"""

    idx: "np.ndarray"          # 接触分 index
    from_above: "np.ndarray"   # True=上から接近（サポート検定）/ False=下から（レジスタンス検定）
    bounce: "np.ndarray"       # 逆方向へ x 行以上（step9 主物差し）
    cont: "np.ndarray"         # 接近方向へ x 行以上（貫通）
    mre: "np.ndarray"          # 最大逆行（跳ね返り側）行数
    mce: "np.ndarray"          # 最大順行（貫通側）行数
    end: "np.ndarray"          # k 分後終値の水準からの符号付き乖離（跳ね返り＝正）行数
    level: "np.ndarray"


def measure(
    path: DayPath,
    levels: "np.ndarray",
    idx: "np.ndarray",
    *,
    k: int,
    x: float,
    win_hi: "np.ndarray",
    win_lo: "np.ndarray",
) -> "Reaction | None":
    """接触事件の反応を測る。判定不能（idx<=0 / 窓長<2 / 接近方向不定）は除外する。

    step9 ``bounced`` と同一規約: 接近方向は接触直前分 ``c[idx−1]`` の水準に対する上下で
    決め、``|c[idx−1] − lv| <= tol`` なら判定不能。跳ね返りは k 分以内に逆方向 x 行以上。
    """
    c = path.closes
    M = c.size
    tol = path.cell_width / 2.0
    ok = (idx > 0) & (idx <= M - 2)
    if not np.any(ok):
        return None
    lv = np.asarray(levels, dtype=np.float64)[ok]
    i = idx[ok]
    prev = c[i - 1]
    decided = np.abs(prev - lv) > tol
    if not np.any(decided):
        return None
    lv, i = lv[decided], i[decided]
    from_above = prev[decided] > lv
    row = path.row_width
    hi = win_hi[i]
    lo = win_lo[i]
    # 上から接近 → 跳ね返り＝上へ（hi 側）/ 貫通＝下へ（lo 側）。下から接近はその逆。
    rev = np.where(from_above, hi - lv, lv - lo) / row
    con = np.where(from_above, lv - lo, hi - lv) / row
    end_i = np.minimum(i + k, M - 1)
    end = np.where(from_above, c[end_i] - lv, lv - c[end_i]) / row
    return Reaction(
        idx=i,
        from_above=from_above,
        bounce=rev >= x,
        cont=con >= x,
        mre=rev,
        mce=con,
        end=end,
        level=lv,
    )
