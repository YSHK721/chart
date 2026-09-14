"""tests から mp_stats を import するための path 設定（analysis/ を挿入）。"""

from __future__ import annotations

import sys
from pathlib import Path

_ANALYSIS_DIR = Path(__file__).resolve().parents[1]
if str(_ANALYSIS_DIR) not in sys.path:
    sys.path.insert(0, str(_ANALYSIS_DIR))
