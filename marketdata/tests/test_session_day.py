"""session_day（セッション日境界・ISSUE-078）の検証。

セッション日の定義: ブローカー時間（America/New_York + 7 時間＝NY17:00 が 00:00）の暦日。
境界は夏 21:00 UTC / 冬 22:00 UTC（米DSTで自動切替＝IANA tz に委譲・自前カレンダー禁止）。

期待値はテスト側で UTC の datetime から独立に構築する（実装と同じ NY tz 変換式を再利用しない）。
実測根拠（ISSUE-078 調査）: JP225 CFD 休場帯は夏 20:15-22:00 / 冬 21:15-23:00 UTC＝境界は
夏冬とも休場帯内。週明けオープンは夏 日曜22:03 / 冬 日曜23:00 UTC＝月曜セッションへ帰属する。
"""
from __future__ import annotations

from datetime import datetime, timezone

import numpy as np
import pytest

from marketdata import session_day as sd


def utc(y: int, m: int, d: int, hh: int = 0, mm: int = 0, ss: int = 0) -> int:
    return int(datetime(y, m, d, hh, mm, ss, tzinfo=timezone.utc).timestamp())


# ---------------------------------------------------------------------------
# session_day_start: 夏（EDT）＝21:00 UTC 境界
# ---------------------------------------------------------------------------
def test_summer_boundary_is_2100_utc():
    # 2026-07-13(月) 21:00:00 UTC が「7/14 セッション」の始端（NY 7/13 17:00 EDT）。
    b = utc(2026, 7, 13, 21, 0, 0)
    assert sd.session_day_start(b) == b            # 境界ちょうどは新セッションに属す。
    assert sd.session_day_start(b - 1) == b - 86400  # 直前秒は前セッション（7/12 21:00 UTC 始まり）。
    assert sd.session_day_start(b + 3600) == b


def test_winter_boundary_is_2200_utc():
    # 2026-01-13(火) 22:00:00 UTC が「1/14 セッション」の始端（NY 1/13 17:00 EST）。
    b = utc(2026, 1, 13, 22, 0, 0)
    assert sd.session_day_start(b) == b
    assert sd.session_day_start(b - 1) == b - 86400


# ---------------------------------------------------------------------------
# 週明け（日曜夜 UTC）の月曜セッション帰属＝薄い日曜原子の消滅（ISSUE-078 の主目的）
# ---------------------------------------------------------------------------
def test_sunday_evening_ticks_belong_to_monday_session_summer():
    # 実測: 2026-07-12(日) 22:03:44 UTC 週明けオープン。
    t = utc(2026, 7, 12, 22, 3, 44)
    assert sd.session_day_start(t) == utc(2026, 7, 12, 21, 0, 0)
    assert sd.session_date_label(t) == "2026-07-13"  # 月曜セッション。


def test_sunday_evening_ticks_belong_to_monday_session_winter():
    # 実測: 2026-01-11(日) 23:00 UTC 週明けオープン。
    t = utc(2026, 1, 11, 23, 0, 37)
    assert sd.session_day_start(t) == utc(2026, 1, 11, 22, 0, 0)
    assert sd.session_date_label(t) == "2026-01-12"  # 月曜セッション。


def test_friday_close_stays_in_friday_session():
    # 実測: 金曜クローズ 夏 20:14:59 UTC（2026-07-10）は金曜セッション（7/9 21:00 始まり）。
    t = utc(2026, 7, 10, 20, 14, 59)
    assert sd.session_day_start(t) == utc(2026, 7, 9, 21, 0, 0)
    assert sd.session_date_label(t) == "2026-07-10"


# ---------------------------------------------------------------------------
# DST 切替日: セッション長が 23h / 25h になる（境界は NY ローカル 17:00 固定）
# ---------------------------------------------------------------------------
def test_spring_dst_transition_makes_23h_session():
    # 2026 春: 3月第2日曜=3/8 02:00 EST→EDT。「3/8 セッション」は 3/7 17:00 EST(22:00 UTC)
    #   → 3/8 17:00 EDT(21:00 UTC) の 23 時間。
    start = sd.session_day_start(utc(2026, 3, 8, 0, 0, 0))
    assert start == utc(2026, 3, 7, 22, 0, 0)
    assert sd.next_session_day_start(start) - start == 23 * 3600


