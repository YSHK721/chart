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

依存方向: 本モジュールは stdlib（zoneinfo/datetime）・numpy・pandas と、週/月ラベル規則の唯一源
:mod:`marketdata.resample`（ISSUE-094 🟡-10a）に依存する（resample は pandas のみに依存する葉＝
循環しない）。日切りが必要な全層（dwell/zp/tf-period/rollup/frontend 供給値）は本モジュールを
唯一の規則源として参照する（再実装を禁ずる）。
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

from marketdata.resample import period_label_naive

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


# --------------------------------------------------------------------------- #
# 週/月バケットのラベル規約（ISSUE-086: 全時間足パラメータ統一）
#   規約源は marketdata.resample の 1W='W-FRI'（週= [土..金] ブローカー日・ラベル=金曜）と
#   1M='ME'（ラベル=暦月末日）。従来はブローカー暦日の手書き算術で同値を再実装し「テストで一致
#   担保」していた（規則の二重表現）。ISSUE-094 🟡-10a: 規則源 resample.period_label_naive への
#   単方向委譲へ構造変更する（数値/ラベル出力は byte 不変・全期間 40 万点で一致実測済み）。
# --------------------------------------------------------------------------- #
def session_period_label(tf: str, t: "int | float") -> str:
    """``t`` が属するセッション日の 1W/1M バケットラベル 'YYYY-MM-DD' を返す。

    1W: 同ブローカー週（土..金）の金曜。1D バーの W-FRI resample ラベルと一致する。
    1M: 同ブローカー月の暦月末日（ME ラベル）。tf は '1W'|'1M' のみ（他は ValueError）。

    ラベル規則は :func:`marketdata.resample.period_label_naive`（W-FRI/ME の唯一源）へ委譲する。
    ブローカー暦日 ``b`` を naive 化して渡し、pandas offset の rollforward で右端ラベルを得る。
    """
    b = _broker_date(t)
    lab = period_label_naive(tf, pd.Timestamp(b).tz_localize(None))
    return lab.strftime("%Y-%m-%d")


def period_session_labels(tf: str, label: str) -> "list[str]":
    """バケットラベル → 当該バケットに属する全ブローカー暦日ラベル（昇順）を返す。

    1W: [ラベル金曜-6日（土）.. ラベル金曜] の 7 日。1M: [1日 .. 月末日]。
    休場日もラベルとしては列挙する（データ有無は呼び出し側の日次計算が空で吸収する）。
    """
    y, m, d = (int(x) for x in str(label).split("-"))
    end = datetime(y, m, d).date()
    if tf == "1W":
        first = end - timedelta(days=6)
    elif tf == "1M":
        first = end.replace(day=1)
    else:
        raise ValueError(f"period_session_labels: 1W|1M のみ対応: {tf!r}")
    out = []
    cur = first
    while cur <= end:
        out.append(cur.strftime("%Y-%m-%d"))
        cur += timedelta(days=1)
    return out


def next_period_label(tf: str, label: str) -> str:
    """バケットラベル → 次バケットのラベル（1W: +7日 / 1M: 翌月末）。"""
    y, m, d = (int(x) for x in str(label).split("-"))
    cur = datetime(y, m, d).date()
    if tf == "1W":
        nxt = cur + timedelta(days=7)
    elif tf == "1M":
        first_next = (cur.replace(day=1) + timedelta(days=32)).replace(day=1)
        nxt = (first_next + timedelta(days=32)).replace(day=1) - timedelta(days=1)
    else:
        raise ValueError(f"next_period_label: 1W|1M のみ対応: {tf!r}")
    return nxt.strftime("%Y-%m-%d")
