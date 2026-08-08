"""MarketProfileFormingController — GET /market_profile_forming の純ロジック（HTTP 殻非依存）。

Phase2 設計 mp_ticklive_design.md「新規 backend controller」。MP サブバー tick 逐次成長のために、
クライアント側 DwellAccumulator が初回取得する「base（確定足までの累積・表示 bin 配列）＋ forming 期間の
tick 列 ＋ active table」を束ねて返す（以降クライアントはローカル増分＝per-tick HTTP なし）。

``handle_market_profile_forming(ref, timeframe, since, base, now, bins, va, barw) -> (status, body)``:
  - base=1（既定）: forming_ticks + dwell base（``to = formingStart - 1`` ＝ forming 期間排除・二重計上なし）
      + get_active_table を束ねる。base は忠実 binning（Task A 是正）のため **GRID_W 固定グリッド**
      （``baseFine`` / ``baseKmin``）で返す（表示 bin 直接ではない）。応答 shape
      ``{ok, formingStart, ticks, baseFine, baseKmin, activeTable, priceMin, priceMax, nBins,
      gridW, vaPct, now}``（``vaPct``＝解決済み VA 比率・ISSUE-260）。
  - base=0: forming tick 尾部（``since`` フィルタ）＋ formingStart のみ（軽量・クライアント増分の差分取得）。
  - 非 tick ref / 非対応 tf（1W/1M/未知）→ 400 nested error（:func:`market_profile_controller._error_body` 再利用）。

依存方向: framework → 本 controller → adapter.compute（forming_bar / market_profile_forming / market_profile_dwell）
＋ 既存 :mod:`market_profile_controller`（base 経路の dwell 計算・エラー整形を DRY 再利用）。既存関数は非改変。
"""

from __future__ import annotations

import math
from typing import Any

from marketdata import tf_meta as _forming_bar  # ISSUE-087 🔴-1: 裸 adapter 依存を排し単一情報源を参照
from market_profile_api.compute import market_profile_dwell as _mpd
from market_profile_api.compute import market_profile_forming as _mpf
from market_profile_api.controller import market_profile_controller as _mpc
from market_profile_api.controller.market_profile_controller import (
    _error_body,
    handle_market_profile,
)
# ISSUE-260: VA 比率の解決は単一情報源。base=1 応答へ**解決済み比率**を載せ、クライアント側
#   （DwellAccumulator）が自前の既定を持たずにサーバ決定値へ従えるようにする。
from market_profile_api.compute.market_profile import resolve_va_pct


