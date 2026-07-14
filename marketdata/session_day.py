"""session_day — セッション日境界の単一定義（ISSUE-078・依頼者承認 2026-07-14）。

セッション日の定義:
    ブローカー時間（America/New_York + 7 時間 ＝ NY 17:00 が 00:00 になる座標系）の暦日。
    境界（セッション日始端）は「NY ローカル前日 17:00」＝夏 21:00 UTC / 冬 22:00 UTC。
    米 DST の切替は IANA tz（zoneinfo 'America/New_York'）へ委譲する（自前カレンダー禁止＝
    制度変更・歴史的切替日も tzdata が単一真実源）。

採用理由（実測・ISSUE-078 調査）:
    - JP225 CFD の休場帯は夏 20:15〜22:00 / 冬 21:15〜23:00 UTC（実測）＝本境界は年間を通じ
      休場帯内にあり取引時間を分断しない（日足の中身はブローカー標準の取引日と一致する）。
    - 週明けオープン（夏 日曜22:03 / 冬 日曜23:00 UTC）は月曜セッションへ帰属し、UTC 暦日切りが
      生んでいた「薄い日曜原子」（足指標の同格計上・zp 幻影滞在 ISSUE-077 の温床）が消滅する。

注意:
    - DST 切替を含むセッションは 23h / 25h になる。「start + 86400」は禁止し、必ず
      :func:`next_session_day_start` を使うこと（切替日に境界がずれる）。
    - NY ローカル 17:00 は DST 切替時刻（02:00）と重ならないため、曖昧・不存在時刻は生じない。

依存方向: 本モジュールは stdlib（zoneinfo/datetime）と numpy のみに依存する（marketdata 内の
最下層 peer・他モジュールを import しない）。日切りが必要な全層（dwell/zp/tf-period/rollup/
frontend 供給値）は本モジュールを唯一の規則源として参照する（再実装を禁ずる）。
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import numpy as np

_NY = ZoneInfo("America/New_York")
# ブローカー時間 = NY + 7h（NY 17:00 → 00:00）。セッション日ラベルはブローカー暦日。
_BROKER_SHIFT = timedelta(hours=7)


def _broker_date(t: "int | float") -> "datetime":
    """UNIX 秒 → ブローカー暦日（naive date を持つ datetime・時刻部は無意味）。"""
    return datetime.fromtimestamp(float(t), tz=_NY) + _BROKER_SHIFT


def _start_of_broker_date(y: int, m: int, d: int) -> int:
    """ブローカー暦日 (y,m,d) のセッション始端 UNIX 秒＝NY ローカル前日 17:00。"""
    ny_naive = datetime(y, m, d) - _BROKER_SHIFT  # 前日 17:00（naive・NY ローカル値）。
    return int(ny_naive.replace(tzinfo=_NY).timestamp())


def session_day_start(t: "int | float") -> int:
    """``t``（UNIX 秒）が属するセッション日の始端 UNIX 秒を返す（境界ちょうどは新セッション）。"""
    b = _broker_date(t)
    return _start_of_broker_date(b.year, b.month, b.day)


def next_session_day_start(t: "int | float") -> int:
    """``t`` が属するセッション日の翌セッション始端（半開区間の終端）を返す。

    DST 切替日はセッション長が 23h/25h になるため ``start+86400`` を使ってはならない。
    """
    b = _broker_date(t) + timedelta(days=1)
    return _start_of_broker_date(b.year, b.month, b.day)


def session_date_label(t: "int | float") -> str:
    """``t`` が属するセッション日のラベル 'YYYY-MM-DD'（ブローカー暦日）を返す。"""
    return _broker_date(t).strftime("%Y-%m-%d")


def session_bar_time(t: "int | float") -> int:
    """``t`` が属するセッション日の **1D バー time 規約値**＝ラベル日の UTC 深夜 epoch を返す。

    表示規約（ISSUE-078 単位③）: 1D バーはデータ窓こそ [session_day_start, next) だが、time は
    セッション日ラベル（ブローカー暦日）の UTC 深夜に置く。チャートの日付軸ラベル・既存フロントの
    date→time 突合（dateToUnix(label)）と一致させるための表示座標であり、データ窓の始端ではない。
    """
    b = _broker_date(t)
    return int(datetime(b.year, b.month, b.day, tzinfo=timezone.utc).timestamp())


def session_label_to_start(label: str) -> int:
    """ラベル 'YYYY-MM-DD' → 当該セッション日の始端 UNIX 秒（:func:`session_date_label` の逆）。"""
    y, m, d = (int(x) for x in str(label).split("-"))
    return _start_of_broker_date(y, m, d)


def session_day_starts(ts: "np.ndarray") -> "np.ndarray":
    """tick 秒配列の各要素をセッション日始端へ写像する（int64・スカラ版と完全一致）。

    境界表を [min, max] を覆う範囲で構築し ``searchsorted`` で引く（要素ごとの tz 変換を避ける）。
    空入力は空配列。
    """
    arr = np.asarray(ts)
    if arr.size == 0:
        return np.empty(0, dtype=np.int64)
    lo = session_day_start(float(arr.min()))
    hi = float(arr.max())
    bounds = [lo]
    while bounds[-1] <= hi:
        bounds.append(next_session_day_start(bounds[-1]))
    b = np.asarray(bounds, dtype=np.int64)
    idx = np.searchsorted(b, arr.astype(np.int64), side="right") - 1
    return b[idx]
