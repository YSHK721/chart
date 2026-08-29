"""§5.5.3 `PriceValueMap` — 区分メビウス `v = (aC + b) / (C + d)` の**唯一の所有者**。

実測（§5.5.2・`probe_inverse.py`）: 各指標の core を無改変で前進評価すると、現在バーの
終値 C に対する指標値 `v(C)` は**区分メビウス**である（3 点当てはめの残差は最大 3.9e-12・
全区分で単調増加）。区分の境目は適用価格 hlc3 の折れ（走行 H / L）で、指標 profit_rsi には
さらに上下分岐が加わる。**境目の供給は指標側（BreakpointSource）の責務**であり、本モジュールは
指標を知らない（§8 SRP）。

ここから 2 つが確定する。

  - 逆写像は近似ではなく**閉形式で厳密**: `C = (b - v·d) / (v - a)`。
    反復探索・ニュートン法・二分法はいずれも要らない。
  - 全区分で単調増加なので、価格の交差による到達判定が指標値の交差と**同値**になり、
    §6.1 の判定規約をそのまま適用できる（判定を二重に持たない）。

費用（§5.5.4）: 係数決定は 1 区分あたり 3 点（係数が 3 つだから 3 点が最小）。決めた後の
価格評価は**前進評価を一切呼ばない**（ラダー 82 行の評価で発行 0 回）。係数は現在の価格 C に
依存しない（前バーの状態と走行 H / L だけで決まる）ため、再当てはめの契機はバー確定と
走行極値の更新のときだけである。

参照実装: `tools/measure/issue449/probe_heatmap.py:155-176`。
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Callable, Sequence

import numpy as np

#: 3 点を置くための最小の区分幅（参照実装 probe_heatmap.py:193 と同一）。
_MIN_PIECE_WIDTH: float = 1e-6

#: 無限端の区分へ置く有限端までの倍率（同 :188-189）。
_OPEN_EDGE_SPANS: float = 4.0

#: 区分内へ 3 点を置く相対位置（同 :195）。
_PROBE_POSITIONS: "tuple[float, float, float]" = (0.15, 0.5, 0.85)


class MobiusFitError(RuntimeError):
    """区分メビウスの係数が一意に決まらなかった（＝当てはめ不能）。"""


@dataclass(frozen=True)
class MobiusPiece:
    """1 区分 `[lo, hi]` 上の `v = (aC + b) / (C + d)`。端は ±inf を取りうる。

    `probe_lo` / `probe_hi` は**実際に前進評価を置いた有限区間**である。無限端の区分でも
    当てはめを信用してよいのは探針を置いた範囲までであり、逆写像と単調性の判定はこちらを
    使う（当てはめの外挿で「価格が発散する」形＝§10 の RSI 1W の 345,009 を作らない）。
    """

    lo: float
    hi: float
    a: float
    b: float
    d: float
    probe_lo: "float | None" = None
    probe_hi: "float | None" = None

    @property
    def resolved_lo(self) -> float:
        return self.lo if self.probe_lo is None else self.probe_lo

    @property
    def resolved_hi(self) -> float:
        return self.hi if self.probe_hi is None else self.probe_hi

    def contains(self, price: float) -> bool:
        """価格の帰属（**名目区間**。全価格がどれか 1 区分に属するようにする）。"""
        return self.lo <= price <= self.hi

    def resolves(self, price: float) -> bool:
        """当てはめを信用してよい範囲か（探針を置いた区間）。"""
        return self.resolved_lo <= price <= self.resolved_hi

    def value_at(self, price: float) -> float:
        return (self.a * price + self.b) / (price + self.d)

    def price_at(self, value: float) -> "float | None":
        """閉形式の逆写像。極（`v = a`）と解像範囲外は None（無限大を返さない）。"""
        denominator = value - self.a
        if denominator == 0.0:
            return None
        price = (self.b - value * self.d) / denominator
        if not math.isfinite(price) or not self.resolves(price):
            return None
        return float(price)

    def is_increasing(self) -> bool:
        """解像範囲で単調増加か。

        `d/dC (aC+b)/(C+d) = (a·d - b) / (C+d)^2` なので、`a·d - b > 0` かつ極 `C = -d` が
        解像範囲の内側に無いことが条件。
        """
        if self.a * self.d - self.b <= 0.0:
            return False
        return not self.resolves(-self.d)


def _fit_piece(points: "Sequence[tuple[float, float]]") -> "tuple[float, float, float]":
    """3 点 `(C, v)` から `(a, b, d)` を解く（参照実装 probe_heatmap.py:168-171 と同形）。

    `v·(C + d) = a·C + b` ⇔ `a·C + b - v·d = v·C` の連立。
    """
    matrix = np.array([[price, 1.0, -value] for price, value in points], dtype=np.float64)
    rhs = np.array([value * price for price, value in points], dtype=np.float64)
    try:
        solved = np.linalg.solve(matrix, rhs)
    except np.linalg.LinAlgError as error:
        raise MobiusFitError(
            f"区分メビウスの係数が一意に決まりません（探針 {list(points)}）: {error}"
        ) from error
    if not np.all(np.isfinite(solved)):
        raise MobiusFitError(f"区分メビウスの係数が有限になりません（探針 {list(points)}）")
    return float(solved[0]), float(solved[1]), float(solved[2])


@dataclass(frozen=True)
class PriceValueMap:
    """区分ごとの係数の束。指標を知らない（`fit` に渡される `forward` だけを知る）。"""

    pieces: "tuple[MobiusPiece, ...]"

    @classmethod
    def fit(
        cls,
        forward: Callable[[float], float],
        breakpoints: Sequence[float],
        *,
        span: float,
    ) -> "PriceValueMap":
        """区分ごとに 3 点の前進評価で係数を決める。

        Args:
            forward: `forward(C) -> value`。**既存の増分器をそのまま呼ぶ**（指標の core は
                1 行も変えない）。本メソッドの外では 1 度も呼ばない。
            breakpoints: 区分の境目（順不同・重複可）。指標側 `breakpoints()` の戻り値。
            span: 無限端の区分へ有限端を置くための幅（参照実装は `max(H0 - L0, 1.0)`）。

        Raises:
            ValueError: `span` が非正、境目が非有限、または境目が 1 つも無いとき。
            MobiusFitError: 係数が一意に決まらないとき。
        """
        width = float(span)
        if not (math.isfinite(width) and width > 0.0):
            raise ValueError(f"span は正の有限値が必要です: {span!r}")
        for point in breakpoints:
            if not math.isfinite(float(point)):
                raise ValueError(f"区分の境目は有限値が必要です: {point!r}")
        cuts = sorted({round(float(point), 9) for point in breakpoints})
        if not cuts:
            # 境目が無いと両端が ±inf になり、探針を置く位置が決まらない。参照実装の
            # `breakpoints()` は最低でも走行 L / H の 2 点を返す（probe_heatmap.py:156）。
            raise ValueError("区分の境目が 1 つも無いと探針を置く位置が決まりません")

        edges = [-math.inf, *cuts, math.inf]
        pieces: "list[MobiusPiece]" = []
        for lo, hi in zip(edges, edges[1:]):
            left = lo if math.isfinite(lo) else hi - _OPEN_EDGE_SPANS * width
            right = hi if math.isfinite(hi) else lo + _OPEN_EDGE_SPANS * width
            if right - left < _MIN_PIECE_WIDTH:
                continue
            probes = [left + (right - left) * position for position in _PROBE_POSITIONS]
            a, b, d = _fit_piece([(price, float(forward(price))) for price in probes])
            pieces.append(
                MobiusPiece(lo=lo, hi=hi, a=a, b=b, d=d, probe_lo=left, probe_hi=right)
            )
        return cls(pieces=tuple(pieces))

    def piece_at(self, price: float) -> "MobiusPiece | None":
        """その価格を含む区分（境目は下側の区分が持つ・参照実装 probe_heatmap.py:213 と同一）。"""
        for piece in self.pieces:
            if piece.contains(price):
                return piece
        return None

    def value_at(self, price: float) -> float:
        """価格 → 指標値（閉形式のみ。前進評価は発行しない）。"""
        piece = self.piece_at(price)
        if piece is None:
            raise ValueError(f"どの区分にも属さない価格です: {price!r}")
        return piece.value_at(price)

    def price_at(self, value: float) -> "float | None":
        """指標値 → 価格（閉形式の逆写像）。到達しうる区分が無ければ None。"""
        for piece in self.pieces:
            price = piece.price_at(value)
            if price is not None:
                return price
        return None

    def is_monotonic_increasing(self) -> bool:
        """全区分で単調増加か（§5.5.2 の性質。ここが崩れると §6.1 の同値性が失われる）。"""
        return all(piece.is_increasing() for piece in self.pieces)
