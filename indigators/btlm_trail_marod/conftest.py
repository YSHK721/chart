"""pytest 共有設定（btlm_trail_marod）。

ワークスペース根を import パスへ追加し、参照する btlm_trail core（``resolve_source`` /
``rolling_ols_window_end``）が内部で解決する ``common.applied_price`` を絶対 import で
解決できるようにする（本番の解決境界は call_binding／venv .pth が担う。テストは本
conftest で代替）。this file: indigators/btlm_trail_marod/conftest.py → parents[2] = ワークスペース根。
"""

import sys
from pathlib import Path

_ROOT = str(Path(__file__).resolve().parents[2])
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)
