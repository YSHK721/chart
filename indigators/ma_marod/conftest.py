"""pytest 共有設定（ma_marod）。

ワークスペース根を import パスへ追加し、core が絶対 import する ``common.applied_price``
を解決できるようにする（本番の解決境界は call_binding／venv .pth が担う。テストは本
conftest で代替）。this file: indigators/ma_marod/conftest.py → parents[2] = ワークスペース根。
"""

import sys
from pathlib import Path

_ROOT = str(Path(__file__).resolve().parents[2])
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)
