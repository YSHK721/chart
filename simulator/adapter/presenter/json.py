"""JsonPresenter（JsonReportPort 実装）。

BacktestResult → JSON ファイルへ変換する。stats（BacktestStats dataclass）を
dict 化し、トレード件数等とともに永続化する（再 load で値一致）。

adapter 層は usecase + domain + 技術ドライバのみに依存する。
"""
from __future__ import annotations

import json
from dataclasses import asdict
from typing import Any

from simulator.usecase.ports import JsonReportPort


class JsonPresenter(JsonReportPort):
    """BacktestResult を JSON ファイルへ変換する。"""

    def present_json(self, result: Any, path: Any) -> None:
        payload = {
            "stats": asdict(result.stats),
            "trade_count": int(len(result.trades)),
        }
        with open(path, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False)
