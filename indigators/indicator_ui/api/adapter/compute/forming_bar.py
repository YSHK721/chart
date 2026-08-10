"""forming_bar — ライブ足内更新用の「形成中バー」算出（ティック由来・薄い adapter）。

選択中 timeframe の現在期間 ``[floor(now, tf), now)`` の実ティックから形成中（in-progress）バー
（mid OHLCV・1本）を組み立て、フロントのライブ足内更新（最新足のみ ``updateLastCandle``）へ供給する。
集計本体は :func:`marketdata.tick_m1.forming_bar_from_ticks`（純粋集計・規則源）へ委譲し、本モジュールは
「ref→tick ソース解決」と「期間始端の算出」のみ担う。

対応 timeframe: 固定周期（1m/5m/15m/30m/1h/4h/1D）のみ。期間始端＝``floor(now, tf)`` が
``marketdata.resample`` の固定周期ラベル（左端）と一致するため、形成中バーの ``time`` が既存
ロールアップ足の境界と整合する。週(1W)/月(1M)は固定 floor で表せないため**非対応**（``None`` を返す）。

データ保全: 既存 CSV/ロールアップには触れず、ティック parquet（``DATA_DIR/ticks``）を**読むだけ**。
"""

from __future__ import annotations

import logging
import os
import time
from typing import Any, Optional

import pandas as pd

logger = logging.getLogger(__name__)

# ISSUE-087 🔴-1: now 解決は marketdata.tf_meta（単一情報源）へ移設。本名は再エクスポートで維持
#   （既存呼び出し・テストの互換）。デモ時計（FORMING_DEMO_NOW）の挙動も tf_meta 側が同一規約で担う。


# repo 根を sys.path へ（marketdata を import するため・dataset/rollup_store と同じロード境界）。
import sys as _sys
from pathlib import Path as _Path

# ISSUE-087 🟡-3: repo 根/MP api の解決は venv の .pth（tools/install_dev_paths.py）が担う（実行時 sys.path 改変を撤去）。
from marketdata.resample import TIMEFRAME_RULES  # noqa: E402  (規則源・floor freq を導出)
from marketdata.tick_m1 import forming_bar_from_ticks  # noqa: E402
# セッション日境界（ISSUE-078）: 1D の期間始端と 1D バー time 規約（ラベル深夜）の唯一の規則源。
from marketdata.session_day import session_bar_time, session_day_start  # noqa: E402

# ISSUE-087 🔴-1/🔴-2: tick ref・floor 規則・期間始端は marketdata.tf_meta（単一情報源）へ移設。
#   本モジュールは既存名を再エクスポートして互換を維持する（indicator_ui 内の利用箇所は不変）。
#   market_profile_api は adapter.compute を経由せず marketdata.tf_meta を直接参照する（裸依存排除）。
from marketdata.tf_meta import (  # noqa: E402
    NON_FLOORABLE_TF as _NON_FLOORABLE_TF,
    TF_BAR_SEC as _TF_BAR_SEC,
    TICK_REFS,
    floor_freq as _floor_freq,
    is_supported_timeframe,
    is_tick_ref,
    period_start_unix,
    resolve_now_unix,
)

# ロールアップ方式 forming が対応する全 tf（1m＋上位足 5m..1M）。ロールアップの現周期 partial バー
# （base）から周期始端を得るため、1W/1M も floor 不要で対応できる（rollup ラベル＝始端）。
ROLLUP_FORMING_TF = frozenset(TIMEFRAME_RULES)

# ISSUE-162（歯抜けゼロ橋渡し）: 欠落閉周期の tick 合成対象＝固定長 tf の周期秒。
#   1W/1M は周期が可変かつライブ中に閉周期が欠落し得ないため対象外。
# ISSUE-179 項目 5: tf→秒のリテラル二重定義（本表 7 件 ↔ ``marketdata.tf_meta.TF_BAR_SEC``）を
#   解消し、台帳からの **導出** へ置換した。除外規則は同台帳の ``NON_FLOORABLE_TF``
#   （＝``TF_DESCRIPTORS.floorable`` の導出値）を唯一源とする。置換前後で内容・反復順とも
#   完全一致することを実測済み（差分 0）。
_FIXED_TF_SECONDS = {
    tf: sec for tf, sec in _TF_BAR_SEC.items() if tf not in _NON_FLOORABLE_TF
}
# 欠落閉周期の最大合成本数（暴走防御。定常運転の欠落は高々 1〜2 周期）。
_MAX_GAP_FILL_PERIODS = 5


