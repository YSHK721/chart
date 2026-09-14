"""indigators/ 配下テストの依存解決点（ISSUE-174 案 a）。

指標パッケージの src 層は兄弟パッケージ（``moving_averages`` / ``mql_builtins`` /
``profit_system``）を top-level 名で import する。その解決に必要な ``indigators/`` を
ここで 1 回だけ sys.path へ載せ、各 src の ``sys.path.insert`` を不要にする。

repo 根（``common`` / ``common_view`` 等）の解決は repo 根 ``pyproject.toml`` の
``[tool.pytest.ini_options] pythonpath = ["."]`` が担う（本ファイルでは扱わない）。
"""

from __future__ import annotations

import sys
from pathlib import Path

_INDIGATORS = str(Path(__file__).resolve().parent)

if _INDIGATORS not in sys.path:
    sys.path.insert(0, _INDIGATORS)
