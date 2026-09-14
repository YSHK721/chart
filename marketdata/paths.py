"""marketdata.paths — 時系列データの単一基点（DATA_DIR）。

Sd（data 分離＋DATA_DIR 単一基点・設計 §10.1 C-1 / §10.2 H-5）。
時系列データ（jp225_m1.csv / jp225_daily.csv / rollups/* / rollup_state.json 等）の
**唯一の物理基点**を 1 定数 :data:`DATA_DIR` に集約する。多基点ハードコード
（``<root>/marketdata/data`` の parents[N] 直書き）はすべて本定数経由へ置換する。

解決規則:
  - ``_REPO_ROOT = Path(__file__).resolve().parents[1]``（marketdata パッケージの直上＝
    唯一の基点）。
  - 既定（``MARKETDATA_DATA_DIR`` 未設定）= リポジトリ直下 ``data/marketdata``（生成物・
    gitignore 対象）。
  - ``MARKETDATA_DATA_DIR`` が設定されている場合、その path を採用する。ただし指す path が
    **存在しない場合は** :class:`FileNotFoundError` で**即時失敗**する（fallback 禁止・
    誤った既定への暗黙退行を防ぐ fail-fast）。
"""

from __future__ import annotations

import os
from pathlib import Path

# 唯一の基点（marketdata パッケージの直上）。多基点 parents[N] を本定数に一本化する。
_REPO_ROOT = Path(__file__).resolve().parents[1]

# 既定の時系列データ基点（リポジトリ直下 data/marketdata・生成物）。
_DEFAULT_DATA_DIR = _REPO_ROOT / "data" / "marketdata"

_env_override = os.environ.get("MARKETDATA_DATA_DIR")
if _env_override is not None:
    DATA_DIR = Path(_env_override)
    if not DATA_DIR.exists():
        raise FileNotFoundError(
            f"MARKETDATA_DATA_DIR={_env_override!r} が指す path が存在しません "
            f"（fail-fast・fallback 禁止）。"
        )
else:
    DATA_DIR = _DEFAULT_DATA_DIR

__all__ = ["DATA_DIR"]
