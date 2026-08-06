"""上位時間足（MTF）指標のチャート時間軸への投影（ISSUE-274）。

指標を計算足 H（``computeTimeframe``）で計算し、その系列を **チャート足 C の時間軸へ写す**。
投影を通過した系列は「C の時間軸を持つ通常の系列」になるため、フロントの描画・スタイル・
スケール・足内追従の各経路は一切改変せずに再利用できる。

なぜ投影が要るのか（ISSUE-274 の実測）:
    H の系列をそのままチャートへ渡すと、時間軸に C に存在しない時刻が union され
    （ローソク不在域の発生・価格軸の汚染）、点間は直線補間され（階段にならない）、
    右端は最大 1 上位足ぶん欠ける。さらに暦足ではラベルが確定時刻の **21 時間前**に
    位置するため、まだ知り得ない確定値が過去へ描かれる（未来情報の混入）。
    これら 4 つは独立の不具合ではなく「H の時間軸のまま渡している」単一原因の現れであり、
    投影で原因そのものが消える。

規約（決定論的定義）:
    - 各バーの所属期間は :func:`marketdata.tf_meta.period_start_unix` が唯一源。
      **ラベル時刻で比較してはならない**（暦足ではラベル ≠ 期間始端 ≠ 確定時刻）。
    - ``wait_for_close=True``  : C バー ``t`` の値 ＝ ``t`` の属する H 期間より前に
      **確定した**最後の H バーの値。過去・現在とも不変（完全非リペイント）。
    - ``wait_for_close=False`` : 形成中の H バーの現在値も使う（``t`` の属する H 期間の値）。
      可変範囲は形成中の 1 期間ぶんに限られ、確定後は不変。
    - 材料不足（``t`` がどの H バーよりも前）の C バーには点を出さない（NaN を描かない）。

対象 kind は時系列 ``data`` を持つ ``line`` / ``histogram`` のみ。``horizontal_line``
（価格軸分布・``data`` を持たない）は :mod:`adapter.compute.latest_dispatch` の末尾切りと
同じ理由で触らない。
"""

from __future__ import annotations

from typing import Any

from adapter.compute.fake_chart import TIMESERIES_KINDS, to_unix_seconds

# 投影対象 kind は kind の定義側（fake_chart.TIMESERIES_KINDS）が唯一源（ISSUE-278 #2）。
#   写しを置いていた結果 `level_dash`（cvfe の既定表示）が投影から漏れ、上位足 H の時刻が
#   そのままチャート足 C の時間軸へ混入していた（ISSUE-274 が消した現象の再現）。
_PROJECTABLE_KINDS = TIMESERIES_KINDS


def _chart_bar_times(df_chart: Any) -> list[int]:
    """チャート足 DataFrame のインデックスを UNIX 秒の昇順リストへ変換する。

    ``marketdata.dataset.load_candles`` と同じ変換（``to_unix_seconds``）を使う。これにより
    投影先の時刻集合は **/candles が返すローソクの時刻集合と定義上一致**する（時間軸へ
    C に存在しない時刻が混ざることが構成上あり得なくなる）。
    """
    return [to_unix_seconds(idx) for idx in df_chart.index]


def project_series(
    series: "list[dict[str, Any]]",
    df_chart: Any,
    compute_tf: str,
    *,
    wait_for_close: bool = False,
    period_start_unix: Any,
) -> "list[dict[str, Any]]":
    """H の時間軸の ``series`` を、チャート足の時間軸（``df_chart`` の各バー）へ投影する。

    Args:
        series: 指標計算の応答系列（``[{name, kind, data: [{time, value, ...}], ...}]``）。
        df_chart: チャート足 C の OHLC DataFrame（インデックス＝C のバー時刻）。
        compute_tf: 計算足 H の時間足コード。期間の所属判定に使う。
        wait_for_close: 形成中の H バーを使わない（＝確定足のみ）なら True。
        period_start_unix: ``(unix, tf) -> unix``。その時刻が属する期間の始端を返す唯一源
            （:func:`marketdata.tf_meta.period_start_unix`）。依存方向を内向きに保つため注入する。

    Returns:
        各系列の ``data`` を C のバー時刻へ写した新しい系列リスト（入力は変更しない）。
        ``data`` を持たない系列（``horizontal_line`` 等）はそのまま通す。
    """
    chart_times = _chart_bar_times(df_chart)
    if not chart_times:
        return series
    # C の各バーが属する H 期間の始端（1 バー 1 回だけ解決し、系列間で使い回す）。
    chart_period_starts = [period_start_unix(t, compute_tf) for t in chart_times]

    projected: "list[dict[str, Any]]" = []
    for s in series:
        points = s.get("data")
        if s.get("kind") not in _PROJECTABLE_KINDS or not points:
            projected.append(s)
            continue
        # H の各点が属する期間の始端。ラベル時刻そのものではない（暦足で両者は一致しない）。
        source_starts = [period_start_unix(int(p["time"]), compute_tf) for p in points]
        data: "list[dict[str, Any]]" = []
        cursor = -1
        for bar_time, bar_start in zip(chart_times, chart_period_starts):
            # 当該 C バーの時点で採用してよい最後の H バーまでカーソルを進める。
            #   確定待ち   : その H 期間が C バーの属する期間より前＝すでに確定している。
            #   確定待たず : 同一期間（形成中）まで採用する。
            while cursor + 1 < len(points) and (
                source_starts[cursor + 1] < bar_start
                or (not wait_for_close and source_starts[cursor + 1] <= bar_start)
            ):
                cursor += 1
            if cursor < 0:
                continue    # 材料不足（この C バーより前に採用できる H バーが無い）＝点を出さない。
            # per-point の付随情報（histogram のバー別 color 等）は温存し time だけ差し替える。
            data.append({**points[cursor], "time": bar_time})
        projected.append({**s, "data": data})
    return projected
