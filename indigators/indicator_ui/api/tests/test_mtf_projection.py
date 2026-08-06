"""上位足投影（ISSUE-274）の契約を固定する。

投影が守る不変条件:
  1. 応答の time はチャート足のバー時刻の部分集合（時間軸へ C 外の時刻を混ぜない）。
  2. 右端はチャート末尾のバーまで届く（上位足ぶん欠けない）。
  3. 同一 H 期間内の C バーは同値＝階段（直線補間にならない）。
  4. ``wait_for_close=True`` は確定済み H バーだけを使う（未来情報を混ぜない）。
  5. ``data`` を持たない系列（horizontal_line）は素通し。
  6. 点の付随情報（histogram の per-bar color 等）は温存する。

期間の所属判定は注入された ``period_start_unix`` に委ねる（本テストは 1 時間固定周期の fake を
使い、投影ロジックそのものを暦足の実装から独立に検証する）。実データの暦足境界は
``marketdata.tf_meta.period_start_unix`` 側の責務。
"""

from __future__ import annotations

import pandas as pd
import pytest

from adapter.compute.mtf_projection import project_series

HOUR = 3600
MINUTE = 300     # 5 分足を C とする


def _hourly(now_unix: int, tf: str) -> int:
    """1 時間固定周期の period_start_unix（テスト用 fake）。"""
    return now_unix - now_unix % HOUR


def _chart(start: int, n: int) -> pd.DataFrame:
    """5 分足のチャートフレーム（インデックスだけを使う）。"""
    idx = pd.to_datetime([start + i * MINUTE for i in range(n)], unit="s")
    return pd.DataFrame({"close": [0.0] * n}, index=idx)


def _line(points):
    return [{"name": "MA", "kind": "line", "data": points}]


# 10:00 と 11:00 の 2 本の H バー。
H_POINTS = [
    {"time": 10 * HOUR, "value": 100.0},
    {"time": 11 * HOUR, "value": 200.0},
]


def _project(chart, points, *, wait_for_close=False):
    out = project_series(_line(points), chart, "1h",
                         wait_for_close=wait_for_close, period_start_unix=_hourly)
    return out[0]["data"]


def test_times_are_subset_of_chart_bars_and_reach_the_last_bar():
    """時刻はチャートバーの部分集合で、右端はチャート末尾に一致する（D-1 / D-3）。"""
    chart = _chart(10 * HOUR, 24)          # 10:00 〜 11:55 の 5 分足 24 本
    chart_times = [int(ts.timestamp()) for ts in chart.index]
    data = _project(chart, H_POINTS)
    times = [p["time"] for p in data]
    assert set(times) <= set(chart_times)
    assert times[-1] == chart_times[-1]


def test_values_form_a_staircase_not_an_interpolation():
    """同一 H 期間内の C バーは同値＝階段になる（D-2）。"""
    chart = _chart(10 * HOUR, 24)
    data = _project(chart, H_POINTS)
    first_hour = [p["value"] for p in data if p["time"] < 11 * HOUR]
    second_hour = [p["value"] for p in data if p["time"] >= 11 * HOUR]
    assert set(first_hour) == {100.0}
    assert set(second_hour) == {200.0}
    assert len(first_hour) == len(second_hour) == 12


def test_wait_for_close_never_shows_a_bar_before_it_is_confirmed():
    """確定待ちでは、形成中 H バーの値がその期間の C バーへ出ない（D-5）。"""
    chart = _chart(10 * HOUR, 24)
    data = _project(chart, H_POINTS, wait_for_close=True)
    by_time = {p["time"]: p["value"] for p in data}
    # 10:00 台は「10:00 の足が確定していない」ため材料が無く点を出さない。
    assert all(t >= 11 * HOUR for t in by_time)
    # 11:00 台には 10:00 の足（11:00 に確定済み）の値が出る。11:00 の足の値は出ない。
    assert set(by_time.values()) == {100.0}


def test_without_wait_for_close_the_forming_bar_is_used():
    """確定を待たない既定では、形成中 H バーの現在値をその期間へ適用する。"""
    chart = _chart(10 * HOUR, 24)
    data = _project(chart, H_POINTS)
    by_time = {p["time"]: p["value"] for p in data}
    assert by_time[10 * HOUR] == 100.0
    assert by_time[11 * HOUR] == 200.0


def test_bars_before_the_first_source_point_get_no_point():
    """材料不足の C バーには点を出さない（NaN を描かない）。"""
    chart = _chart(9 * HOUR, 24)           # 09:00 開始＝最初の 1 時間は材料が無い
    data = _project(chart, H_POINTS)
    assert data, "10:00 以降には点が出る"
    assert min(p["time"] for p in data) == 10 * HOUR


