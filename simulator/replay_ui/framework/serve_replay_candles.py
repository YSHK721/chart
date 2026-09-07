"""ReplayCandlesApp — 足の供給ルートを担う App（framework 層・ISSUE-479 Wave2 3-4 / S-3）。

``/candles`` と ``/available_days`` の 2 ルートだけを持ち、外れた path は 1 つ前の配信面へ
落とす。業務の入口（replay backend の App）は ``core`` として明示で受け取り、必要な面は
クラス属性の宣言表で表明する（ISSUE-502 段階 5B: 透過委譲の撤去）。

なぜ分けるか（S-3）: 分割前の Handler は 7 ルートぶんのクエリ解釈と例外処理を 1 つの
do_GET に持っており、足の供給を触るときにプロファイルや指標カタログの分岐も読む必要が
あった。ルートの追加・変更が届く範囲を、その機能のファイル 1 つに閉じる。

**応答は 1 バイトも変えない**。クエリ解釈・既定値・例外分類・応答の形は分割前の do_GET
から逐語で移しており、書き出しは `write_replay_json` の単一定義を通る。パリティは
`replay_ui/tests/integration/test_replay_route_parity.py` が byte 単位で固定する。

重い処理のワーカーとロックは ``core`` が 1 つだけ持つ（本 App は自前で作らない）。
rpy2/R はスレッド親和で、App ごとにワーカーを持つと同一スレッド実行という前提が壊れる。
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
CANDLES_PATH = "/candles"
AVAILABLE_DAYS_PATH = "/available_days"


class ReplayCandlesApp:
    """足の供給ルートを JSON 経路として持ち、外れた path を ``fallback`` へ落とす面。

    ``core``: 業務の入口（`ReplayApp`）。本 App はここへ直接呼ぶ。
    ``fallback``: 自分のルートから外れた path を渡す先（1 つ前の配信面）。
    """

    #: 本 App が ``core`` へ要求する面（ISSUE-502 段階 5B: 透過委譲の撤去に伴う宣言）。
    #: 生成時に不足を検査するので、結線の欠落は**起動時**に落ちる（リクエスト時ではない）。
    REQUIRED_CORE_MEMBERS = ("candles", "available_days", "available_days_enabled")

    def __init__(self, *, core: Any, fallback: Any) -> None:
        require_core_members(core, self.REQUIRED_CORE_MEMBERS, owner=type(self).__name__)
        self._core = core
        routes: "dict[str, Any]" = {CANDLES_PATH: self._candles}
        # /available_days は Port が注入されているときだけ持つ（未注入なら静的配信へ
        # フォールバックする＝分割前の ``and app.available_days_enabled`` と同値）。
        if core.available_days_enabled:
            routes[AVAILABLE_DAYS_PATH] = self._available_days
        self.static_server = GetRouteResponder(
            routes=routes, fallback=fallback, writer=write_replay_json
        )

    @property
    def core(self) -> Any:
        """業務の入口（結線を複製していないことを確かめる面）。"""
        return self._core

    def _candles(self, path: str) -> "tuple[int, Any]":
        q = parse_qs(urlparse(path).query)
        ref = (q.get("datasetRef") or ["jp225_m1"])[0]
        tf = (q.get("timeframe") or [None])[0]
        lim = int(q["limit"][0]) if "limit" in q else None
        # カレンダー選択（再生開始日）用の窓指定。未指定は従来の tail(limit)＝挙動不変。
        try:
            start = int(q["from"][0]) if "from" in q else None
            pre = int(q["pre"][0]) if "pre" in q else 0
        except Exception:  # noqa: BLE001
            return nested_error("validation", "from/pre must be int")
        try:
            candles = self._core.candles(ref, tf, lim, start=start, pre=pre)
            return (200, {"ok": True, "candles": candles})
        except Exception as e:  # noqa: BLE001 — 例外分類は _error_response へ集約（ISSUE-097 🟡-4）
            return _error_response(e)

    def _available_days(self, path: str) -> "tuple[int, Any]":
        q = parse_qs(urlparse(path).query)
        ref = (q.get("datasetRef") or ["jp225_m1"])[0]
        tf = (q.get("timeframe") or [None])[0]
        try:
            return (200, {"ok": True, "days": self._core.available_days(ref, tf)})
        except Exception as e:  # noqa: BLE001 — 例外分類は _error_response へ集約（ISSUE-097 🟡-4）
            return _error_response(e)
