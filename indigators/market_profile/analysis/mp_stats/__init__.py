"""mp_stats — Market Profile (TPO/POC) 情報価値検定パイプライン（分析専用・純 numpy）。

本パッケージは既存実装を一切変更しない読み取り専用の分析コードである。
共有統計核（common.stats_boot: 定常ブートストラップ・PW ブロック長・SPA・VaR 検定・norm_cdf）を
再利用するため、import 時に repo root を sys.path へ挿入する（.pth 未登録環境のフォールバック。
ISSUE-091 A1: simulator.adapter への側方依存は common への抽出で解消済み）。
"""

from __future__ import annotations

import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[4]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))
