"""系列 JSON の末尾 K 点を組む **唯一の実装**（ISSUE-273）。

規約（内部設計 §6.3.2）: 末尾から遡って NaN でない値を最大 k 点集め、``{"time": UNIX 秒,
"value": float}`` の昇順 list にする。指標 src 側の ``_emit``（dropna → time/value 点）と同一。

なぜ 1 箇所に置くか:
    同じ規約が 5 箇所（btlm_trail / marod / tickvol / profit_rsi の ``_tail_points`` と
    moving_averages のインライン）に独立実装され、**既に時刻正規化の規約が 2 通りに分岐していた**:

      - btlm_trail / marod / tickvol / moving_averages … prepare 時に
        ``stamps.astype("datetime64[s]").astype("int64")`` で int 化し、emit では ``int(times[i])``
      - profit_rsi … 生の ``resolve_times`` を保持し、emit で ``fake_chart._to_unix_seconds``

    どちらも UNIX 秒を返すが、**正規化の位置が違う**ため「どちらが規約か」がコードから読めず、
    規約変更（例: ミリ秒化）時に 5 箇所を同時に直す必要があった。正規化を本モジュール 1 箇所へ
    寄せ、呼び出し側は times をそのまま渡すだけにする。

時刻の受け入れ:
    整数系（``datetime64[s]`` から int 化済み・UNIX 秒）はそのまま。それ以外（datetime・
    ``pd.Timestamp``・文字列）は :func:`adapter.compute.fake_chart.to_unix_seconds` で変換する。
    どちらの呼び出し規約でも同じ値になるため、既存 5 経路の出力は不変。
"""

from __future__ import annotations

import numbers
from typing import Any

from adapter.compute.fake_chart import to_unix_seconds


def _unix_seconds(value: Any) -> int:
    """時刻値を UNIX 秒へ正規化する（**正規化はここ 1 箇所**）。"""
    # numpy の整数も numbers.Integral を満たす（bool は除外＝時刻ではない）。
    if isinstance(value, numbers.Integral) and not isinstance(value, bool):
        return int(value)
    return to_unix_seconds(value)


def tail_points(
    confirmed: Any, last: float, times: Any, n: int, k: int
) -> "list[dict[str, Any]]":
    """末尾から k 点（NaN は出さない）を系列 JSON の data 形式で返す。

    バー ``i`` の値は ``i < n-1`` なら確定配列 ``confirmed[i]``、``i == n-1`` なら形成中バーの
    値 ``last``。NaN（warm-up・未計算）は点を出さない（``v == v`` で判定＝src 側 ``_emit`` と同一）。
    """
    points: "list[dict[str, Any]]" = []
    i = n - 1
    while i >= 0 and len(points) < k:
        v = last if i == n - 1 else confirmed[i]
        if v == v:  # NaN 除外（NaN != NaN）
            points.append({"time": _unix_seconds(times[i]), "value": float(v)})
        i -= 1
    points.reverse()
    return points


def tail_points_offset(
    buffer: Any, times: Any, *, i_high: int, i_low: int, offset: int, k: int
) -> "list[dict[str, Any]]":
    """``values[i]`` を ``times[i+offset]`` へ置く emit 規約（moving_averages 系）の末尾 K 点。

    走査範囲 ``[i_low, i_high]`` は呼び出し側が決める（warm-up マスク・offset の範囲外を含むため）。
    時刻の正規化は :func:`tail_points` と同一（本モジュールに閉じる）。
    """
    points: "list[dict[str, Any]]" = []
    i = i_high
    while i >= i_low and len(points) < k:
        v = buffer[i]
        if v == v:  # NaN 除外
            points.append({"time": _unix_seconds(times[i + offset]), "value": float(v)})
        i -= 1
    points.reverse()
    return points
