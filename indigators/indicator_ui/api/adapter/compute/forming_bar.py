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
from typing import Any, Optional

import pandas as pd

logger = logging.getLogger(__name__)

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
