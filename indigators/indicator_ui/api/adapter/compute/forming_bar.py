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

# プロセス起動時刻（デモ時計の経過基準）。
_BOOT_MONOTONIC = time.monotonic()


def resolve_now_unix(override: Any = None) -> int:
    """形成中バーの基準時刻 now（UNIX 秒・UTC）を解決する（時刻取得の単一注入点）。

    優先順位:
      1. ``override``（int・bool 除外）= リクエストの ``formingNow``/``now``（テスト/クライアント注入）。
      2. env ``FORMING_DEMO_NOW``（デモ時計）= ``"<base_unix>[:<speed>]"``。base から実経過×speed を
         進めたデモ時刻を返す（ライブ tick 供給が無い静的データで足内更新を可視化する再生用・
         本番は未設定で無効）。speed 省略は 1.0。
      3. それ以外 = 実 UTC 現在（``time.time()``）。
    """
    if isinstance(override, int) and not isinstance(override, bool):
        return override
    demo = os.environ.get("FORMING_DEMO_NOW")
    if demo:
        base, _, sp = demo.partition(":")
        try:
            speed = float(sp) if sp else 1.0
            return int(float(base) + (time.monotonic() - _BOOT_MONOTONIC) * speed)
        except ValueError:
            logger.warning("FORMING_DEMO_NOW の形式が不正です: %r（実時刻にフォールバック）", demo)
    return int(time.time())

# repo 根を sys.path へ（marketdata を import するため・dataset/rollup_store と同じロード境界）。
import sys as _sys
from pathlib import Path as _Path

_WORKSPACE_ROOT = _Path(__file__).resolve().parents[5]
if str(_WORKSPACE_ROOT) not in _sys.path:
    _sys.path.insert(0, str(_WORKSPACE_ROOT))
from marketdata.resample import TIMEFRAME_RULES  # noqa: E402  (規則源・floor freq を導出)
from marketdata.tick_m1 import forming_bar_from_ticks  # noqa: E402

# 形成中バーを供給する datasetRef（ティック由来＝ticks parquet を持つ）。これ以外は対象外。
TICK_REFS = frozenset({"jp225_tick"})

# カレンダー周期（W-FRI/ME）は単純 floor で期間始端を表せない＝形成中バー非対応。
_NON_FLOORABLE_TF = frozenset({"1W", "1M"})

# ロールアップ方式 forming が対応する全 tf（1m＋上位足 5m..1M）。ロールアップの現周期 partial バー
# （base）から周期始端を得るため、1W/1M も floor 不要で対応できる（rollup ラベル＝始端）。
ROLLUP_FORMING_TF = frozenset(TIMEFRAME_RULES)


def _floor_freq(tf: Any) -> Optional[str]:
    """tf の pandas floor freq を :data:`marketdata.resample.TIMEFRAME_RULES` から導出する。

    規則源を marketdata.resample に単一化し（§4・floor freq を二重定義しない）、形成中バーの
    期間始端 ``floor(now, tf)`` がロールアップ足境界（resample 固定周期ラベル＝左端）と整合する。
    1m（rule=None）は分床 ``"min"``。5m..1D は rule 文字列がそのまま floor freq（``"5min"/"1h"/"1D"``）。
    1W/1M（``W-FRI``/``ME``）は floor 不可で ``None``（非対応）。未知 tf も ``None``。
    """
    if tf in _NON_FLOORABLE_TF or tf not in TIMEFRAME_RULES:
        return None
    rule = TIMEFRAME_RULES[tf]
    return "min" if rule is None else rule


def is_tick_ref(ref: Any) -> bool:
    """形成中バー供給対象の ref か（ティック由来）。"""
    return ref in TICK_REFS


def is_supported_timeframe(tf: Any) -> bool:
    """形成中バーを供給できる固定周期 tf か（1W/1M・未知は False）。"""
    return _floor_freq(tf) is not None