def augment_forming_payload(
    payload: Any, ref: Any, timeframe: Any, since: Any, *, buffer: Any
) -> None:
    """forming payload の ``ticks`` を in-memory LiveTickBuffer で補完する（秒成長の遅延解消・in-place）。

    ISSUE-094 🟡-8: 旧 indicator_ui/framework/server.py の ``_augment_mp_forming_ticks``（MP forming
    payload への buffer tick 合成＝業務判断）を MP 側 controller へ移設した。殻（server.py）は
    ``buffer`` を引数で渡すだけで、対応 ref/tf 判定・payload 妥当性・buffer 読取窓・合成は本関数が担う。

    当日 parquet フロンティア遅延（~44s）で欠ける「現在分の末尾 tick」を buffer（near-real-time）で
    埋める。buffer 未注入・非 tick ref・非対応 tf・不正 payload なら **無改変**（現行挙動不変）。
    純関数 :func:`market_profile_forming.augment_forming_ticks`（parquet 優先 dedup・since 適用）へ委譲する。
    """
    if (
        buffer is None
        or not _forming_bar.is_tick_ref(ref)
        or not _forming_bar.is_supported_timeframe(timeframe)
    ):
        return
    if not isinstance(payload, dict) or "ticks" not in payload:
        return
    fs = payload.get("formingStart")
    now_unix = payload.get("now")
    if fs is None or now_unix is None:
        return
    since_int = int(since) if (since is not None and str(since).lstrip("-").isdigit()) else None
    buffer_ticks = buffer.ticks_since(int(fs) * 1000 - 1)  # formingStart 以降（境界含む）の (ms, mid)。
    payload["ticks"] = _mpf.augment_forming_ticks(
        payload["ticks"], buffer_ticks, int(fs), int(now_unix), since=since_int
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


def _reconcile_session_range(
    base_fine: list,
    base_kmin: Any,
    price_min: Any,
    price_max: Any,
    n_bins: Any,
    grid_w: Any,
    base_tpo_units: Any,
    ticks: list,
    barw: Any,
) -> tuple[list, Any, Any, Any, Any]:
    """セッション窓（frm!=None）時のみ呼ぶ純関数。base 応答レンジを

        「base の非空レンジ ∪ forming tick(mid) の実測 min/max」

    の和集合から導出し、``(baseFine, baseKmin, priceMin, priceMax, nBins)`` を返す。

    目的（1D 空 base / 1h 日中ブレイクアウトの clip 解消）:
      forming で実際に addTick される tick(mid) を必ず包含する fine grid を返し、クライアント
      DwellAccumulator.addTick の fine grid 範囲外 clip（off<0 or off>=size）を消す。DwellAccumulator は
      一切変更しない（本関数が返す baseKmin/baseFine.length がそのまま init の kw0/size になる）。

    レンジ規約（present-mode ``compute_dwell_profile`` に忠実）:
      priceMin/priceMax は和集合の生 min/max（floor/ceil 整列しない＝present-mode の出力 price_min/price_max
      と同規約）。fine grid は ``kw0=floor(priceMin/GRID_W)``・``size=floor(priceMax/GRID_W)-kw0+1``（同モジュール
      の kw0/size 式と同一）。これにより priceMin<=tick_min・priceMax>=tick_max から
      ``kw0<=floor(tick_min/GRID_W)`` かつ ``kw0+size>floor(tick_max/GRID_W)`` が保証され全 tick を包含する。

    base の温存:
      base が非空（``base_tpo_units>0``）なら base 非空レンジも和集合に含め、baseFine を和集合グリッドへ
      左右 zero-pad で移し替えて既経過 dwell を温存する（二重計上なし・base 温存の両立）。base が空/縮退なら
      forming tick レンジのみで baseFine を zero-padded（全ゼロ）にする。base も forming tick も無ければ
      入力を素通しする（真に空＝縮退のまま）。
    """
    gw = float(grid_w) if grid_w else _mpd.GRID_W
    # forming tick mid の実測 min/max（＝実際に addTick される実データ）。
    tick_lo = tick_hi = None
    if ticks:
        mids = [float(t[1]) for t in ticks]
        tick_lo, tick_hi = min(mids), max(mids)
    # base 非空（実 dwell あり）か。tpo_units は sum(fine) の int 丸め＝非空判定に一致。
    base_has = (
        base_kmin is not None
        and isinstance(base_fine, list)
        and len(base_fine) > 0
        and float(base_tpo_units or 0) > 0.0
    )
    lows: list[float] = []
    highs: list[float] = []
    if base_has:
        lows.append(float(price_min))
        highs.append(float(price_max))
    if tick_lo is not None:
        lows.append(tick_lo)
        highs.append(tick_hi)
    if not lows:  # base も forming tick も無い＝真に空。入力を素通し（縮退のまま）。
        return base_fine, base_kmin, price_min, price_max, n_bins

    union_min = min(lows)
    union_max = max(highs)
    if union_max <= union_min:  # 単一価格の縮退回避（present-mode の +1 と同規約）。
        union_max = union_min + 1.0

    new_kmin = int(math.floor(union_min / gw))
    k_top = int(math.floor(union_max / gw))
    new_size = max(1, k_top - new_kmin + 1)
    new_fine = [0.0] * new_size
    if base_has:
        offset = int(base_kmin) - new_kmin  # 和集合下限は base 下限以下＝offset>=0。
        for i, v in enumerate(base_fine):
            j = offset + i
            if 0 <= j < new_size:
                new_fine[j] = float(v)

    new_price_min = union_min
    new_price_max = union_max
    # nBins は導出レンジ基準で整合。barw>0（range モード）は導出レンジで再算出、なければ base の nBins を維持。
    new_nbins = n_bins
    barw_f = _mpc._parse_float(barw, 0.0)
    if barw_f > 0 and new_price_max > new_price_min:
        new_nbins = _mpc._resolve_n_bins(int(n_bins or 60), barw_f, new_price_min, new_price_max)
    return new_fine, new_kmin, new_price_min, new_price_max, new_nbins


def handle_market_profile_forming(
    ref: Any,
    timeframe: Any,
    since: Any = None,
    base: Any = None,
    now: Any = None,
    bins: Any = None,
    va: Any = None,
    barw: Any = None,
    frm: Any = None,
) -> tuple[int, dict[str, Any]]:
    """GET /market_profile_forming の純ロジック（(status, body) を返す・HTTP 殻非依存）。

    ``frm``（セッション窓 MP・任意・既定 None）: base 累積のローリング窓下限 time（UNIX 秒・含む）。
        フロントは ``from = 当日始まり = floor(now, 86400)`` を渡し、base を [当日始まり, formingStart) の
        当日経過ぶんへ限定する（combined = [当日始まり, now) ＝古典的 Market Profile）。base 経路
        （handle_market_profile src=dwell）は既に ``from`` を受ける（``from<=time`` フィルタ）ため、予約語
        ``from`` を kwargs 経由で透過するのみ（additive）。``frm=None``（省略）は透過せず従来全期間 base＝
        現行挙動不変（present-mode 後方互換）。forming tick 列は ``now`` 由来で ``frm`` の影響を受けない。
    """
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
        #   セッション窓 MP: frm（当日始まり）指定時は base 累積下限を frm へ繰り上げる（[frm, formingStart)）。
        #   予約語 from は kwargs 経由で透過（frm=None＝省略時は付与せず従来全期間＝後方互換）。
        base_kwargs: dict[str, Any] = {}
        if frm is not None:
            base_kwargs["from"] = frm
        # ISSUE-260: 比率は 1 回だけ解決し、base 計算と応答（vaPct）で同一値を用いる。
        #   クライアントは combined（base + forming tick）の VA を自分で算出する必要があるが
        #   （per-tick HTTP を避ける増分成長の設計）、**比率**はサーバの解決値に従う。
        va_pct = resolve_va_pct(va)
        _, base_body = handle_market_profile(
            ref, timeframe=timeframe, src="dwell", to=int(forming_start) - 1,
            bins=bins, va=va_pct, barw=barw, want_fine=True, **base_kwargs,
        )
        profile = base_body.get("profile") or {}
        base_fine = profile.get("fine", [])
        base_kmin = profile.get("fine_kmin")
        price_min = profile.get("price_min")
        price_max = profile.get("price_max")
        n_bins = profile.get("n_bins")
        grid_w = profile.get("grid_w", _mpd.GRID_W)
        if frm is not None:
            # セッション窓（frm!=None）時のみ: base 応答レンジを base 非空レンジ ∪ forming tick(mid) 実測
            #   min/max の和集合から導出し、addTick される全 tick を包含する（1D 空 base / 1h ブレイクアウト
            #   の clip 解消）。frm=None（present-mode）は本分岐を通らず profile を素通し＝byte 同一で不変。
            base_fine, base_kmin, price_min, price_max, n_bins = _reconcile_session_range(
                base_fine, base_kmin, price_min, price_max, n_bins, grid_w,
                profile.get("tpo_units", 0), ft["ticks"], barw,
            )
        body["baseFine"] = base_fine
        body["baseKmin"] = base_kmin
        body["priceMin"] = price_min
        body["priceMax"] = price_max
        body["nBins"] = n_bins
        body["gridW"] = grid_w
        body["vaPct"] = va_pct  # ISSUE-260: 解決済み VA 比率（front の第 2 定義を不要にする）。
        body["activeTable"] = _mpf.get_active_table(symbol)

    return 200, body
