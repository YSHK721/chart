"""ティック列の検証・バー分割・前値補間サンプリング（仕様 §3.3・§4.1-2・§4 柱書）。

層名/責務:
    純粋ロジック層。ティック列（時刻・対数価格）とバー境界から、
      * 入力検証（E02 / E03 / E04）
      * バー区間の索引化（:func:`split_bars`）
      * カレンダー時間 Δ の前値補間サンプリング（:func:`previous_tick_sample`）
    を提供する。外部 I/O・ログを持たない。

:func:`split_bars` は一括経路と逐次経路が共有する唯一のバー分割手段である。
境界ティックの帰属をここに 1 本化することで、両経路で ``bar_ticks`` が食い違う
余地を構造的に排除する（仕様 §6「Look-ahead 不在」の前提）。

バー区間は半開区間 ``[bar_edges[t], bar_edges[t+1])`` とする（仕様 §4 柱書が
「``bar_edges[t]`` より厳密に前のティックのみ参照可」と定めることと整合する）。

依存: 外部 numpy のみ / プロジェクト内 errors。
"""

from __future__ import annotations

import numpy as np

from .errors import (
    E02_TICKS_NOT_MONOTONIC,
    E03_NONPOSITIVE_PRICE,
    E04_EDGES_NOT_MONOTONIC,
    CvfeError,
)


def validate_ticks(ticks: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """``ticks`` を検証し ``(times, log_price)`` を返す（仕様 §3.1・E02 / E03）。"""
    arr = np.asarray(ticks, dtype=np.float64)
    if arr.ndim != 2 or arr.shape[1] != 2:
        raise CvfeError(E02_TICKS_NOT_MONOTONIC, f"ticks は shape (K,2) が必要: {arr.shape}")
    if arr.shape[0] < 1:
        raise CvfeError(E02_TICKS_NOT_MONOTONIC, "ticks は K >= 1 が必要")

    times = np.ascontiguousarray(arr[:, 0])
    price = np.ascontiguousarray(arr[:, 1])

    if not np.all(np.isfinite(times)) or (times.size > 1 and np.any(np.diff(times) <= 0.0)):
        raise CvfeError(E02_TICKS_NOT_MONOTONIC, "ticks[:,0] は狭義単調増加が必要")
    if not np.all(np.isfinite(price)) or np.any(price <= 0.0):
        raise CvfeError(E03_NONPOSITIVE_PRICE, "ticks[:,1] は全要素 > 0 が必要")

    return times, np.log(price)


def validate_edges(bar_edges: np.ndarray) -> np.ndarray:
    """``bar_edges`` を検証して返す（仕様 §3.1・E04）。"""
    edges = np.ascontiguousarray(np.asarray(bar_edges, dtype=np.float64).ravel())
    if edges.size < 2:
        raise CvfeError(E04_EDGES_NOT_MONOTONIC, "bar_edges は 2 要素以上が必要")
    if not np.all(np.isfinite(edges)) or np.any(np.diff(edges) <= 0.0):
        raise CvfeError(E04_EDGES_NOT_MONOTONIC, "bar_edges は狭義単調増加が必要")
    return edges


def split_bars(times: np.ndarray, bar_edges: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """各バーのティック索引範囲 ``[start, end)`` を返す（半開区間）。

    一括経路・逐次経路の双方がこの関数のみを用いてバーを切り出す。
    """
    starts = np.searchsorted(times, bar_edges[:-1], side="left")
    ends = np.searchsorted(times, bar_edges[1:], side="left")
    return starts, ends


def previous_tick_sample(times: np.ndarray, logp: np.ndarray,
                         t_start: float, t_end: float, delta: float) -> np.ndarray:
    """``[t_start, t_end]`` を間隔 ``delta`` で刻み、各格子点の直前ティック値を返す。

    仕様 §4.1-2 の「前値補間（previous-tick）でカレンダー時間サンプリング」の実体。
    格子は ``t_start`` から ``delta`` 刻みで ``t_end`` を超えない範囲に取る
    （``t_end - t_start`` が ``delta`` の倍数のときは ``t_end`` を含む）。

    格子点が最初のティックより前にある場合は最初のティック値を用いる
    （それ以前の気配が存在しないため。仕様は当該条件を定めていない）。
    """
    if times.size == 0:
        return np.empty(0, dtype=np.float64)
    span = t_end - t_start
    if span < 0.0 or delta <= 0.0:
        return np.empty(0, dtype=np.float64)
    n_grid = int(np.floor(span / delta)) + 1
    grid = t_start + np.arange(n_grid, dtype=np.float64) * delta
    idx = np.searchsorted(times, grid, side="right") - 1
    np.clip(idx, 0, times.size - 1, out=idx)
    return logp[idx]