def forming_bar(ref: str, tf: str, now_unix: int) -> Optional[dict]:
    """``ref``/``tf`` の現在形成中バーを返す（対象外 ref/tf・ティック無しは ``None``）。

    ``[floor(now, tf), now)`` の実ティックを :func:`forming_bar_from_ticks` で集計する。
    """
    if not is_tick_ref(ref) or not is_supported_timeframe(tf):
        return None
    start = period_start_unix(now_unix, tf)
    bar = forming_bar_from_ticks(start, int(now_unix))
    # ISSUE-078: 1D の time はセッション日ラベルの UTC 深夜へ再ラベル（rollup 1D バーと同一規約・
    #   チャート日付軸整合）。データ窓（start..now）はセッション始端基準のまま。
    if bar is not None and tf == "1D":
        bar = {**bar, "time": session_bar_time(start)}
    return bar


def merge_forming(base: Optional[dict], tail: Optional[dict]) -> Optional[dict]:
    """確定畳み込み ``base``（rollup 現周期 partial）と未確定テール ``tail`` を形成中バー1本へ合成する。

    ロールアップ方式 forming の中核（純関数・O(1)）。``base`` は現周期の確定サブバー集約
    （rollup の最終 partial バー・``time``=周期始端）、``tail`` は確定末尾以降の未確定増分。

    合成規則: ``time``/``open`` は周期始端側（base 優先・無ければ tail）、``high``/``low`` は両者の
    max/min、``close`` は最新側（tail 優先・無ければ base）、``volume`` は加算。片方 ``None`` はもう
    一方を（base のみは copy して）返し、両方 ``None`` は ``None``。入力は不変（base を破壊しない）。
    """
    if base is None and tail is None:
        return None
    if tail is None:
        return dict(base)  # 確定末尾以降に tick 無し → base をそのまま（コピー）。
    if base is None:
        return dict(tail)  # 周期先頭（rollup partial 未生成）→ tail のみ（time は tail の周期始端）。
    return {
        "time": base["time"],
        "open": base["open"],
        "high": max(base["high"], tail["high"]),
        "low": min(base["low"], tail["low"]),
        "close": tail["close"],
        "volume": base["volume"] + tail["volume"],
    }


# ISSUE-094 🟡-8: augment_forming_ticks は MP forming payload 整形（A7）であり、本モジュール
#   （形成中バー算出 A4）から MP compute（market_profile_forming）へ実体を移設した。既存呼び出し・
#   テストの互換のため公開名を再エクスポートで温存する（本モジュール経由の参照は不変）。
from market_profile_api.compute.market_profile_forming import (  # noqa: E402,F401
    augment_forming_ticks,
)


def forming_bar_from_buffer_ticks(
    ticks: Any, start_unix: int, now_unix: int
) -> Optional[dict]:
    """in-memory ``LiveTickBuffer`` の ``(unix_ms, mid)`` 昇順列から ``[start_unix, now_unix)`` の
    形成中バー（mid OHLCV・1本）を組む（seed 鮮度化・当日 parquet 窓が空のときの fallback）。

    集計規則は :func:`marketdata.tick_m1.forming_bar_from_ticks` と同一（open=最初/high=最大/
    low=最小/close=最終・volume=tick 数・``time``=期間始端）。同源（mid）のため両経路の値は整合する。
    窓 ``[start_unix*1000, now_unix*1000)`` に tick が無ければ ``None``。純関数（I/O 無し）。
    """
    lo = int(start_unix) * 1000
    hi = int(now_unix) * 1000
    mids = [float(mid) for (ms, mid) in ticks if lo <= ms < hi]
    if not mids:
        return None
    return {
        "time": int(start_unix),
        "open": mids[0],
        "high": max(mids),
        "low": min(mids),
        "close": mids[-1],
        "volume": float(len(mids)),
    }