def test_series_without_data_pass_through_untouched():
    """horizontal_line（価格軸分布・data を持たない）は素通しする。"""
    chart = _chart(10 * HOUR, 24)
    level = {"name": "level", "kind": "horizontal_line", "price": 123.0}
    out = project_series([level], chart, "1h", period_start_unix=_hourly)
    assert out == [level]


def test_per_point_extras_are_preserved():
    """histogram の per-bar color 等、点の付随情報は温存する。"""
    chart = _chart(10 * HOUR, 12)
    points = [{"time": 10 * HOUR, "value": 5.0, "color": "#ff0000"}]
    out = project_series([{"name": "h", "kind": "histogram", "data": points}],
                         chart, "1h", period_start_unix=_hourly)
    assert all(p["color"] == "#ff0000" for p in out[0]["data"])
    assert len(out[0]["data"]) == 12


def test_empty_chart_frame_returns_series_unchanged():
    """チャートフレームが空なら系列をそのまま返す（投影先が無い）。"""
    empty = _chart(10 * HOUR, 0)
    series = _line(H_POINTS)
    assert project_series(series, empty, "1h", period_start_unix=_hourly) is series


# --------------------------------------------------------------------------- #
# 暦足（ラベル ≠ 期間始端 ≠ 確定時刻）— D-5 の再発防止
# --------------------------------------------------------------------------- #
# 実測（jp225_tick）: 1D のラベルは 00:00 UTC だが、その足の実区間は前日 21:00 〜 当日 21:00。
#   ラベルは期間の始端より 3 時間後、確定時刻より 21 時間前にある。したがって「ラベル時刻の
#   大小比較」で前方保持すると、確定していない足の値を 21 時間ぶん過去へ描く（未来情報の混入）。
DAY = 86400
SESSION_OFFSET = 3 * 3600     # 期間始端 = ラベル日の 00:00 から 3 時間さかのぼった前日 21:00


def _calendar_day(now_unix: int, tf: str) -> int:
    """1D 相当の period_start_unix（ラベル日の前日 21:00 が期間始端）。"""
    return (now_unix + SESSION_OFFSET) // DAY * DAY - SESSION_OFFSET


def test_calendar_label_is_not_used_as_the_boundary():
    """暦足では期間始端で判定する（ラベルで判定すると未来情報が混入する・D-5）。"""
    # ラベル 2 日ぶん（00:00 / 00:00）。それぞれの実区間は [前日21:00, 当日21:00)。
    day1, day2 = 10 * DAY, 11 * DAY
    points = [{"time": day1, "value": 100.0}, {"time": day2, "value": 200.0}]
    # day2 の区間内（= day1 21:00 以降）の C バーを並べる。ラベル day2(00:00) の前後を含む。
    bar_times = [day1 + 22 * 3600, day2 - 3600, day2, day2 + 3600, day2 + 10 * 3600]
    idx = pd.to_datetime(bar_times, unit="s")
    chart = pd.DataFrame({"close": [0.0] * len(bar_times)}, index=idx)

    out = project_series([{"name": "MA", "kind": "line", "data": points}], chart, "1D",
                         wait_for_close=True, period_start_unix=_calendar_day)
    values = {p["time"]: p["value"] for p in out[0]["data"]}
    # day2 の区間は「day2 の足が未確定」なので、全 C バーが day1 の確定値でなければならない。
    #   ラベル比較の実装だと day2 のラベル（00:00）以降だけ 200.0 になり、ここで落ちる。
    assert set(values.values()) == {100.0}, (
        "ラベル時刻ではなく期間始端で判定していない（未確定の上位足の値が過去へ漏れている）"
    )


def test_calendar_forming_bar_applies_to_the_whole_period():
    """確定を待たない場合、形成中の暦足の値はその期間の C バー全体へ適用される。"""
    day1, day2 = 10 * DAY, 11 * DAY
    points = [{"time": day1, "value": 100.0}, {"time": day2, "value": 200.0}]
    bar_times = [day1 + 22 * 3600, day2 - 3600, day2, day2 + 3600]
    chart = pd.DataFrame({"close": [0.0] * len(bar_times)},
                         index=pd.to_datetime(bar_times, unit="s"))
    out = project_series([{"name": "MA", "kind": "line", "data": points}], chart, "1D",
                         period_start_unix=_calendar_day)
    # 4 本すべてが day2 の期間（day1 21:00 以降）に属するため、全点が形成中の値になる。
    assert {p["value"] for p in out[0]["data"]} == {200.0}


@pytest.mark.parametrize("wait", [False, True])
def test_projection_does_not_mutate_the_input(wait):
    """入力系列・入力点を破壊しない（呼び出し元が同じ series を再利用できる）。"""
    chart = _chart(10 * HOUR, 24)
    points = [dict(p) for p in H_POINTS]
    series = _line(points)
    project_series(series, chart, "1h", wait_for_close=wait, period_start_unix=_hourly)
    assert series[0]["data"] == H_POINTS
