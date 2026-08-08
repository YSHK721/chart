"""上位足投影（ISSUE-274）の契約を固定する。

投影が守る不変条件:
  1. 応答の time はチャート足のバー時刻の部分集合（時間軸へ C 外の時刻を混ぜない）。
  2. 右端はチャート末尾のバーまで届く（上位足ぶん欠けない）。
  3. 同一 H 期間内の C バーは同値＝階段（直線補間にならない）。
  4. 確定済みの H 期間には「その時点で確定していた」値を使い、進行中の期間だけ形成値を使う。
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


def _project(chart, points):
    out = project_series(_line(points), chart, "1h",
                         period_start_unix=_hourly)
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
    """同一 H 期間内の C バーは同値＝階段になる（D-2）。

    確定済み期間には「その時点で確定していた」値が入るため、段は 1 期間ぶん後ろへずれる:
      10:00 台 = 材料なし（10:00 足は未確定）／11:00 台 = 100.0／12:00 台 = 200.0。
    """
    chart = _chart(10 * HOUR, 36)          # 10:00 〜 12:55（12:00 期間が進行中）
    data = _project(chart, H_POINTS)
    second_hour = [p["value"] for p in data if 11 * HOUR <= p["time"] < 12 * HOUR]
    third_hour = [p["value"] for p in data if p["time"] >= 12 * HOUR]

    assert [p["value"] for p in data if p["time"] < 11 * HOUR] == [], "未確定期間に点が出ている"
    assert set(second_hour) == {100.0}
    assert set(third_hour) == {200.0}
    assert len(second_hour) == len(third_hour) == 12


def test_closed_period_uses_the_value_that_was_confirmed_then():
    """確定済みの H 期間には「その時点で確定していた」値を使う（look-ahead 遮断・D-5）。

    10:00 台の C バーは 10:00 の足がまだ閉じていない時間帯である。10:00 足の最終値は
    その時点では知り得ないため使わない（材料が無いので点を出さない）。
    """
    chart = _chart(10 * HOUR, 24)          # 10:00〜11:55。11:00 期間が最後＝進行中。
    data = _project(chart, H_POINTS)
    by_time = {p["time"]: p["value"] for p in data}

    assert all(t >= 11 * HOUR for t in by_time), "10:00 台に未確定の値が漏れている"


def test_in_progress_period_uses_the_forming_value_so_the_right_edge_moves():
    """進行中の H 期間だけは形成中の値を使う（ティック粒度で右端が動く・D-4）。"""
    chart = _chart(10 * HOUR, 24)          # 最後の C バーは 11:55＝11:00 期間（進行中）
    data = _project(chart, H_POINTS)
    by_time = {p["time"]: p["value"] for p in data}

    assert by_time[11 * HOUR] == 200.0, "進行中期間で形成値が使われていない"
    assert by_time[max(by_time)] == 200.0, "右端が形成値になっていない"


def test_a_closed_last_period_does_not_leak_its_final_value():
    """最後の H 期間が閉じている（C が次の期間まで伸びている）なら、形成扱いしない。"""
    chart = _chart(10 * HOUR, 36)          # 10:00〜12:55。12:00 期間の C バーが在る＝11:00 は確定済み
    data = _project(chart, H_POINTS)
    by_time = {p["time"]: p["value"] for p in data}

    # 11:00 台の C バーには 10:00 足（11:00 に確定）の値が出る。11:00 足の最終値は使わない。
    assert by_time[11 * HOUR] == 100.0
    # 12:00 以降は 11:00 足が確定済みなのでその値になる。
    assert by_time[12 * HOUR] == 200.0


def test_bars_before_the_first_source_point_get_no_point():
    """材料不足の C バーには点を出さない（NaN を描かない）。"""
    chart = _chart(9 * HOUR, 36)           # 09:00 開始＝最初の 1 時間は材料が無い
    data = _project(chart, H_POINTS)
    assert data, "確定した H 足が出て以降には点が出る"
    # 10:00 足が確定するのは 11:00。それ以前の C バーには出せる値が無い。
    assert min(p["time"] for p in data) == 11 * HOUR


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
    #   末尾に day3 期間（day2 21:00 以降）のバーを 1 本足し、day2 期間を「確定済み」にする
    #   （進行中期間だけは形成値を使う規約のため、確定判定を分けて検証する）。
    bar_times = [day1 + 22 * 3600, day2 - 3600, day2, day2 + 3600, day2 + 10 * 3600,
                 day2 + 22 * 3600]
    idx = pd.to_datetime(bar_times, unit="s")
    chart = pd.DataFrame({"close": [0.0] * len(bar_times)}, index=idx)

    out = project_series([{"name": "MA", "kind": "line", "data": points}], chart, "1D",
                         period_start_unix=_calendar_day)
    values = {p["time"]: p["value"] for p in out[0]["data"]}
    # day2 期間（確定済み）の C バーは、その時点で確定していた day1 の値でなければならない。
    #   ラベル比較の実装だと day2 のラベル（00:00）以降だけ 200.0 になり、ここで落ちる。
    in_day2 = [v for t, v in values.items() if t < day2 + 21 * 3600]
    assert set(in_day2) == {100.0}, (
        "ラベル時刻ではなく期間始端で判定していない（未確定の上位足の値が過去へ漏れている）"
    )
    # day3 期間（進行中）には day2 の確定値が出る。
    assert values[day2 + 22 * 3600] == 200.0


def test_calendar_forming_bar_applies_to_the_whole_period():
    """進行中の暦足期間では、形成中の値がその期間の C バー全体へ適用される（右端が動く）。"""
    day1, day2 = 10 * DAY, 11 * DAY
    points = [{"time": day1, "value": 100.0}, {"time": day2, "value": 200.0}]
    bar_times = [day1 + 22 * 3600, day2 - 3600, day2, day2 + 3600]
    chart = pd.DataFrame({"close": [0.0] * len(bar_times)},
                         index=pd.to_datetime(bar_times, unit="s"))
    out = project_series([{"name": "MA", "kind": "line", "data": points}], chart, "1D",
                         period_start_unix=_calendar_day)
    # 4 本すべてが day2 の期間（day1 21:00 以降）に属するため、全点が形成中の値になる。
    assert {p["value"] for p in out[0]["data"]} == {200.0}


def test_projection_does_not_mutate_the_input():
    """入力系列・入力点を破壊しない（呼び出し元が同じ series を再利用できる）。"""
    chart = _chart(10 * HOUR, 24)
    points = [dict(p) for p in H_POINTS]
    series = _line(points)
    project_series(series, chart, "1h", period_start_unix=_hourly)
    assert series[0]["data"] == H_POINTS


# --- ISSUE-278 #2: 時系列 kind の集合は kind 定義側が唯一源 ------------------------- #
def test_level_dash_is_projected_like_line():
    """``level_dash``（cvfe の既定表示）も投影対象。

    payload 形状は line と同一（``{time, value}``・fake_chart.create_level_dash）なのに、投影の
    対象表がこのモジュールへ写されていたため漏れ、上位足 H の時刻がそのまま C の時間軸へ
    混入していた（ISSUE-274 が消した現象の再現）。
    """
    chart = _chart(10 * HOUR, 24)
    series = [{"name": "cvfe", "kind": "level_dash", "data": [dict(p) for p in H_POINTS]}]

    out = project_series(series, chart, "1h", period_start_unix=_hourly)

    times = [p["time"] for p in out[0]["data"]]
    chart_times = [int(t.timestamp()) for t in chart.index]
    assert times, "level_dash が投影されていない（H の時刻が素通しされる）"
    assert set(times) <= set(chart_times), "C に存在しない時刻が時間軸へ混入している"


def test_timeseries_kinds_has_single_source():
    """末尾切り・投影の対象表が kind の定義側と同一実体であること（写しを作らない）。"""
    from adapter.compute.fake_chart import TIMESERIES_KINDS
    from adapter.compute.latest_dispatch import _TRIMMABLE_KINDS
    from adapter.compute.mtf_projection import _PROJECTABLE_KINDS

    assert _TRIMMABLE_KINDS is TIMESERIES_KINDS
    assert _PROJECTABLE_KINDS is TIMESERIES_KINDS


def test_projected_series_declare_that_they_are_stepped():
    """投影後の系列は階段関数であることを宣言する（描画側が形を推測しない・ISSUE-289）。

    直線補間で描くと段の境界が斜線になり、「上位足の期間の途中で値が動いている」ように
    見える（実測: 1h チャート × 1D 計算で 2 時間かけて 703 下降する斜線）。
    """
    chart = _chart(10 * HOUR, 36)
    out = project_series(_line(H_POINTS), chart, "1h", period_start_unix=_hourly)

    assert out[0]["stepped"] is True


def test_untouched_series_do_not_get_the_stepped_hint():
    """投影しない系列（data を持たない等）へはヒントを足さない（従来ボディと同一）。"""
    chart = _chart(10 * HOUR, 24)
    level = {"name": "level", "kind": "horizontal_line", "price": 123.0}

    out = project_series([level], chart, "1h", period_start_unix=_hourly)

    assert out == [level]
