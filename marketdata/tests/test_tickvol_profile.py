"""tickvol_profile（取引密度の時刻帯プロファイル）の検証。

仕様（依頼者確定 2026-08-01）:
    ビン = セッション日内オフセットの 15 分固定量子化、集計 = 直前 N セッション日（当日を含まない）
    の各ビン合計の「日をまたいだ中央値」、判定 = 代表値分布の pct パーセンタイル以上、
    帯 = 連続 HIGH ビンの結合。

合成データでアルゴリズム契約を固定し、最後に実データ（jp225_tick）へ `until` を固定して
実測済みの帯をピン留めする（実測値は計画時に独立集計で確認済み）。
"""
from __future__ import annotations

from datetime import datetime, timezone

import numpy as np
import pandas as pd
import pytest

from marketdata import tickvol_profile as tv
from marketdata.session_day import next_session_day_start, session_day_start


def utc(y: int, m: int, d: int, hh: int = 0, mm: int = 0) -> int:
    return int(datetime(y, m, d, hh, mm, tzinfo=timezone.utc).timestamp())


def build_df(day_starts, per_bin_volume, minutes_per_day=None):
    """各セッション日について、1 分足を並べた DataFrame を作る。

    per_bin_volume: (day_index, bin_index) -> 1 分あたり volume。
    """
    rows = []
    for di, start in enumerate(day_starts):
        n = minutes_per_day[di] if minutes_per_day else 24 * 60
        for m in range(n):
            t = start + m * 60
            b = (t - start) // tv.BIN_SEC
            rows.append((t, float(per_bin_volume(di, int(b)))))
    idx = pd.to_datetime([r[0] for r in rows], unit="s")
    return pd.DataFrame({"volume": [r[1] for r in rows]}, index=idx)


DAY0 = session_day_start(utc(2026, 7, 20, 12))
DAYS = [DAY0]
for _ in range(4):
    DAYS.append(next_session_day_start(DAYS[-1]))


# --------------------------------------------------------------------------- #
# ビン化と中央値
# --------------------------------------------------------------------------- #
def test_bin_is_15min_and_value_is_median_of_per_day_sums():
    # ビン 0 は毎分 10（15 分＝150/日）、ビン 1 は毎分 100（1500/日）、他は 1。
    def vol(_di, b):
        return {0: 10.0, 1: 100.0}.get(b, 1.0)

    p = tv.session_offset_profile(build_df(DAYS[:3], vol), until=None)
    assert p["bin_sec"] == 900
    assert p["day_count"] == 3
    assert p["values"][0] == pytest.approx(150.0)   # 15 本 × 10
    assert p["values"][1] == pytest.approx(1500.0)  # 15 本 × 100
    assert p["values"][2] == pytest.approx(15.0)


def test_median_ignores_a_single_outlier_day():
    # 3 日のうち 1 日だけビン 0 が 100 倍でも、中央値は平常日の値のまま（平均なら 34 倍になる）。
    def vol(di, b):
        if b == 0:
            return 1000.0 if di == 1 else 10.0
        return 1.0

    p = tv.session_offset_profile(build_df(DAYS[:3], vol))
    assert p["values"][0] == pytest.approx(150.0)


# --------------------------------------------------------------------------- #
# 因果性（当日を含まない）
# --------------------------------------------------------------------------- #
def test_until_excludes_its_own_session_day_and_everything_after():
    # 3 日目のビン 0 だけ極端に大きい。until を 3 日目の途中に置くと 3 日目は集計に入らない。
    def vol(di, b):
        if b == 0:
            return 10_000.0 if di == 2 else 10.0
        return 1.0

    df = build_df(DAYS[:3], vol)
    p = tv.session_offset_profile(df, until=DAYS[2] + 3600)
    assert p["day_count"] == 2                       # 3 日目は当日＝除外
    assert p["values"][0] == pytest.approx(150.0)    # 当日の巨大値に汚染されない


def test_until_at_the_exact_session_boundary_excludes_that_session():
    def vol(di, b):
        return 10_000.0 if di == 2 else 10.0

    p = tv.session_offset_profile(build_df(DAYS[:3], vol), until=DAYS[2])
    assert p["day_count"] == 2


# --------------------------------------------------------------------------- #
# 窓長・欠損ビン・DST
# --------------------------------------------------------------------------- #
def test_only_the_last_n_sessions_are_used():
    def vol(di, b):
        return 10.0 if di >= 3 else 1000.0   # 古い 3 日は大きい

    p = tv.session_offset_profile(build_df(DAYS[:5], vol), sessions=2)
    assert p["day_count"] == 2
    assert p["values"][0] == pytest.approx(150.0)     # 直近 2 日（10/分）のみ


