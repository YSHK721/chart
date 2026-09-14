"""``/live_ticks`` のバー帰属（tick → どのバーに属するか）を配信する薄殻。

なぜサーバが返すのか（設計の要）:
    「この時刻はどのバーに属するか」の規則は、日中足＝UTC floor、1D＝セッション日、
    1W/1M＝W-FRI/ME ラベルと**時間足ごとに違う**。これをフロントが再計算すると、規則の第 2 定義が
    生まれ、表せない時間足（暦周期）が構造的に脱落する（実際 1W/1M はライブの tick 再生から
    外れていた）。バー帰属をサーバの唯一源（:func:`marketdata.tf_meta.bar_time_unix`）で解決して
    tick と一緒に配ることで、フロントは規則を持たず、**全時間足がひとつの経路・同じ更新粒度**に
    なる（リプレイが ``cd.time`` をデータから受け取っているのと同じ設計）。

責務: クエリ検証（timeframe）→ 唯一源の呼び出し → 応答フィールドの組み立てのみ。規則は持たない。
timeframe 未指定／未知は ``None``＝``/live_ticks`` は従来応答のまま（後方互換）。
"""

from __future__ import annotations

from typing import Any

from marketdata.resample import is_known_timeframe
from marketdata.tf_meta import bar_time_unix


def handle_live_tick_bar_times(
    query: "dict[str, list[str]]", ticks: "list", now_ms: int
) -> "dict[str, Any] | None":
    """``{"barTimes": [...], "nowBarTime": int}`` を組む（組めないときは ``None``）。

    Args:
        query: ``/live_ticks`` のクエリ（``timeframe`` を読む）。
        ticks: 配信する ``[[ms, mid], ...]``。``barTimes`` は同数・同順。
        now_ms: サーバ現在時刻（ms）。``nowBarTime`` はフロントが「現在のバーより前の tick か」を
            判定する材料で、これもフロント側で時刻から導出させない。
    """
    tf = (query.get("timeframe") or [None])[0]
    if not is_known_timeframe(tf):
        return None
    return {
        "barTimes": [bar_time_unix(tf, int(t[0]) // 1000) for t in ticks],
        "nowBarTime": bar_time_unix(tf, int(now_ms) // 1000),
    }
