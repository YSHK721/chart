"""ReplayIntradayApp — 足内データのルートを担う App（framework 層・ISSUE-479 Wave2 3-4 / S-3）。

serve_replay_candles と同じ様式で ``/intraday`` の 1 ルートだけを持つ。クエリ解釈・
既定値・例外分類・応答の形は分割前の do_GET から逐語で移してあり、応答は 1 バイトも
変わらない（書き出しは `write_replay_json` の単一定義を通る）。

重い処理のワーカーとロックは内側の単一インスタンスを共有する（自前で作らない）。
"""
from __future__ import annotations

from typing import Any
from urllib.parse import parse_qs, urlparse

from api_shared.http_contract import nested_error
from simulator.replay_ui.framework.serve_replay import (
    _error_response,
    write_replay_json,
)
from api_shared.json_get_routes import GetRouteResponder

#: 本 App が持つルート。
INTRADAY_PATH = "/intraday"


class ReplayIntradayApp:
    """内側 App を包み、足内データのルートを JSON 経路として前置きした面。"""

    def __init__(self, *, inner: Any) -> None:
        self._inner = inner
        self.static_server = GetRouteResponder(
            routes={INTRADAY_PATH: self._intraday},
            fallback=inner.static_server,
            writer=write_replay_json,
        )

    @property
    def inner(self) -> Any:
        """包んでいる内側 App（結線を複製していないことを確かめる面）。"""
        return self._inner

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
            payload = self._inner.intraday(ref, start, end, mode, want_secs=want_secs)
            return (200, payload)
        except Exception as e:  # noqa: BLE001 — 例外分類は _error_response へ集約（ISSUE-097 🟡-4）
            return _error_response(e)

    def __getattr__(self, name: str) -> Any:
        """自分が持たない属性は内側 App へ委譲する（結線を殺さない）。"""
        inner = self.__dict__.get("_inner")
        if inner is None:  # __init__ 完了前・複製時の再帰防止
            raise AttributeError(name)
        return getattr(inner, name)
