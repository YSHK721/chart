"""系列点列 → バー時刻列の整列（usecase 層・純関数・Phase 3 F-5）。

指標の計算結果は「時刻つきの点列」で返る。バックテストの指標レジストリは「バー index →
値」の配列を要する。その変換をここ 1 箇所に閉じる。

規則（無音の縮退を作らないための明示）:
    1. 返す値列はバー時刻列と同じ長さ・同じ順（index は呼び出し側が付ける）。
    2. **先頭 warmup の欠測は許容**する（値は NaN）。指標の未定義区間であり破損ではない。
    3. **有効区間（最初に点が現れたバー以降）の欠測は明示エラー**。ここを NaN で埋めると
       `PandasIndicatorRegistry._raise_if_invalid_nan` の `IndicatorNaNError`（＝指標系列の
       データ破損）に化け、「供給側が点を取りこぼした」という本当の原因が消える。
    4. **窓の内側でバー時刻列に無い時刻の点は時間軸不一致**として明示エラー。無音で捨てると、
       別の時間軸で計算された系列をそのまま供給してしまう。窓＝バー時刻列の
       ``[最小, 最大]`` の閉区間。
    5. **窓の外側の点は対象外**として落とす。案 ii が計算する窓（データセット先頭 →
       ``until_time``）は、供給・検定が対象にする窓と同じか広いことがあり、窓の外の点が
       返るのは正常である。ここを規則 4 と同一視すると実データでは整列に失敗し、
       **全指標が「供給できません」で選択不可になる**（＝検定が常に空振りする。
       2026-08-11 に実測して判明）。「窓の外」と「別の足のグリッド」は別事象なので、
       前者だけを対象外にし、後者の防御は保つ。
    6. 点が持つ**未定義値**（`SeriesPoint.value is None`）は NaN として運ぶ。破損判定を
       ここでしない（未定義点は指標側の正常出力であり、破損判定は指標レジストリの責務）。
       「点そのものが無い」（規則 2・3）とは別事象である。

CLEAN_ARCH: usecase 層。pandas / numpy を import しない（math のみ）。
"""
from __future__ import annotations

import math
from typing import Sequence

from simulator.sim_ui.usecase.indicator_models import SeriesAlignmentError, SeriesPoint


def align_series_to_bars(
    points: "Sequence[SeriesPoint]", bar_times: "Sequence[int]"
) -> "list[float]":
    """``bar_times`` と同じ長さ・同じ順の値列を返す（欠測 warmup は NaN）。"""
    known = set(int(t) for t in bar_times)
    if not known:
        # 窓が空＝対象 0 本。比較の可否は検定側が決める（規則 5）。
        return []
    window_start, window_end = min(known), max(known)

    by_time: "dict[int, float]" = {}
    outside_grid: "list[int]" = []
    for point in points:
        time = int(point.time)
        if time < window_start or time > window_end:
            continue                      # 規則 5: 窓の外＝対象外
        if time not in known:
            outside_grid.append(time)     # 規則 4: 窓の内側のグリッド不一致
            continue
        # 規則 6: 未定義値（None）は NaN として運ぶ（点はある）。
        by_time[time] = math.nan if point.value is None else float(point.value)
    if outside_grid:
        raise SeriesAlignmentError(
            "バー時刻列に無い時刻の点があります（時間軸不一致）: "
            + ", ".join(str(t) for t in sorted(outside_grid)[:5])
        )

    values: "list[float]" = []
    started = False
    for raw in bar_times:
        time = int(raw)
        if time in by_time:
            started = True
            values.append(by_time[time])
            continue
        if started:
            # 規則 3: 有効区間の欠測。
            raise SeriesAlignmentError(
                f"有効区間のバー時刻に対応する点がありません: {time}"
            )
        # 規則 2: 先頭 warmup。
        values.append(math.nan)
    return values
