"""pytest 共有設定（marketdata のテスト基盤）。

リポジトリルート（marketdata の直上）を import パスへ追加し、``import marketdata.paths``
を pytest 起動ディレクトリに依存せず解決する。
this file: marketdata/conftest.py → parents[1] = リポジトリルート。
"""

import sys
from pathlib import Path

_REPO_ROOT = str(Path(__file__).resolve().parents[1])
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)