def test_fall_dst_transition_makes_25h_session():
    # 2026 秋: 11月第1日曜=11/1 02:00 EDT→EST。「11/1 セッション」は 10/31 17:00 EDT(21:00 UTC)
    #   → 11/1 17:00 EST(22:00 UTC) の 25 時間。
    start = sd.session_day_start(utc(2026, 11, 1, 0, 0, 0))
    assert start == utc(2026, 10, 31, 21, 0, 0)
    assert sd.next_session_day_start(start) - start == 25 * 3600


def test_normal_session_is_24h():
    start = sd.session_day_start(utc(2026, 7, 8, 12, 0, 0))
    assert sd.next_session_day_start(start) - start == 86400


# ---------------------------------------------------------------------------
# ラベルと逆変換
# ---------------------------------------------------------------------------
def test_label_roundtrip():
    t = utc(2026, 7, 13, 5, 30, 0)  # 7/13 セッション中（7/12 21:00 始まり）。
    label = sd.session_date_label(t)
    assert label == "2026-07-13"
    assert sd.session_label_to_start(label) == utc(2026, 7, 12, 21, 0, 0)
    # 冬側も往復一致。
    assert sd.session_label_to_start("2026-01-12") == utc(2026, 1, 11, 22, 0, 0)


def test_session_day_start_is_idempotent():
    for t in [utc(2026, 7, 12, 22, 3, 44), utc(2026, 1, 11, 23, 0, 37), utc(2026, 3, 8, 12, 0, 0)]:
        s = sd.session_day_start(t)
        assert sd.session_day_start(s) == s


# ---------------------------------------------------------------------------
# ベクトル版（tick 配列の日割り用）: スカラ版と完全一致
# ---------------------------------------------------------------------------
def test_vectorized_matches_scalar_across_dst():
    # 冬→夏 DST を跨ぐ 2026-03-01..03-15 を 1 時間刻みで走査。
    ts = np.arange(utc(2026, 3, 1), utc(2026, 3, 15), 3600, dtype=np.int64)
    vec = sd.session_day_starts(ts)
    scalar = np.array([sd.session_day_start(int(t)) for t in ts], dtype=np.int64)
    np.testing.assert_array_equal(vec, scalar)


def test_vectorized_empty_input():
    out = sd.session_day_starts(np.array([], dtype=np.int64))
    assert out.dtype == np.int64 and out.size == 0


# ---------------------------------------------------------------------------
# session_bar_time（ISSUE-078 単位③）: 1D バーの time 規約＝セッション日ラベルの UTC 深夜 epoch。
#   チャートの日付軸ラベル・既存フロント（dateToUnix(label)）との整合をとる表示規約。
# ---------------------------------------------------------------------------
def test_session_bar_time_is_utc_midnight_of_label():
    t = utc(2026, 7, 12, 22, 3, 44)  # 日曜夜＝月曜セッション（ラベル 2026-07-13）。
    assert sd.session_date_label(t) == "2026-07-13"
    assert sd.session_bar_time(t) == utc(2026, 7, 13)  # ラベル日の UTC 深夜。
    # セッション始端そのものでも同じラベル日へ写像される。
    assert sd.session_bar_time(utc(2026, 7, 12, 21, 0, 0)) == utc(2026, 7, 13)
    # 冬。
    assert sd.session_bar_time(utc(2026, 1, 11, 23, 0, 37)) == utc(2026, 1, 12)


