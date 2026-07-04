"""MarketProfileFormingController — GET /market_profile_forming の純ロジック（HTTP 殻非依存）。

Phase2 設計 mp_ticklive_design.md「新規 backend controller」。MP サブバー tick 逐次成長のために、
クライアント側 DwellAccumulator が初回取得する「base（確定足までの累積・表示 bin 配列）＋ forming 期間の
tick 列 ＋ active table」を束ねて返す（以降クライアントはローカル増分＝per-tick HTTP なし）。

``handle_market_profile_forming(ref, timeframe, since, base, now, bins, va, barw) -> (status, body)``:
  - base=1（既定）: forming_ticks + dwell base（``to = formingStart - 1`` ＝ forming 期間排除・二重計上なし）
      + get_active_table を束ねる。base は忠実 binning（Task A 是正）のため **GRID_W 固定グリッド**
      （``baseFine`` / ``baseKmin``）で返す（表示 bin 直接ではない）。応答 shape
      ``{ok, formingStart, ticks, baseFine, baseKmin, activeTable, priceMin, priceMax, nBins, gridW, now}``。
  - base=0: forming tick 尾部（``since`` フィルタ）＋ formingStart のみ（軽量・クライアント増分の差分取得）。
  - 非 tick ref / 非対応 tf（1W/1M/未知）→ 400 nested error（:func:`market_profile_controller._error_body` 再利用）。

依存方向: framework → 本 controller → adapter.compute（forming_bar / market_profile_forming / market_profile_dwell）
＋ 既存 :mod:`market_profile_controller`（base 経路の dwell 計算・エラー整形を DRY 再利用）。既存関数は非改変。
"""

from __future__ import annotations

from typing import Any

from adapter.compute import forming_bar as _forming_bar
from adapter.compute import market_profile_dwell as _mpd
from adapter.compute import market_profile_forming as _mpf
from adapter.controller.market_profile_controller import (
    _error_body,
    handle_market_profile,
)


def _is_full_base(base: Any) -> bool:
    """base フラグ（None/1/'1'=full・0/'0'=light）を判定する。既定（None）は full（base 同梱）。"""
    if base is None:
        return True
    if isinstance(base, bool):
        return base
    if isinstance(base, int):
        return base != 0
    if isinstance(base, str):
        return base.strip() != "0"
    return True


def _parse_since(since: Any) -> "int | None":
    """since（UNIX 秒・str|int|None）を int へ（不正・None は None＝全 forming）。"""
    if isinstance(since, bool):
        return None
    if isinstance(since, int):
        return since
    if isinstance(since, str) and since.strip().isdigit():
        return int(since.strip())
    return None


def handle_market_profile_forming(
    ref: Any,
    timeframe: Any,
    since: Any = None,
    base: Any = None,
    now: Any = None,
    bins: Any = None,
    va: Any = None,
    barw: Any = None,
) -> tuple[int, dict[str, Any]]:
    """GET /market_profile_forming の純ロジック（(status, body) を返す・HTTP 殻非依存）。"""
    # 検証: forming 対象は tick 由来 ref かつ固定周期 tf（1W/1M/未知は非対応）。
    if not _forming_bar.is_tick_ref(ref):
        return _error_body(
            "validation",
            f"market_profile_forming はティック対応 ref のみ対応です: {ref!r}（例: 'jp225_tick'）",
        )
    if not _forming_bar.is_supported_timeframe(timeframe):
        return _error_body(
            "validation",
            f"market_profile_forming 非対応の timeframe です: {timeframe!r}（1W/1M は非対応）",
        )

    symbol = _mpd.resolve_symbol(ref)
    now_i = _forming_bar.resolve_now_unix(now)
    forming_start = _forming_bar.period_start_unix(now_i, timeframe)
    since_i = _parse_since(since)

    ft = _mpf.forming_ticks(symbol, timeframe, now_i, since=since_i)
    body: dict[str, Any] = {
        "ok": True,
        "formingStart": ft["formingStart"],
        "ticks": ft["ticks"],
        "now": ft["now"],
    }

    if _is_full_base(base):
        # base（確定足までの累積）は既存 dwell 経路を to=formingStart-1 で再利用する。
        #   MP-04 是正: ticklive は dwell（滞在秒 time-at-price）を原子とする機能である。base は src='dwell'
        #   に固定し（下の handle_market_profile 呼び出しで明示強制）、forming tick から DwellAccumulator が
        #   計算する dwell 原子と一致させる。UI で candle/m1 を選択中でも本エンドポイントの base は必ず dwell
        #   ＝ticklive は dwell 限定（参照実装 mp_core._session_dwell の dwell 原子に忠実）。
        #   to=formingStart-1 により forming 期間（time==formingStart）を base から排除＝二重計上なし。
        #   忠実 binning（Task A 是正）: base を **表示 bin** ではなく **GRID_W 固定グリッド**（fine/fine_kmin）で
        #   返す（want_fine=True）。クライアント DwellAccumulator は forming tick を同一 fine grid へ累積し、
        #   combined fine → 表示 bin 再集計して POC/VA を出すため、base と forming の binning が完全一致し
        #   mp_core.compute_profile / compute_dwell_profile（全窓）と厳密一致する。
        _, base_body = handle_market_profile(
            ref, timeframe=timeframe, src="dwell", to=int(forming_start) - 1,
            bins=bins, va=va, barw=barw, want_fine=True,
        )
        profile = base_body.get("profile") or {}
        body["baseFine"] = profile.get("fine", [])
        body["baseKmin"] = profile.get("fine_kmin")
        body["priceMin"] = profile.get("price_min")
        body["priceMax"] = profile.get("price_max")
        body["nBins"] = profile.get("n_bins")
        body["gridW"] = profile.get("grid_w", _mpd.GRID_W)
        body["activeTable"] = _mpf.get_active_table(symbol)

    return 200, body
