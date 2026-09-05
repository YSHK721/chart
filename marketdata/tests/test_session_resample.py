"""セッション日対応 resample（ISSUE-078 単位③）の検証。

1D/1W/1M はブローカー日（NY17:00 ET 基準・America/New_York+7h）で集計し、ラベルは
ブローカー暦日（naive・意味は UTC 深夜 epoch＝チャート日付軸と一致）。日中足（5m..4h）は
UTC floor のまま resample_ohlc と byte 同一（バー不変）。
"""
from __future__ import annotations

from datetime import datetime, timezone

import pandas as pd

from marketdata import resample as rs


def _utc(y, m, d, hh=0, mm=0):
    return datetime(y, m, d, hh, mm, tzinfo=timezone.utc)


def _m1(rows):
    """rows=[(dt, o, h, l, c, v)] → date-index DataFrame（1 分足原子と同形）。"""
    df = pd.DataFrame(rows, columns=["date", "open", "high", "low", "close", "volume"])
    df["date"] = pd.to_datetime(df["date"]).dt.tz_localize(None)
    return df.set_index("date")


def test_1d_sunday_evening_rows_land_in_monday_bar():
    # 夏: 日曜 22:03/22:04 UTC ＋ 月曜 05:00 UTC → 単一の月曜バー（ラベル 2026-07-13 00:00）。
    df = _m1([
        (_utc(2026, 7, 12, 22, 3), 100, 101, 99, 100.5, 10),
        (_utc(2026, 7, 12, 22, 4), 100.5, 102, 100, 101.0, 5),
        (_utc(2026, 7, 13, 5, 0), 101.0, 105, 101, 104.0, 7),
    ])
    out = rs.resample_ohlc_tf(df, "1D")
    assert list(out.index) == [pd.Timestamp("2026-07-13")]
    row = out.iloc[0]
    assert row["open"] == 100 and row["high"] == 105 and row["low"] == 99
    assert row["close"] == 104.0 and row["volume"] == 22


def test_1d_boundary_2100_utc_splits_sessions_summer():
    # 夏境界 21:00 UTC: 20:59 は当日（7/13 ラベル）、21:00 は翌セッション（7/14 ラベル）。
    df = _m1([
        (_utc(2026, 7, 13, 20, 59), 1, 1, 1, 1, 1),
        (_utc(2026, 7, 13, 21, 0), 2, 2, 2, 2, 1),
    ])
    out = rs.resample_ohlc_tf(df, "1D")
    assert list(out.index) == [pd.Timestamp("2026-07-13"), pd.Timestamp("2026-07-14")]


def test_1d_boundary_2200_utc_splits_sessions_winter():
    df = _m1([
        (_utc(2026, 1, 13, 21, 59), 1, 1, 1, 1, 1),
        (_utc(2026, 1, 13, 22, 0), 2, 2, 2, 2, 1),
    ])
    out = rs.resample_ohlc_tf(df, "1D")
    assert list(out.index) == [pd.Timestamp("2026-01-13"), pd.Timestamp("2026-01-14")]


def test_1w_labels_friday_broker_date():
    # 週足 W-FRI: 月曜セッション（日曜夜 UTC 由来）〜金曜セッションが金曜ブローカー日ラベルの週へ。
    df = _m1([
        (_utc(2026, 7, 12, 22, 3), 1, 1, 1, 1, 1),   # 月曜セッション（7/13）
        (_utc(2026, 7, 16, 12, 0), 2, 3, 1, 2, 1),   # 木曜セッション（7/16）
    ])
    out = rs.resample_ohlc_tf(df, "1W")
    assert list(out.index) == [pd.Timestamp("2026-07-17")]  # 金曜ラベル（週の帰属）。


