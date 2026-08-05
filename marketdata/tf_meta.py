"""tf メタ・tick ref・期間始端・now 解決の単一情報源（ISSUE-087 🔴-1/🔴-2）。

移設元: indigators/indicator_ui/api/adapter/compute/forming_bar.py の純関数群。
market_profile_api と indicator_ui api の両方が本モジュールを同格に参照することで、
MP→indicator_ui の裸パッケージ依存（sys.path 注入前提の横断結合）を排する。
規則源: 周期集合・floor 可否は :data:`marketdata.resample.TIMEFRAME_RULES`、
1D セッション始端は :mod:`marketdata.session_day`（二重定義しない）。
"""
from __future__ import annotations

import logging
import os
import time
from typing import Any, Optional

import pandas as pd

from marketdata import dataset_registry
from marketdata.resample import (
    CALENDAR_LABEL_TFS,
    TF_DESCRIPTORS,
    TIMEFRAME_RULES,
    period_utc_start,
)
from marketdata.session_day import session_bar_time, session_day_start, session_period_label

# 形成中バー/tf-period を供給する datasetRef（ティック由来＝ticks parquet を持つ）。値の源は
# marketdata.dataset_registry の記述子レジストリ（唯一源・ISSUE-094 🟡-9）。定義位置は本モジュール
# のまま（利用側は tf_meta.TICK_REFS を無変更参照）。registry→dataset の中立配置で循環を避ける。
TICK_REFS = dataset_registry.tick_refs()

# カレンダー周期（W-FRI/ME）は単純 floor で期間始端を表せない。
# 台帳 :data:`marketdata.resample.TF_DESCRIPTORS` の floorable フラグからの導出値（唯一源・ISSUE-134）。
# 名称は外部消費者（indicator_ui forming_bar が import）を非破壊にするため温存する。
NON_FLOORABLE_TF = frozenset(
    code for code, d in TF_DESCRIPTORS.items() if not d.floorable
)

# tf → バー秒長（名目値）。1W=7日・1M=30日名目（カレンダー tf の窓幅・表示計算用。
#   厳密な期間境界は resample/session_day のラベル規約が担う＝本表を境界計算に使わない）。
#
# 台帳 :data:`marketdata.resample.TF_DESCRIPTORS` の ``bar_sec`` からの導出値（唯一源・ISSUE-261）。
#   かつては手書き dict で、検定も `set(TF_BAR_SEC) == set(TIMEFRAME_RULES)`（キー集合のみ）
#   だったため**値のずれは検出できなかった**。時間足の追加は台帳 1 行で完結する。
#   名称・型（dict）・挿入順は外部消費者（monkeypatch.setitem する回帰テストを含む）を
#   非破壊にするため温存する。
TF_BAR_SEC: "dict[str, int]" = {
    code: d.bar_sec for code, d in TF_DESCRIPTORS.items()
}

# プロセス起動時刻（resolve_now_unix のデモ時計の経過基準）。
_BOOT_MONOTONIC = time.monotonic()


def is_tick_ref(ref: Any) -> bool:
    """形成中バー/tf-period 供給対象の ref か（ティック由来）。"""
    return ref in TICK_REFS


def floor_freq(tf: Any) -> Optional[str]:
    """tf の pandas floor freq を TIMEFRAME_RULES から導出する（1W/1M・未知は None）。"""
    if tf in NON_FLOORABLE_TF or tf not in TIMEFRAME_RULES:
        return None
    rule = TIMEFRAME_RULES[tf]
    return "min" if rule is None else rule


def is_supported_timeframe(tf: Any) -> bool:
    """固定周期（floor 可能）tf か（1W/1M・未知は False）。"""
    return floor_freq(tf) is not None


def bar_time_unix(tf: str, unix_sec: int) -> int:
    """``unix_sec`` が属する tf バーの **time**（チャート time 規約・UNIX 秒）を返す。

    「この時刻はどのバーに属するか」の**唯一の入口**（全 tf・分岐は本関数の内側だけ）。消費側
    （ライブ tick 再生・形成中バーの畳み込み・指標末尾値）は tf ごとに規則を持たず本関数を呼ぶ。

    規則そのものは既存の唯一源の**合成**であり、本関数は新しい暦計算を持たない:
      日中足(1m..4h): UTC floor（resample の左ラベル）
      1D            : :func:`marketdata.session_day.session_bar_time`（ブローカー暦日ラベルの UTC 深夜）
      1W/1M         : :func:`marketdata.session_day.session_period_label`（W-FRI/ME・実体は
                      :func:`marketdata.resample.period_label_naive`）→ ラベル日の UTC 深夜

    ラベル規約はロールアップ（``resample_ohlc_tf``）が書き出すバー time と同一であり、
    ``/candles`` の足・``/forming_bar`` の形成中バーと必ず同じ点に載る。
    """
    unix_sec = int(unix_sec)
    if tf in CALENDAR_LABEL_TFS:
        label = session_period_label(tf, unix_sec)          # 'YYYY-MM-DD'（右端ラベル）
        return int(pd.Timestamp(label).value // 1_000_000_000)
    if tf == "1D":
        return session_bar_time(unix_sec)
    return period_start_unix(unix_sec, tf)


def period_start_unix(now_unix: int, tf: str) -> int:
    """``now_unix`` が属する期間の **UTC 始端** 秒（``bar_time_unix`` の time とは別物）。

    time（ラベル）は表示・突合の鍵、始端は「その期間の tick 窓の左端」。セッション tf では
    両者が一致しない（1D のラベルは暦日の UTC 深夜／始端は前日 NY17:00、1W/1M のラベルは
    期間の右端）。規則源は :mod:`marketdata.session_day` と :func:`marketdata.resample.period_utc_start`。
    """
    now_unix = int(now_unix)
    if tf in CALENDAR_LABEL_TFS:
        label = session_period_label(tf, now_unix)
        start = period_utc_start(tf, pd.Timestamp(label))
        return int(start.value // 1_000_000_000)
    if tf == "1D":
        return session_day_start(now_unix)
    start = pd.Timestamp(now_unix, unit="s").floor(floor_freq(tf))  # naive UTC
    return int(start.value // 1_000_000_000)


def resolve_now_unix(override: Any = None) -> int:
    """基準時刻 now（UNIX 秒・UTC）を解決する（時刻取得の単一注入点）。

    優先順位: ①override（int・bool 除外）②env FORMING_DEMO_NOW="<base>[:<speed>]"
    （デモ時計＝base から実経過×speed）③実 UTC 現在。
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
            logging.getLogger(__name__).warning(
                "FORMING_DEMO_NOW の形式が不正です: %r（実時刻にフォールバック）", demo)
    return int(time.time())