def period_start_unix(now_unix: int, tf: str) -> int:
    """現在期間の始端 UNIX 秒（``floor(now, tf)``・UTC・規則源は marketdata.resample）。"""
    start = pd.Timestamp(int(now_unix), unit="s").floor(_floor_freq(tf))  # naive UTC
    return int(start.value // 1_000_000_000)


def forming_bar(ref: str, tf: str, now_unix: int) -> Optional[dict]:
    """``ref``/``tf`` の現在形成中バーを返す（対象外 ref/tf・ティック無しは ``None``）。

    ``[floor(now, tf), now)`` の実ティックを :func:`forming_bar_from_ticks` で集計する。
    """
    if not is_tick_ref(ref) or not is_supported_timeframe(tf):
        return None
    start = period_start_unix(now_unix, tf)
    return forming_bar_from_ticks(start, int(now_unix))


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


def augment_forming_ticks(
    parquet_ticks: Any,
    buffer_ticks: Any,
    forming_start: int,
    now_unix: int,
    since: Any = None,
) -> list:
    """MP 形成中期間の tick 列 ``[[sec, mid]...]`` を in-memory buffer で補完する（秒成長の遅延解消）。

    parquet 由来 ``parquet_ticks``（``[[sec, mid]...]``・当日フロンティア遅延で末尾が欠ける）に、
    ``buffer_ticks``（``[(unix_ms, mid)...]``・near-real-time）のうち **parquet 被覆の最終秒より後**かつ
    窓 ``[forming_start, now_unix)`` 内の tick を **秒重複なく**追加する（parquet 優先）。``since`` 指定時は
    合成結果へ ``sec > since`` を適用（クライアント既取得分を除外＝base=0 増分）。純関数（I/O 無し）。
    buffer 空 → parquet の窓内クランプのみ＝現行挙動不変。
    """
    lo, hi = int(forming_start), int(now_unix)
    result = [[int(t[0]), float(t[1])] for t in parquet_ticks if lo <= int(t[0]) < hi]
    parquet_max = max((t[0] for t in result), default=lo - 1)
    for tk in buffer_ticks or ():
        sec = int(tk[0] // 1000)
        if lo <= sec < hi and sec > parquet_max:  # parquet 末尾より後だけ補完（二重計上なし）
            result.append([sec, float(tk[1])])
    if since is not None:
        s = int(since)
        result = [t for t in result if t[0] > s]
    result.sort(key=lambda t: t[0])
    return result


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


def apply_forming_bar(df: "pd.DataFrame", ref: str, tf: str, now_unix: int) -> "pd.DataFrame":
    """``df``（date-index OHLCV）の末尾へ現在形成中バーを **set/replace** して返す（指標の足内更新用）。

    対象外 ref/tf・ティック無し・空 df なら ``df`` をそのまま返す（後方互換）。形成中バーの ``time``
    （＝``floor(now, tf)``）が ``df`` 末尾と同一なら置換、新しければ追加する（``updateLastCandle`` と
    同じ append/replace 規則）。これにより ``mode='latest'`` の最新点再計算が形成中バー込みで走る。
    既存 df より過去の time（異常）は触らない。

    ライブ経路の堅牢化: 形成中バー算出（ticks parquet 読込）が torn-read / IO 失敗しても
    **指標計算を落とさず** ``df`` を素通しする（CSV 側 dataset の torn-read フォールバックと整合）。
    """
    try:
        bar = forming_bar(ref, tf, now_unix)
    except Exception as exc:  # noqa: BLE001 — parquet torn-read/IO 失敗は注入せず df 素通し（live 経路堅牢化）
        logger.warning("形成中バー算出に失敗（注入せず継続）: %s/%s (%s)", ref, tf, exc)
        return df
    if bar is None or df is None or len(df) == 0:
        return df
    t = pd.Timestamp(int(bar["time"]), unit="s")  # naive UTC（df.index と同基準）
    if t < df.index[-1]:
        return df  # 形成中バーが既存末尾より過去 → 触らない（異常時の防御）。
    out = df.copy()
    lower = {str(c).lower(): c for c in out.columns}
    for key in ("open", "high", "low", "close", "volume"):
        col = lower.get(key)
        if col is not None:
            out.loc[t, col] = float(bar[key])
    return out.sort_index()
