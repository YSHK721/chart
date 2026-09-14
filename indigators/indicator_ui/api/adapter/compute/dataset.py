"""dataset — :mod:`marketdata.dataset` への後方互換 shim（実体は marketdata へ移設）。

datasetRef ホワイトリスト解決と OHLC/candles 供給の実体は最下層共有パッケージ
``marketdata/dataset.py`` へ **byte 一致**で移設した（3 者 indicator_ui/MP/simulator ＋ prototype が
marketdata を peer 依存する新構造）。

本 shim は ``sys.modules[__name__]`` を marketdata.dataset 本体へ差し替えることで、
``from adapter.compute import dataset`` を marketdata.dataset と**同一モジュールオブジェクト**へ
解決させる（``_BASE_CACHE`` / ``_RESAMPLE_CACHE`` / ``DATASET_WHITELIST`` 等の状態・monkeypatch
対象が単一真実源で一致し、既存テスト・利用側を無改変で維持する）。

★重要（prototype 無改変）: ``prototype_260626-01/proto_server.py`` は ``from adapter.compute import
dataset`` の副作用として **repo 根が sys.path に載る**ことに依存し、その後 ``from marketdata.resample
import ...`` を解決している（proto_server は _API のみ sys.path 挿入）。旧 dataset.py が行っていた
この repo 根挿入を本 shim が **import する側で**引き継ぐ（挿入 → marketdata.dataset 本体を import）。
"""

from __future__ import annotations

import sys as _sys
from pathlib import Path as _Path

# marketdata パッケージ解決＋旧 dataset 自己挿入の移管（api/adapter/compute/ → parents[5] =
# /workspaces/app）。proto_server はこの副作用に依存するため本 shim で必ず先に行う。
# ISSUE-087 🟡-3: repo 根/MP api の解決は venv の .pth（tools/install_dev_paths.py）が担う（実行時 sys.path 改変を撤去）。

from marketdata import dataset as _real  # noqa: E402

# adapter.compute.dataset を marketdata.dataset 本体へ同一化する（後続 import は本体を返す）。
_sys.modules[__name__] = _real
