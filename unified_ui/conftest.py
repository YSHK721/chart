"""pytest 設定（unified_ui 自己完結ハーネス）。

`router.py` を `import router` で解決可能にするため、本ディレクトリを
sys.path 先頭へ追加する。既存プロジェクトの pytest 設定には一切触れない。
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
