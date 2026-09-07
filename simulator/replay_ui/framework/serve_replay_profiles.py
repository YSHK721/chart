"""ReplayProfilesApp — 価格帯プロファイル系のルートを担う App（ISSUE-479 Wave2 3-4 / S-3）。

serve_replay_candles と同じ様式で 3 ルートを持つ:
    /market_profile          足ベース TPO / dwell（as-seen-at-t）
    /market_profile_forming  MP サブバーの tick 逐次成長
    /tickvol_profile         取引密度ハイライトの帯定義

3 つを 1 つの App に束ねるのは、いずれも「価格帯に沿った集計」という同じ関心事で、
変更が同時に来るからである（別々の App にすると 3 箇所を同じ理由で触ることになる）。

業務の入口（replay backend の App）は ``core`` として明示で受け取り、必要な面は
クラス属性の宣言表で表明する（ISSUE-502 段階 5B: 透過委譲の撤去）。3 ルートはいずれも
成否の分類つき結果を受け取り、HTTP ステータスへの写像は ``http_response_for`` の 1 箇所へ
委ねる（本 App は番号を持たない）。

クエリ解釈・既定値・例外分類・応答の形は分割前の do_GET から逐語で移してあり、応答は
1 バイトも変わらない。各ルートは Port が注入されているときだけ持つ（未注入なら ``fallback`` へ
落ちる＝分割前の ``and app.*_enabled`` と同値）。

重い処理のワーカーとロックは ``core`` が 1 つだけ持つ（本 App は自前で作らない）。
"""
from __future__ import annotations

from typing import Any
from urllib.parse import parse_qs, urlparse

from simulator.replay_ui.framework.serve_replay import (
    _error_response,
    http_response_for,
    require_core_members,
    write_replay_json,
)
from api_shared.json_get_routes import GetRouteResponder

#: 本 App が持つルート。
MARKET_PROFILE_PATH = "/market_profile"
MARKET_PROFILE_FORMING_PATH = "/market_profile_forming"
TICKVOL_PROFILE_PATH = "/tickvol_profile"


class ReplayProfilesApp:
    """プロファイル系のルートを持ち、外れた path を ``fallback`` へ落とす面。

    ``core``: 業務の入口（`ReplayApp`）。``fallback``: 1 つ前の配信面。
    """

    #: 本 App が ``core`` へ要求する面（生成時に不足を検査する＝起動時 fail-stop）。
    REQUIRED_CORE_MEMBERS = (
        "tickvol_profile", "tickvol_profile_enabled",
        "market_profile", "market_profile_enabled",
        "market_profile_forming", "forming_enabled",
    )

    def __init__(self, *, core: Any, fallback: Any) -> None:
        require_core_members(core, self.REQUIRED_CORE_MEMBERS, owner=type(self).__name__)
        self._core = core
        routes: "dict[str, Any]" = {}
        if core.tickvol_profile_enabled:
            routes[TICKVOL_PROFILE_PATH] = self._tickvol_profile
        if core.market_profile_enabled:
            routes[MARKET_PROFILE_PATH] = self._market_profile
        if core.forming_enabled:
            routes[MARKET_PROFILE_FORMING_PATH] = self._market_profile_forming
        self.static_server = GetRouteResponder(
            routes=routes, fallback=fallback, writer=write_replay_json
        )

    @property
    def core(self) -> Any:
        """業務の入口（結線を複製していないことを確かめる面）。"""
        return self._core

    def _tickvol_profile(self, path: str) -> "tuple[int, Any]":
        # 取引密度ハイライト（時刻帯の背景色）の帯定義。until はリビール T（単一時計 to）。
        #   until が属するセッション日は集計に含めない（当日非参照＝因果）。
        q = parse_qs(urlparse(path).query)
        ref = (q.get("datasetRef") or [None])[0]
        sessions = (q.get("sessions") or [None])[0]
        pct = (q.get("pct") or [None])[0]
        until = (q.get("until") or [None])[0]
        try:
            return http_response_for(self._core.tickvol_profile(ref, sessions, pct, until))
        except Exception as e:  # noqa: BLE001 — 例外分類は _error_response へ集約（ISSUE-097 🟡-4）
            return _error_response(e)

    def _market_profile(self, path: str) -> "tuple[int, Any]":
        q = parse_qs(urlparse(path).query)
        ref = (q.get("datasetRef") or [None])[0]
        tf = (q.get("timeframe") or [None])[0]
        limit = (q.get("limit") or [None])[0]
        bins = (q.get("bins") or [None])[0]
        va = (q.get("va") or [None])[0]
        src = (q.get("src") or [None])[0]
        barw = (q.get("barw") or [None])[0]
        # to は必ずリビール T（as-seen-at-t）。省略時 None＝全期間（後方互換）。
        to = (q.get("to") or [None])[0]
        # from（ローリング窓下限）／today（スナップショット）／sessions（日別分割）。省略時 None。
        frm = (q.get("from") or [None])[0]
        today = (q.get("today") or [None])[0]
        sessions = (q.get("sessions") or [None])[0]
        try:
            return http_response_for(self._core.market_profile(
                ref, tf, limit, bins, va, src, barw, to,
                frm=frm, today=today, sessions=sessions))
        except Exception as e:  # noqa: BLE001 — ValueError→validation 欠落を是正し中央翻訳へ集約（ISSUE-097 🟡-4）
            return _error_response(e)

    def _market_profile_forming(self, path: str) -> "tuple[int, Any]":
        q = parse_qs(urlparse(path).query)
        ref = (q.get("datasetRef") or [None])[0]
        tf = (q.get("timeframe") or [None])[0]
        since = (q.get("since") or [None])[0]
        base = (q.get("base") or [None])[0]
        now_raw = (q.get("now") or [None])[0]
        # now は必ずリビール T（因果）。数値でなければ None（controller が実時刻へフォールバックするが
        #   フロントは常に T を送るため実運用では常に T が入る）。
        now = int(now_raw) if (now_raw and now_raw.lstrip("-").isdigit()) else None
        bins = (q.get("bins") or [None])[0]
        va = (q.get("va") or [None])[0]
        barw = (q.get("barw") or [None])[0]
        # from（セッション窓 base 下限・当日始まり）。省略時 None＝従来全期間 base（後方互換）。
        frm = (q.get("from") or [None])[0]
        try:
            return http_response_for(self._core.market_profile_forming(
                ref, tf, now, base, since, bins, va, barw, frm))
        except Exception as e:  # noqa: BLE001 — ValueError→validation 欠落を是正し中央翻訳へ集約（ISSUE-097 🟡-4）
            return _error_response(e)
