"""report — 測定表の整形出力（値を作らない・ISSUE-479 Wave2 M-4）。"""
from __future__ import annotations

import math
from typing import Any


def _fmt(v: Any) -> str:
    if isinstance(v, float):
        if not math.isfinite(v):
            return "nan"
        return f"{v:.6g}"
    return str(v)


def print_table(rows: "list[dict[str, Any]]", cols: "list[str]") -> None:
    if not rows:
        print("（該当なし）")
        return
    widths = [max(len(c), *(len(_fmt(r.get(c, ""))) for r in rows)) for c in cols]
    print("  ".join(c.ljust(w) for c, w in zip(cols, widths)))
    print("  ".join("-" * w for w in widths))
    for r in rows:
        print("  ".join(_fmt(r.get(c, "")).ljust(w) for c, w in zip(cols, widths)))
