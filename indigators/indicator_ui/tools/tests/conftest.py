"""pytest 共有設定（tools のテスト基盤）。

tools/ ディレクトリを import パスへ追加し、各テストの ``sys.path.insert`` を排除する。
this file: tools/tests/conftest.py → parents[1] = tools/。
"""

import sys
from pathlib import Path

_TOOLS_DIR = str(Path(__file__).resolve().parents[1])
if _TOOLS_DIR not in sys.path:
    sys.path.insert(0, _TOOLS_DIR)
