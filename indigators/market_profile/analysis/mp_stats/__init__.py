"""mp_stats — Market Profile (TPO/POC) 情報価値検定パイプライン（分析専用・純 numpy）。

本パッケージは既存実装を一切変更しない読み取り専用の分析コードである。
simulator 側の統計部品（定常ブートストラップ・PW ブロック長・norm_cdf）を
再利用するため、import 時に repo root を sys.path へ挿入する
（indigators/profit_band/analysis/demonstrate_stats.py と同じ前例に従う）。
"""

from __future__ import annotations

import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[4]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))