def _default_rollup_base(ref: str, tf: str) -> Optional[dict]:
    """rollup CSV の現周期 partial バー（最終行）を dict で返す（1m は原子＝rollup 無しで ``None``）。

    ``rollup_store.read`` は末尾数十行のみ逆シーク読みするため O(1) 相当・mtime キャッシュ付き。
    """
    if tf == "1m":
        return None  # 1m は原子（rollup 無し）→ base 無し＝tail のみで組成。
    from marketdata import rollup_store  # 遅延 import（起動コストを増やさない）

    df = rollup_store.read(ref, tf)
    if df is None or len(df) == 0:
        return None
    row = df.iloc[-1]
    lower = {str(c).lower(): c for c in df.columns}
    ts = df.index[-1]
    return {
        "time": int(pd.Timestamp(ts).value // 1_000_000_000),  # naive UTC → unix 秒
        "open": float(row[lower["open"]]),
        "high": float(row[lower["high"]]),
        "low": float(row[lower["low"]]),
        "close": float(row[lower["close"]]),
        "volume": float(row[lower["volume"]]),
    }


def _default_confirmed_end(ref: str) -> Optional[int]:
    """確定末尾 UNIX 秒（rollup が畳んだ最終 m1 分の翌分 = ``last_processed_ts + 60``）。

    ``RollupState`` 不在（未生成）は ``None``。base（rollup partial）の被覆終端と一致するため、
    tail 窓 ``[confirmed_end, now)`` が base とギャップ・重複なく連結する。
    """
    from marketdata.paths import DATA_DIR  # 遅延 import
    from marketdata.rollup import RollupState

    state = RollupState.load(_Path(DATA_DIR) / "rollups" / ref)
    if state is None:
        return None
    return int(pd.Timestamp(state.last_processed_ts).value // 1_000_000_000) + 60


def rollup_forming_bar(
    ref: str,
    tf: str,
    now_unix: int,
    *,
    buffer: Any,
    base_reader: Any = None,
    confirmed_end_reader: Any = None,
) -> Optional[dict]:
    """ロールアップ方式 forming: 確定畳み込み（base=rollup 現周期 partial）＋未確定テール（buffer の
    ``[confirmed_end, now)``）を O(1) で合成する（全 tf・1W/1M 含む）。

    コストは tf 非依存（base=末尾数行読み＋tail=数十秒ぶんの tick）で、全期間の再読み込みを排除する。
    ``base_reader``/``confirmed_end_reader`` は既定で rollup CSV / ``RollupState`` を読む（テストで注入差替）。
    非 tick ref・非対応 tf は ``None``。1W/1M は周期始端を base.time から得るため base 必須
    （周期先頭で base 不在なら誤 time を描かず ``None``）。
    """
    if not is_tick_ref(ref) or tf not in ROLLUP_FORMING_TF:
        return None
    base_reader = base_reader or _default_rollup_base
    confirmed_end_reader = confirmed_end_reader or _default_confirmed_end

    base = base_reader(ref, tf)
    if base is None and tf in _NON_FLOORABLE_TF:
        return None  # 1W/1M は base（周期始端の権威）必須。

    tail = None
    ce = confirmed_end_reader(ref)
    if ce is not None and buffer is not None:
        ticks = buffer.ticks_since(int(ce) * 1000 - 1)  # 確定末尾以降（境界含む）。
        tail = forming_bar_from_buffer_ticks(ticks, int(ce), now_unix)
    return merge_forming(base, tail)


def apply_forming_bar(df: "pd.DataFrame", ref: str, tf: str, now_unix: int, *,
                      synthesize_closed_gaps: bool = True) -> "pd.DataFrame":
    """``df``（date-index OHLCV）の末尾へ現在形成中バーを **set/replace** して返す。

    対象外 ref/tf・ティック無し・空 df なら ``df`` をそのまま返す（後方互換）。形成中バーの ``time``
    （＝``floor(now, tf)``）が ``df`` 末尾と同一なら置換、新しければ追加する（``updateLastCandle`` と
    同じ append/replace 規則）。既存 df より過去の time（異常）は触らない。

    注入対象は 2 種あり、性質が異なるため呼び出し側が選べる（ISSUE-361）:

    形成中バー（常に注入）
        まだ確定していないバーであり M1 に存在しない。チャートは必ずこれを描くため、
        計算窓に入らなければ「最新足だけ指標が無い」状態になる。

    欠落閉周期の合成（``synthesize_closed_gaps=True`` のときだけ注入）
        M1/rollup の焼き込み（+12s 猶予）を待つ間、df 末尾と形成中周期の間の**閉じた**周期
        （例: 12:12 確定済・12:14 形成中のときの 12:13）を実 tick の完結窓
        ``forming_bar_from_ticks(s, s+period)`` で合成する（ISSUE-162）。これは
        **確定値の前倒し**であり、後から M1 が同じバーを上書きする＝確定バーのリペイントに
        なる。よって足内更新（``mode='latest'``）専用とし、full では合成しない
        （確定値は一度だけ書かれ以後不変・ユーザー承認設計 2026-07-23）。合成バーは
        ``/candles`` にも無い＝チャートに対応するローソクが無い点も full では不適合。

    ライブ経路の堅牢化: 形成中バー算出（ticks parquet 読込）が torn-read / IO 失敗しても
    **指標計算を落とさず** ``df`` を素通しする（CSV 側 dataset の torn-read フォールバックと整合）。
    """
    try:
        bar = forming_bar(ref, tf, now_unix)
    except Exception as exc:  # noqa: BLE001 — parquet torn-read/IO 失敗は注入せず df 素通し（live 経路堅牢化）
        logger.warning("形成中バー算出に失敗（注入せず継続）: %s/%s (%s)", ref, tf, exc)
        return df
    if df is None or len(df) == 0:
        return df
    if bar is not None:
        t = pd.Timestamp(int(bar["time"]), unit="s")  # naive UTC（df.index と同基準）
        if t < df.index[-1]:
            return df  # 形成中バーが既存末尾より過去 → 触らない（異常時の防御）。

    # ISSUE-162: 注入するバーを先に確定する（欠落閉周期の tick 合成＋形成中バー）。
    #   形成中バーが None（新周期の tick 未着＝境界直後の数秒）でも閉周期合成は独立に行う
    #   （巻き添え早期 return は境界直後の歯抜け再発になる）。固定長 tf のみ対象・tick 無し
    #   周期（週末等）は合成せず skip（実データが無いバーを捏造しない）。
    to_inject = []
    # ref ゲート: tick 系 ref のみ（forming_bar と同一条件）。非 tick ref（sample 等）へ実 tick の
    #   合成バーを混入させない（データ源の混線防止）。
    period = _FIXED_TF_SECONDS.get(tf or "1m") if is_tick_ref(ref) else None
    if period is not None and synthesize_closed_gaps:
        last_unix = int(df.index[-1].timestamp())
        now_i = int(now_unix)
        forming_start = int(bar["time"]) if bar is not None else now_i - (now_i % period)
        gap_starts = range(last_unix + period, forming_start, period)
        for gs in list(gap_starts)[-_MAX_GAP_FILL_PERIODS:]:
            try:
                closed = forming_bar_from_ticks(gs, gs + period)  # 完結窓 [gs, gs+period)
            except Exception as exc:  # noqa: BLE001 — 橋渡しは表示補完・失敗しても本計算を落とさない
                logger.warning("欠落閉周期の合成に失敗（skip）: %s/%s t=%s (%s)", ref, tf, gs, exc)
                continue
            if closed is not None:
                to_inject.append(closed)
    if bar is not None:
        to_inject.append(bar)
    if not to_inject:
        return df  # 注入なし＝同一オブジェクトで素通し（従来挙動・コピーもしない）。

    out = df.copy()
    lower = {str(c).lower(): c for c in out.columns}
    for b in to_inject:
        bt = pd.Timestamp(int(b["time"]), unit="s")
        for key in ("open", "high", "low", "close", "volume"):
            col = lower.get(key)
            if col is not None:
                out.loc[bt, col] = float(b[key])
    return out.sort_index()
