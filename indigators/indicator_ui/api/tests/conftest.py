"""pytest 共有設定（テスト基盤）。

api/ ディレクトリを import パスへ追加し、各テストの `sys.path.insert` を排除する。
this file: api/tests/conftest.py → parents[1] = api/。
"""

import sys
from pathlib import Path

_API_DIR = str(Path(__file__).resolve().parents[1])
if _API_DIR not in sys.path:
    sys.path.insert(0, _API_DIR)