def test_intraday_passthrough_identical_to_utc_resample():
    # 4h は ISSUE-489（依頼者承認 2026-09-05）でセッション日起点へ変更＝UTC 床の対象外
    #   （下の test_anchored_4h_* が新規約を固定する）。
    df = _m1([
        (_utc(2026, 7, 12, 22, 3), 100, 101, 99, 100.5, 10),
        (_utc(2026, 7, 12, 22, 4), 100.5, 102, 100, 101.0, 5),
        (_utc(2026, 7, 13, 5, 0), 101.0, 105, 101, 104.0, 7),
    ])
    for tf in ("5m", "1h"):
        a = rs.resample_ohlc_tf(df, tf)
        b = rs.resample_ohlc(df, rs.TIMEFRAME_RULES[tf])
        pd.testing.assert_frame_equal(a, b)


def test_anchored_4h_bars_start_at_the_session_day_boundary():
    """ISSUE-489: 4h バーはセッション日境界（夏 21:00 / 冬 22:00 UTC）起点の等間隔グリッド。

    UTC 床だと 22:03 の行は 20:00 バーへ入り、前セッション末尾と混在する（実測 1,525 本）。
    新規約では日曜 22:03 の開場行が 21:00 起点のバーの先頭になる。
    """
    df = _m1([
        (_utc(2026, 7, 12, 22, 3), 100, 101, 99, 100.5, 10),   # 夏: 境界 21:00 UTC
        (_utc(2026, 7, 13, 0, 59), 100.5, 102, 100, 101.0, 5),  # 同じバー（21:00〜01:00）
        (_utc(2026, 7, 13, 1, 0), 101.0, 105, 101, 104.0, 7),   # 次のバー（01:00〜05:00）
        (_utc(2026, 1, 15, 22, 30), 90, 91, 89, 90.5, 3),       # 冬: 境界 22:00 UTC
    ])
    out = rs.resample_ohlc_tf(df.sort_index(), "4h")

    labels = [ts.strftime("%m-%d %H:%M") for ts in out.index]
    assert "07-12 21:00" in labels and "07-13 01:00" in labels
    assert "01-15 22:00" in labels
    first = out.loc[pd.Timestamp("2026-07-12 21:00")]
    assert first["open"] == 100 and first["close"] == 101.0 and first["volume"] == 15


def test_anchored_4h_agrees_with_the_scalar_period_entry():
    """集計（ベクトル）と周期判定（スカラ・tf_meta 経由の唯一入口）の一致（第 2 定義を作らない）。"""
    from marketdata.session_day import session_slot_start

    rows = [
        _utc(2026, 7, 12, 22, 3), _utc(2026, 7, 13, 0, 59), _utc(2026, 7, 13, 1, 0),
        _utc(2026, 7, 13, 12, 34), _utc(2026, 1, 15, 22, 30), _utc(2026, 1, 15, 21, 59),
        _utc(2026, 3, 20, 10, 0),   # 米 DST と EU DST の切替差の週も含める
    ]
    df = _m1([(t, 100, 101, 99, 100.5, 1) for t in rows]).sort_index()
    out = rs.resample_ohlc_tf(df, "4h")

    for t in df.index:
        expected = session_slot_start(int(t.value // 1_000_000_000), 14400)
        containing = out.index[out.index.searchsorted(t, side="right") - 1]
        assert int(containing.value // 1_000_000_000) == expected, str(t)


def test_period_utc_start_maps_labels_to_session_starts():
    # 1D: ラベル 2026-07-13 → セッション始端 2026-07-12 21:00 UTC（夏）。
    assert rs.period_utc_start("1D", pd.Timestamp("2026-07-13")) == pd.Timestamp("2026-07-12 21:00")
    # 1W: 金曜ラベル → 週始端（前週土曜ブローカー日）＝金曜 17:00 NY の 6 日前セッション始端。
    assert rs.period_utc_start("1W", pd.Timestamp("2026-07-17")) == pd.Timestamp("2026-07-10 21:00")
    # 1M: 月末ラベル → 月初ブローカー日のセッション始端。
    assert rs.period_utc_start("1M", pd.Timestamp("2026-07-31")) == pd.Timestamp("2026-06-30 21:00")
    # 日中足: ラベル＝期間始端そのもの（UTC floor）。
    ts = pd.Timestamp("2026-07-13 08:00")
    assert rs.period_utc_start("1h", ts) == ts
