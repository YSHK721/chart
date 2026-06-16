"""Latest 増分計算ディスパッチ（Stage A 基盤）— full / latest の上位入口。

純粋関数（各指標 core / add_*）は不変。本モジュールは /compute 境界で:
  full_compute   : 既存どおり adapter.compute(...) を全件で呼ぶ（後方互換の基準）。
  latest_compute : meta=latest_meta(...) を解決し、df を min_window で tail、
                   adapter.compute を不変呼び出し、応答 series を末尾 K 点に切る。

full と latest は互いに import しない（_trail のみ latest 側に閉じる）。
"""

from __future__ import annotations

from typing import Any

from adapter.compute.latest_meta import latest_meta

# 末尾K切りの対象 kind（時系列 data を持つ系列）。horizontal_line は data を持たない
#   （価格軸分布）ため対象外＝触らない。
_TRIMMABLE_KINDS = ("line", "histogram")


def full_compute(
    adapter: Any, compute_id: str, variant: str, df: Any, params: dict[str, Any]
) -> list[dict[str, Any]]:
    """既存どおり全件で adapter.compute(...) を呼ぶ（mode 省略=full の既定経路）。"""
    return adapter.compute(compute_id, variant, df, params)


def _trail(series: list[dict[str, Any]], k: int | None) -> list[dict[str, Any]]:
    """各 series の line/histogram data を末尾 K 点に切る（horizontal_line は不変）。

    k is None（axis_distribution）は series をそのまま返す（全件）。
    """
    if k is None:
        return series
    trimmed: list[dict[str, Any]] = []
    for s in series:
        if s.get("kind") in _TRIMMABLE_KINDS and "data" in s:
            trimmed.append({**s, "data": s["data"][-k:]})
        else:
            trimmed.append(s)
    return trimmed


def latest_compute(
    adapter: Any, compute_id: str, variant: str, df: Any, params: dict[str, Any]
) -> list[dict[str, Any]]:
    """Latest（末尾K）計算: df を min_window で tail → 不変計算 → 末尾K切り。"""
    meta = latest_meta(compute_id, variant, params)
    sub = df if meta.min_window is None else df.tail(meta.min_window)
    series = adapter.compute(compute_id, variant, sub, params)
    return _trail(series, meta.trailing_k)
