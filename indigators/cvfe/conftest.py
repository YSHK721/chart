"""pytest 共有設定（cvfe）。

ワークスペース根を import パスへ追加し、``src`` 層が絶対 import する
``common.normal_dist`` / ``common.stats_boot`` を解決できるようにする。
this file: indigators/cvfe/conftest.py → parents[2] = ワークスペース根。

規約は ``indigators/ma_marod/conftest.py`` に同じ（無改変踏襲）。
"""

import sys
from pathlib import Path

_ROOT = str(Path(__file__).resolve().parents[2])
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)
