"""上位足（MTF）指標の**因果系列**（ISSUE-294 / 295）。

チャート足 C の各バー τ に載せる値を、次の 1 つの定義へ統一する（ライブ／リプレイ共通）:

    value(τ) = 指標( [τ の期間より前の確定 H 足] + [τ の期間の C 足を τ まで畳んだ H 足] )

従来（ISSUE-274 の投影・:mod:`adapter.compute.mtf_projection`）は「いまの H 系列を計算し、
期間単位で C へ写す」規約だった。その時点の**見え方**としては正しいが、点の意味が
「その期間の値」であるため、**過去のバーの点にそのバーより後の情報が載る**（各日は当日の
最終値で塗り潰される）。本モジュールの規約では点の意味が「τ 時点で計算できた値」に揃い、

    - 各点は自分より後のデータに依存しない＝**時刻不変**（後から塗り替える必要がない）
    - 進行中の期間でも、C バーが進むごとに値が伸びる（ティック粒度の追従は従来どおり）

となる。依頼（2026-08-08）「過去に確定したラインを更新するな／過去のデータも固定しろ／
ライブモードも同期しろ」に対する単一の実装であり、ライブとリプレイはこの 1 か所を共有する。

技術隔離: 本モジュールは pandas を import しない（plain dict の bar 列だけを扱う）。
DataFrame との往復は :mod:`adapter.compute.mtf_causal_frames` が担う。
"""

from __future__ import annotations

from typing import Any, Callable

from adapter.compute.fake_chart import TIMESERIES_KINDS

#: 因果系列を作る対象 kind（時系列 data を持つもの）。唯一源は fake_chart 側の定義。
_SERIES_KINDS = TIMESERIES_KINDS


def fold_bars(bars: "list[dict]", *, time: int) -> dict:
    """バー列を 1 本へ畳む（open=先頭 open・high=最大・low=最小・close=末尾 close）。"""
    return {
        "time": int(time),
        "open": float(bars[0]["open"]),
        "high": max(float(b["high"]) for b in bars),
        "low": min(float(b["low"]) for b in bars),
        "close": float(bars[-1]["close"]),
        "volume": float(sum(float(b.get("volume") or 0.0) for b in bars)),
    }


def group_by_period(
    bars: "list[dict]", *, compute_tf: str, bar_time_unix: Callable[[str, int], int]
) -> "list[tuple[int, list[dict]]]":
    """C 足を H の期間（ラベル）ごとの連続群へ分ける。``[(label, bars), ...]`` を返す。"""
    groups: "list[tuple[int, list[dict]]]" = []
    for b in bars:
        label = int(bar_time_unix(compute_tf, int(b["time"])))
        if groups and groups[-1][0] == label:
            groups[-1][1].append(b)
        else:
            groups.append((label, [b]))
    return groups


def causal_mtf_series(
    *,
    chart_bars: "list[dict]",
    source_bars: "list[dict]",
    compute_tf: str,
    bar_time_unix: Callable[[str, int], int],
    latest_seq: Callable[["list[dict]", "list[list[dict]]"], "list[list[dict]]"],
    window_bars: "list[dict] | None" = None,
) -> "list[dict]":
    """C の各バーへ「そのバーの時点で計算できた H の値」を載せた系列を返す。

    Args:
        chart_bars: 畳みに使う C 足（**出力窓より前**の同一期間ぶんを含む昇順の列）。
        source_bars: H 足（保存済み。進行中期間の足は期間全体の OHLC＝未来を含むため使わない）。
        compute_tf: 計算足 H の時間足コード。
        bar_time_unix: ``(tf, unix) -> label``。期間ラベルの唯一源（``marketdata.tf_meta``）。
        latest_seq: ``(prefix_bars, tails) -> [series, ...]``。確定プレフィクスを 1 回だけ
            計算資源へ載せ、末尾差分ごとに latest 計算する実体（ISSUE-233 の最適化経路）。
        window_bars: 出力対象の C 足（既定は ``chart_bars`` 全部）。畳みには使うが出力には
            含めない先頭側を落とすために使う。

    Returns:
        ``[{name, kind, data:[{time, value...}], stepped: True, ...}]``。期間境界の跳ねを
        斜線で結ばないため ``stepped`` を立てる（ISSUE-289）。
    """
    if not chart_bars or not source_bars:
        return []
    keep = {int(b["time"]) for b in (window_bars if window_bars is not None else chart_bars)}
    out: "dict[Any, dict]" = {}
    order: "list[Any]" = []
    for label, part in group_by_period(
            chart_bars, compute_tf=compute_tf, bar_time_unix=bar_time_unix):
        confirmed = [b for b in source_bars if int(b["time"]) < label]
        tails: "list[list[dict]]" = []
        times: "list[int]" = []
        acc: "dict | None" = None
        for b in part:
            acc = fold_bars([acc, b] if acc else [b], time=label)
            tails.append([dict(acc)])
            times.append(int(b["time"]))
        steps = latest_seq(confirmed, tails)
        for t, series in zip(times, steps or []):
            if t not in keep:
                continue
            for p in series or []:
                data = p.get("data") or []
                if p.get("kind") not in _SERIES_KINDS or not data:
                    continue
                name = p.get("name")
                if name not in out:
                    out[name] = {**p, "data": [], "stepped": True}
                    order.append(name)
                out[name]["data"].append({**data[-1], "time": t})
    return [out[name] for name in order]
