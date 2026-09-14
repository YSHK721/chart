"""session_activity — セッション認識（活発/休場の地図）の純カーネル（ISSUE-094 🔴-2）。

dwell（:mod:`market_profile_dwell`）の 5 アクター同居を解消するため、「セッション認識」アクター
（(曜日×時) のティック密度から活発/休場を判定し、活発帯の秒だけを滞在に計上する規則）を本
モジュールへ分離する。本モジュールは **I/O・キャッシュ・プロセス状態に非依存の純関数のみ** を持つ:

    - :func:`build_active_table`   — ティック秒列 → (7 曜日×24 時) の活発/休場 bool テーブル。
    - :func:`active_seconds_cross` — ``[a, b)`` のうち活発な時間帯に属する秒数（時境界積分）。
    - :func:`table_for_day`        — 「日の属する月初」アンカーで因果的に活動テーブルを導出する規則。
                                     活動テーブル構築（120 日窓のティック読込＋キャッシュ）は呼び出し側から
                                     ``active_table_fn`` として注入する（I/O は本モジュールに持ち込まない）。

集計側（dwell）は本カーネルへ委譲するシン・ラッパー（``_build_active_table`` / ``_active_seconds_cross``
/ ``_table_for_day``）を温存し、既存テストの monkeypatch 経路（``mpd.`` 属性差替）と byte 出力を
不変に保つ。数値規則（``ACTIVE_FRAC``・曜日/時の量子化式・月初アンカーの因果窓）は本モジュールが
唯一の規則源となる（DRY・SRP）。
"""

from __future__ import annotations

import datetime as _dt

import numpy as np

# (曜日×時) のティック数が ピーク×この割合 未満なら「休場」とみなす（試作 prototype_260630-01 と一致）。
ACTIVE_FRAC = 0.10


def build_active_table(secs: "np.ndarray", *, active_frac: float = ACTIVE_FRAC) -> "np.ndarray":
    """ティックから (曜日0-6 × 時0-23) の活動テーブル（True=活発/False=休場）を作る。

    曜日 = ``((s//86400)+3)%7``（1970-01-01=木を Mon0 基準へ）、時 = ``(s%86400)//3600``。
    バケット別ティック数が ピーク×``active_frac`` 以上を活発とする。
    """
    s = np.asarray(secs, dtype=np.int64)
    wd = ((s // 86400) + 3) % 7
    hod = (s % 86400) // 3600
    cnt = np.zeros((7, 24), dtype=np.int64)
    np.add.at(cnt, (wd, hod), 1)
    thr = cnt.max() * active_frac
    return cnt >= thr


def active_seconds_cross(a: int, b: int, table: "np.ndarray") -> int:
    """``[a, b)`` のうち活発な (曜日×時) に属する秒数を時間境界で積分する（跨ぎギャップ用）。"""
    total = 0
    t = int(a)
    b = int(b)
    while t < b:
        nb = (t // 3600 + 1) * 3600
        seg = min(nb, b)
        wd = ((t // 86400) + 3) % 7
        if table[wd, (t % 86400) // 3600]:
            total += seg - t
        t = seg
    return total


def table_for_day(
    symbol: str, day_start: int, *, active_table_days: int, active_table_fn
) -> "np.ndarray":
    """日次/部分ロールアップ用の active table＝「その日の属する月初」アンカー（ISSUE-089）。

    窓は [月初-active_table_days日, 月初)＝当該日より厳密に過去のデータのみ（因果）。日の純関数であり、
    リクエスト窓 t1 やプロセス履歴に依存しない（キャッシュへ焼き込まれる値の決定性を保証）。
    月初アンカーの量子化は表構築（120日ティック読込）を月1回に抑えるため（DST 切替の時間帯シフトへも
    月粒度で追随する）。活動テーブルの構築（ティック読込＋キャッシュ）は ``active_table_fn(symbol,
    at_from, win_to)`` として注入する（本モジュールは I/O 非依存＝規則のみを所有・DIP）。
    """
    d = _dt.datetime.fromtimestamp(int(day_start), tz=_dt.timezone.utc)
    month_start = int(_dt.datetime(d.year, d.month, 1, tzinfo=_dt.timezone.utc).timestamp())
    return active_table_fn(symbol, month_start - int(active_table_days) * 86400, month_start)