def test_bins_absent_on_a_short_session_are_excluded_from_the_median():
    # 3 日目だけ 1 時間で終わる短縮セッション。末尾ビンの母集団は 2 日分のみ。
    def vol(_di, _b):
        return 10.0

    df = build_df(DAYS[:3], vol, minutes_per_day=[24 * 60, 24 * 60, 60])
    p = tv.session_offset_profile(df)
    assert p["values"][0] == pytest.approx(150.0)
    assert p["values"][80] == pytest.approx(150.0)     # 短縮日に無いビンも 0 で薄まらない


def test_dst_session_of_25_hours_produces_more_bins_than_a_normal_day():
    # 2026-11-01 の米 DST 終了を含むセッション（25 時間）。start+86400 前提だと破綻する。
    dst = session_day_start(utc(2026, 11, 1, 12))
    assert next_session_day_start(dst) - dst == 25 * 3600
    df = build_df([dst], lambda *_: 10.0, minutes_per_day=[25 * 60])
    p = tv.session_offset_profile(df)
    assert max(p["values"]) == 25 * 3600 // tv.BIN_SEC - 1   # 100 ビン（24h なら 96）


def test_non_finite_volume_rows_are_dropped():
    # NaN 行は「0 として加算」でも「ビン全体を NaN に汚染」でもなく、その行だけ落とす。
    df = build_df(DAYS[:3], lambda *_: 10.0)
    df.iloc[0, 0] = np.nan
    p = tv.session_offset_profile(df)
    assert p["day_count"] == 3
    assert np.isfinite(p["values"][0])
    assert p["values"][0] == pytest.approx(150.0)   # 中央値は無傷の 2 日側（NaN 日は 140）


def test_empty_or_volumeless_input_yields_no_bins():
    assert tv.session_offset_profile(None)["values"] == {}
    empty = pd.DataFrame({"volume": []}, index=pd.to_datetime([], unit="s"))
    assert tv.session_offset_profile(empty)["values"] == {}
    no_vol = pd.DataFrame({"close": [1.0]}, index=pd.to_datetime([DAY0], unit="s"))
    assert tv.session_offset_profile(no_vol)["values"] == {}


# --------------------------------------------------------------------------- #
# 帯の圧縮
# --------------------------------------------------------------------------- #
def test_consecutive_high_bins_merge_into_one_band():
    values = {0: 1.0, 1: 9.0, 2: 9.0, 3: 9.0, 4: 1.0, 5: 9.0}
    bands = tv.concentration_bands(values, pct=75)
    assert bands == [
        {"startOff": 1 * 900, "endOff": 4 * 900},
        {"startOff": 5 * 900, "endOff": 6 * 900},
    ]


def test_threshold_is_relative_so_a_scaled_profile_gives_identical_bands():
    # 市場全体の活況度が 2 倍になっても帯は変わらない（絶対閾値では破綻する＝実測 3）。
    values = {0: 1.0, 1: 9.0, 2: 9.0, 3: 2.0}
    assert tv.concentration_bands(values) == tv.concentration_bands(
        {k: v * 2.0 for k, v in values.items()}
    )


def test_empty_profile_yields_no_bands():
    assert tv.concentration_bands({}) == []


# --------------------------------------------------------------------------- #
# パラメータのクランプ（黙って精度が落ちるのを防ぐ）
# --------------------------------------------------------------------------- #
def test_regression_pins_the_measured_bands_on_real_jp225_tick_data():
    """実データ（jp225_tick 1m 原子）＋ until 固定で、計画時の独立集計と完全一致することを固定する。

    独立集計（2026-08-01・pandas で本モジュールを使わずに算出）と同一の帯:
        +03:00〜+05:30（東京寄り付き）/ +06:30〜+07:15 / +08:30〜+08:45 / +16:30〜+18:45（NY 序盤）
    過去日データは不変（実測済み）のため、until 固定なら CSV が伸びても値は変わらない。
    """
    from marketdata.paths import DATA_DIR

    csv = DATA_DIR / "jp225_tick_m1.csv"
    if not csv.exists():
        pytest.skip(f"実データ未配置: {csv}")
    df = pd.read_csv(csv, parse_dates=["date"], index_col="date").tail(60 * 24 * 45)
    p = tv.session_offset_profile(df, until=utc(2026, 8, 1))
    assert p["day_count"] == 20
    assert tv.concentration_bands(p["values"], pct=75) == [
        {"startOff": 10800, "endOff": 19800},
        {"startOff": 23400, "endOff": 26100},
        {"startOff": 30600, "endOff": 31500},
        {"startOff": 59400, "endOff": 67500},
    ]


def test_sessions_and_pct_are_clamped():
    assert tv.clamp_sessions(None) == tv.DEFAULT_SESSIONS
    assert tv.clamp_sessions("x") == tv.DEFAULT_SESSIONS
    assert tv.clamp_sessions(1) == tv.MIN_SESSIONS
    assert tv.clamp_sessions(999) == tv.MAX_SESSIONS   # 1m tail 50,000 行の制約
    assert tv.clamp_pct(None) == float(tv.DEFAULT_PCT)
    assert tv.clamp_pct(10) == 50.0
    assert tv.clamp_pct(99) == 95.0
