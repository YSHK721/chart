"""tail_reader — :mod:`marketdata.tail_reader` への後方互換 shim（実体は marketdata に既存）。

ファイル末尾から逆方向シークで最後の n_rows だけ読む ``read_tail`` の実体は最下層共有パッケージ
``marketdata/tail_reader.py`` に既存する（``read_tail(path, n)`` の結果は ``全読み.tail(n)`` と
index/値で一致）。dataset→marketdata 移設に伴い、indicator_ui 側は本 shim で marketdata.tail_reader
本体へ **同一モジュールオブジェクト**として解決させる（monkeypatch 対象・状態を単一真実源に統一）。
marketdata を import するため repo 根を sys.path へ挿入する。
"""

from __future__ import annotations

import sys as _sys
from pathlib import Path as _Path

# marketdata パッケージ解決のため repo 根を sys.path へ（api/adapter/compute/ → parents[5]）。
# ISSUE-087 🟡-3: repo 根/MP api の解決は venv の .pth（tools/install_dev_paths.py）が担う（実行時 sys.path 改変を撤去）。

from marketdata import tail_reader as _real  # noqa: E402

# adapter.compute.tail_reader を marketdata.tail_reader 本体へ同一化する。
_sys.modules[__name__] = _real
