"""ReplayCandlesApp — 足の供給ルートを担う App（framework 層・ISSUE-479 Wave2 3-4 / S-3）。

serve_sim_indicators と同じ様式（内側 App を包む ＋ ``static_server`` 差し替え ＋
属性は内側へ委譲）で、``/candles`` と ``/available_days`` の 2 ルートだけを持つ。

なぜ分けるか（S-3）: 分割前の Handler は 7 ルートぶんのクエリ解釈と例外処理を 1 つの
do_GET に持っており、足の供給を触るときにプロファイルや指標カタログの分岐も読む必要が
あった。ルートの追加・変更が届く範囲を、その機能のファイル 1 つに閉じる。

**応答は 1 バイトも変えない**。クエリ解釈・既定値・例外分類・応答の形は分割前の do_GET
から逐語で移しており、書き出しは `write_replay_json` の単一定義を通る。パリティは
`replay_ui/tests/integration/test_replay_route_parity.py` が byte 単位で固定する。

重い処理のワーカーとロックは**内側の単一インスタンスを共有**する（自前で作らない）。
rpy2/R はスレッド親和で、App ごとにワーカーを持つと同一スレッド実行という前提が壊れる。
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
CANDLES_PATH = "/candles"
AVAILABLE_DAYS_PATH = "/available_days"


class ReplayCandlesApp:
    """内側 App を包み、足の供給ルートを JSON 経路として前置きした面。

    ``inner``: 内側 App（`ReplayApp` または他のルート App）。
    """

    def __init__(self, *, inner: Any) -> None:
        self._inner = inner
        routes: "dict[str, Any]" = {CANDLES_PATH: self._candles}
        # /available_days は Port が注入されているときだけ持つ（未注入なら静的配信へ
        # フォールバックする＝分割前の ``and app.available_days_enabled`` と同値）。
        if inner.available_days_enabled:
            routes[AVAILABLE_DAYS_PATH] = self._available_days
        self.static_server = GetRouteResponder(
            routes=routes, fallback=inner.static_server, writer=write_replay_json
        )

    @property
    def inner(self) -> Any:
        """包んでいる内側 App（結線を複製していないことを確かめる面）。"""
        return self._inner

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
            candles = self._inner.candles(ref, tf, lim, start=start, pre=pre)
            return (200, {"ok": True, "candles": candles})
        except Exception as e:  # noqa: BLE001 — 例外分類は _error_response へ集約（ISSUE-097 🟡-4）
            return _error_response(e)

    def _available_days(self, path: str) -> "tuple[int, Any]":
        q = parse_qs(urlparse(path).query)
        ref = (q.get("datasetRef") or ["jp225_m1"])[0]
        tf = (q.get("timeframe") or [None])[0]
        try:
            return (200, {"ok": True, "days": self._inner.available_days(ref, tf)})
        except Exception as e:  # noqa: BLE001 — 例外分類は _error_response へ集約（ISSUE-097 🟡-4）
            return _error_response(e)

    def __getattr__(self, name: str) -> Any:
        """自分が持たない属性は内側 App へ委譲する。

        Handler と他のルート App は compute / _heavy_worker / 各 ``*_enabled`` を
        属性で引く。ここが解決できないと、受け口はあるのに結線が死ぬ（ISSUE-291 の形）。
        """
        inner = self.__dict__.get("_inner")
        if inner is None:  # __init__ 完了前・複製時の再帰防止
            raise AttributeError(name)
        return getattr(inner, name)
