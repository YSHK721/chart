"""rollup_store — :mod:`marketdata.rollup_store` への後方互換 shim（実体は marketdata へ移設）。

上位足ロールアップ CSV の解決・末尾読込・mtime キャッシュの実体は最下層共有パッケージ
``marketdata/rollup_store.py`` へ **byte 一致**で移設した（dataset→marketdata 移設に伴う連鎖）。

本 shim は ``sys.modules[__name__]`` を marketdata.rollup_store 本体へ差し替えることで、
``from adapter.compute import rollup_store`` を marketdata.rollup_store と**同一モジュールオブジェクト**
に解決させる（``_ROLLUP_CACHE`` 等の状態・monkeypatch 対象が dataset と単一真実源で一致）。
marketdata を import するため repo 根を sys.path へ挿入する（旧 rollup_store の自己挿入の移管）。
"""

from __future__ import annotations

import sys as _sys
from pathlib import Path as _Path

# marketdata パッケージ解決のため repo 根を sys.path へ（旧 rollup_store 自己挿入の移管・
# api/adapter/compute/ → parents[5] = /workspaces/app）。
_WORKSPACE_ROOT = _Path(__file__).resolve().parents[5]
if str(_WORKSPACE_ROOT) not in _sys.path:
    _sys.path.insert(0, str(_WORKSPACE_ROOT))

from marketdata import rollup_store as _real  # noqa: E402

# adapter.compute.rollup_store を marketdata.rollup_store 本体へ同一化する（後続 import は本体を返す）。
_sys.modules[__name__] = _real
