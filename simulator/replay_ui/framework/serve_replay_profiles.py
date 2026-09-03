"""ReplayProfilesApp — 価格帯プロファイル系のルートを担う App（ISSUE-479 Wave2 3-4 / S-3）。

serve_replay_candles と同じ様式で 3 ルートを持つ:
    /market_profile          足ベース TPO / dwell（as-seen-at-t）
    /market_profile_forming  MP サブバーの tick 逐次成長
    /tickvol_profile         取引密度ハイライトの帯定義

3 つを 1 つの App に束ねるのは、いずれも「価格帯に沿った集計」という同じ関心事で、
変更が同時に来るからである（別々の App にすると 3 箇所を同じ理由で触ることになる）。

クエリ解釈・既定値・例外分類・応答の形は分割前の do_GET から逐語で移してあり、応答は
1 バイトも変わらない。各ルートは Port が注入されているときだけ持つ（未注入なら静的配信へ
フォールバック＝分割前の ``and app.*_enabled`` と同値）。

重い処理のワーカーとロックは内側の単一インスタンスを共有する（自前で作らない）。
"""
from __future__ import annotations

from typing import Any
from urllib.parse import parse_qs, urlparse

from simulator.replay_ui.framework.serve_replay import (
    _error_response,
    write_replay_json,
)
from simulator.sim_ui.framework.json_get_routes import GetRouteResponder

#: 本 App が持つルート。
MARKET_PROFILE_PATH = "/market_profile"
MARKET_PROFILE_FORMING_PATH = "/market_profile_forming"
TICKVOL_PROFILE_PATH = "/tickvol_profile"


class ReplayProfilesApp:
    """内側 App を包み、プロファイル系のルートを JSON 経路として前置きした面。"""

    def __init__(self, *, inner: Any) -> None:
        self._inner = inner
        routes: "dict[str, Any]" = {}
        if inner.tickvol_profile_enabled:
            routes[TICKVOL_PROFILE_PATH] = self._tickvol_profile
        if inner.market_profile_enabled:
            routes[MARKET_PROFILE_PATH] = self._market_profile
        if inner.forming_enabled:
            routes[MARKET_PROFILE_FORMING_PATH] = self._market_profile_forming
        self.static_server = GetRouteResponder(
            routes=routes, fallback=inner.static_server, writer=write_replay_json
        )

    @property
    def inner(self) -> Any:
        """包んでいる内側 App（結線を複製していないことを確かめる面）。"""
        return self._inner

    def _tickvol_profile(self, path: str) -> "tuple[int, Any]":
        # 取引密度ハイライト（時刻帯の背景色）の帯定義。until はリビール T（単一時計 to）。
        #   until が属するセッション日は集計に含めない（当日非参照＝因果）。
        q = parse_qs(urlparse(path).query)
        ref = (q.get("datasetRef") or [None])[0]
        sessions = (q.get("sessions") or [None])[0]
        pct = (q.get("pct") or [None])[0]
        until = (q.get("until") or [None])[0]
        try:
            return self._inner.tickvol_profile(ref, sessions, pct, until)
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
            status, payload = self._inner.market_profile(
                ref, tf, limit, bins, va, src, barw, to,
                frm=frm, today=today, sessions=sessions)
            return (status, payload)
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
            status, payload = self._inner.market_profile_forming(
                ref, tf, now, base, since, bins, va, barw, frm)
            return (status, payload)
        except Exception as e:  # noqa: BLE001 — ValueError→validation 欠落を是正し中央翻訳へ集約（ISSUE-097 🟡-4）
            return _error_response(e)

    def __getattr__(self, name: str) -> Any:
        """自分が持たない属性は内側 App へ委譲する（結線を殺さない）。"""
        inner = self.__dict__.get("_inner")
        if inner is None:  # __init__ 完了前・複製時の再帰防止
            raise AttributeError(name)
        return getattr(inner, name)
