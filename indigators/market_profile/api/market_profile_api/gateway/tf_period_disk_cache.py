"""tf_period_disk_cache — tf_period 完了日 (unit, columns) の日次ディスク JSON キャッシュ（gateway 層）。

ISSUE-092 ④: :mod:`tf_period_profile_controller` に直書きされていた日次ディスク JSON の read/write
（``_day_disk_path`` 起点の open 読み書き・tmp→os.replace 原子的確定・fail-safe＝ISSUE-091 #6 の
レイヤ責務違反）を本 gateway 層へ抽出した。controller は :func:`load_day_disk` / :func:`save_day_disk`
へ委譲する。

呼出規律（dwell/zp の provider 注入と同一）: キャッシュ根の有効/無効判定と差替（module 変数
``_TFP_CACHE_ROOT`` の monkeypatch）は controller 側に残し、controller が call-time に解決した
``root: Path`` を本関数へ渡す。本モジュールは純 I/O のみを担い、パス構成・保存形式・原子的確定・
fail-safe（例外握り潰し）は抽出前と完全に同一（byte 不変・回帰ゼロ）。
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any


def day_disk_path(root: Path, symbol: Any, tf: Any, day_start: int) -> Path:
    """完了日 JSON の保存パス ``<root>/<symbol>/<tf>/<day_start>.json``。"""
    return root / str(symbol) / str(tf) / f"{int(day_start)}.json"


def load_day_disk(root: Path, symbol: Any, tf: Any, day_start: int) -> "tuple[float, list] | None":
    """完了日の (unit, columns) をディスクから読む。未ヒット/破損は None（＝再計算へ・fail-safe）。"""
    try:
        with open(day_disk_path(root, symbol, tf, day_start)) as f:
            d = json.load(f)
        return float(d["unit"]), d["columns"]
    except Exception:
        return None


def save_day_disk(root: Path, symbol: Any, tf: Any, day_start: int, unit: float, columns: list) -> None:
    """完了日の (unit, columns) を JSON へ原子的に保存する（失敗は握りつぶす＝次回再計算）。"""
    try:
        path = day_disk_path(root, symbol, tf, day_start)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".json.tmp")
        with open(tmp, "w") as f:
            json.dump({"unit": unit, "columns": columns}, f)
        os.replace(tmp, path)
    except Exception:
        pass
