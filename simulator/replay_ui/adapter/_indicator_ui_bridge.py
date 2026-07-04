"""indicator_ui / marketdata の read-only import 境界（proto_server:29-37 と同一方式）。

CLEAN_ARCH §6: 偶有的技術（indicator_ui の実アダプタ・resample 規則）は adapter 層に隔離する。
本モジュールは ``indicator_ui/api`` と repo 根（``marketdata`` パッケージ用）を ``sys.path`` へ
挿入し、``full_compute`` / ``latest_compute`` / ``dataset`` / ``resample_ohlc`` 等を **読むだけ**で
再利用する。cwd 非依存（絶対パス insert）にして、bash 呼出間の cwd リセットに影響されない。

既存 indicator_ui コードは無改変（import して呼ぶのみ）。
"""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

# repo 根 = simulator/replay_ui/adapter/_indicator_ui_bridge.py の parents[3]。
_DEFAULT_REPO_ROOT = Path(__file__).resolve().parents[3]

_CACHE: "dict[tuple[str, str], SimpleNamespace]" = {}


def load(api_path: Any = None, repo_root: Any = None) -> SimpleNamespace:
    """indicator_ui / marketdata の公開シンボルを束ねた namespace を返す（結果はキャッシュ）。"""
    root = Path(repo_root).resolve() if repo_root is not None else _DEFAULT_REPO_ROOT
    api = (
        Path(api_path).resolve()
        if api_path is not None
        else root / "indigators" / "indicator_ui" / "api"
    )
    key = (str(api), str(root))
    cached = _CACHE.get(key)
    if cached is not None:
        return cached

    import sys

    # marketdata（repo 根）と adapter.compute（api）の双方を解決可能にする。
    for p in (str(root), str(api)):
        if p not in sys.path:
            sys.path.insert(0, p)

    from adapter.compute import dataset, IndicatorComputeAdapter  # noqa: E402
    from adapter.compute.latest_dispatch import full_compute, latest_compute  # noqa: E402
    from marketdata.resample import (  # noqa: E402
        TIMEFRAME_RULES,
        is_known_timeframe,
        resample_ohlc,
    )

    ns = SimpleNamespace(
        dataset=dataset,
        adapter=IndicatorComputeAdapter(),
        full_compute=full_compute,
        latest_compute=latest_compute,
        resample_ohlc=resample_ohlc,
        TIMEFRAME_RULES=TIMEFRAME_RULES,
        is_known_timeframe=is_known_timeframe,
    )
    _CACHE[key] = ns
    return ns
