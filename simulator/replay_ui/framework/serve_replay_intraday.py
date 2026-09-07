"""ReplayIntradayApp — 足内データのルートを担う App（framework 層・ISSUE-479 Wave2 3-4 / S-3）。

serve_replay_candles と同じ様式で ``/intraday`` の 1 ルートだけを持つ。クエリ解釈・
既定値・例外分類・応答の形は分割前の do_GET から逐語で移してあり、応答は 1 バイトも
変わらない（書き出しは `write_replay_json` の単一定義を通る）。

業務の入口（replay backend の App）は ``core`` として明示で受け取り、必要な面は
クラス属性の宣言表で表明する（ISSUE-502 段階 5B: 透過委譲の撤去）。
重い処理のワーカーとロックは ``core`` が 1 つだけ持つ（本 App は自前で作らない）。
"""
from __future__ import annotations

from typing import Any
from urllib.parse import parse_qs, urlparse

from api_shared.http_contract import nested_error
from simulator.replay_ui.framework.serve_replay import (
    _error_response,
    require_core_members,
    write_replay_json,
)
from api_shared.json_get_routes import GetRouteResponder

#: 本 App が持つルート。
INTRADAY_PATH = "/intraday"


class ReplayIntradayApp:
    """足内データのルートを持ち、外れた path を ``fallback`` へ落とす面。

    ``core``: 業務の入口（`ReplayApp`）。``fallback``: 1 つ前の配信面。
    """

    #: 本 App が ``core`` へ要求する面（生成時に不足を検査する＝起動時 fail-stop）。
    REQUIRED_CORE_MEMBERS = ("intraday",)

    def __init__(self, *, core: Any, fallback: Any) -> None:
        require_core_members(core, self.REQUIRED_CORE_MEMBERS, owner=type(self).__name__)
        self._core = core
        self.static_server = GetRouteResponder(
            routes={INTRADAY_PATH: self._intraday},
            fallback=fallback,
            writer=write_replay_json,
        )

    @property
    def core(self) -> Any:
        """業務の入口（結線を複製していないことを確かめる面）。"""
        return self._core

    def _intraday(self, path: str) -> "tuple[int, Any]":
        q = parse_qs(urlparse(path).query)
        ref = (q.get("datasetRef") or ["jp225_m1"])[0]
        try:
            start = int(q["start"][0])
            end = int(q["end"][0])
        except Exception:  # noqa: BLE001
            return nested_error("validation", "start/end required")
        mode = (q.get("mode") or ["real_ticks"])[0]
        want_secs = (q.get("secs") or [None])[0] == "1"  # MP tick-live gate（secs=1 のみ）
        try:
            payload = self._core.intraday(ref, start, end, mode, want_secs=want_secs)
            return (200, payload)
        except Exception as e:  # noqa: BLE001 — 例外分類は _error_response へ集約（ISSUE-097 🟡-4）
            return _error_response(e)