class TestSessionPeriodLabels:
    """ISSUE-086: 週/月バケットのラベル規約（rollup=resample の W-FRI / ME と厳密一致）。"""

    def test_week_label_is_friday_of_broker_week(self):
        from marketdata.session_day import session_period_label

        # 2026-07-15（水）のセッション → 週ラベルは同週金曜 2026-07-17。
        t = sd.session_label_to_start("2026-07-15")
        assert session_period_label("1W", t) == "2026-07-17"
        # 土曜開始（週= [土..金]）: 2026-07-11（土）→ 翌金曜 2026-07-17。
        t2 = sd.session_label_to_start("2026-07-11")
        assert session_period_label("1W", t2) == "2026-07-17"
        # 金曜自身は自分がラベル。
        t3 = sd.session_label_to_start("2026-07-17")
        assert session_period_label("1W", t3) == "2026-07-17"

    def test_month_label_is_calendar_month_end(self):
        from marketdata.session_day import session_period_label

        t = sd.session_label_to_start("2026-07-15")
        assert session_period_label("1M", t) == "2026-07-31"
        t2 = sd.session_label_to_start("2026-02-03")
        assert session_period_label("1M", t2) == "2026-02-28"

    def test_period_session_labels_enumerates_bucket_days(self):
        from marketdata.session_day import period_session_labels

        # 週 2026-07-17: 土 07-11 〜 金 07-17 の 7 ブローカー暦日。
        labels = period_session_labels("1W", "2026-07-17")
        assert labels[0] == "2026-07-11" and labels[-1] == "2026-07-17" and len(labels) == 7
        # 月 2026-02-28: 02-01 〜 02-28。
        labels_m = period_session_labels("1M", "2026-02-28")
        assert labels_m[0] == "2026-02-01" and labels_m[-1] == "2026-02-28" and len(labels_m) == 28

    def test_labels_match_reference_resample(self):
        """参照実装一致: 合成 1 分足を resample_ohlc_tf('1W'/'1M') した bar ラベル集合と、
        各セッション日を session_period_label で束ねたラベル集合が一致する。"""
        import numpy as np
        import pandas as pd
        from marketdata.resample import resample_ohlc_tf
        from marketdata.session_day import session_period_label

        # ISSUE-088 🔵-1: 通常区間に加え、米 DST 切替（2026-03-08 春・2025-11-02 秋）を跨ぐ区間も検定。
        ranges = [
            ("2026-06-01", "2026-07-14"),
            ("2026-02-20", "2026-03-20"),  # 春切替（23h セッション日を含む週/月）
            ("2025-10-20", "2025-11-20"),  # 秋切替（25h セッション日を含む週/月）
        ]
        for start, end in ranges:
            self._assert_range(start, end)

    def _assert_range(self, start: str, end: str) -> None:
        import pandas as pd
        from marketdata.resample import resample_ohlc_tf
        from marketdata.session_day import session_period_label

        idx = pd.date_range(start, end, freq="1min")
        df = pd.DataFrame(
            {"open": 1.0, "high": 1.0, "low": 1.0, "close": 1.0, "volume": 1},
            index=idx,
        )
        for tf in ("1W", "1M"):
            bars = resample_ohlc_tf(df, tf)
            ref_labels = {ts.strftime("%Y-%m-%d") for ts in bars.index}
            secs = idx.values.astype("datetime64[s]").astype("int64")  # 単位非依存の秒化。
            mine = {session_period_label(tf, int(s)) for s in secs[:: 60 * 12]}
            assert mine == ref_labels, f"{tf} [{start}..{end}]: {sorted(mine)} != {sorted(ref_labels)}"


    def test_next_period_label_year_cross_and_feb(self):
        """ISSUE-088 🔵-5: 翌バケットラベル（1W=+7日 / 1M=翌月末・年跨ぎ/閏 2 月）。"""
        from marketdata.session_day import next_period_label

        assert next_period_label("1W", "2026-12-25") == "2027-01-01"
        assert next_period_label("1M", "2026-12-31") == "2027-01-31"
        assert next_period_label("1M", "2026-01-31") == "2026-02-28"
        assert next_period_label("1M", "2028-01-31") == "2028-02-29"  # 閏年。
        assert next_period_label("1M", "2026-03-31") == "2026-04-30"
