"""因果 MTF 系列の DataFrame 境界（ISSUE-295）。

:mod:`adapter.compute.mtf_causal` は pandas を持たない（plain bar 列だけを扱う）。本モジュールは
その入出力と DataFrame の往復、および「確定プレフィクスを期間ごとに 1 回だけ DataFrame 化し、
末尾差分だけを結合して latest 計算する」費用最適化（ISSUE-233 と同型）を担う。

ライブ core（usecase.compute_indicators）とリプレイ core（causal_compute_gateway）は、
本モジュール 1 か所を共有する＝規約も費用特性も同一になる。
"""

from __future__ import annotations

from typing import Any, Callable

import pandas as pd

from adapter.compute.fake_chart import to_unix_seconds
from adapter.compute.mtf_causal import causal_mtf_series


def bars_from_frame(df: Any) -> "list[dict]":
    """OHLC DataFrame → plain bar 列（time＝UNIX 秒・列名は小文字）。"""
    if df is None or len(df) == 0:
        return []
    times = [to_unix_seconds(idx) for idx in df.index]
    keys = [str(c).lower() for c in df.columns]
    columns = [df[c].to_numpy(dtype="float64").tolist() for c in df.columns]
    return [dict(zip(["time", *keys], row)) for row in zip(times, *columns)]


def frame_from_bars(bars: "list[dict]") -> "pd.DataFrame":
    """plain bar 列 → OHLC DataFrame（UTC 秒境界の DatetimeIndex）。"""
    times = [int(b["time"]) for b in bars]
    cols: "list[str]" = []
    for b in bars:
        for k in b:
            if k != "time" and k not in cols:
                cols.append(k)
    return pd.DataFrame({c: [b.get(c) for b in bars] for c in cols},
                        index=pd.to_datetime(times, unit="s"))


def latest_seq_over(compute_latest: Callable[["pd.DataFrame"], "list[dict]"]):
    """``(prefix_bars, tails) -> [series, ...]`` を作る。

    確定プレフィクスの DataFrame 化は **群につき 1 回**だけ行い、時点ごとには末尾差分
    （1 本）だけを結合する（時点ごとに窓全体を組み直すと、指標計算そのものより変換が重い）。
    """

    def _run(prefix_bars: "list[dict]", tails: "list[list[dict]]") -> "list[list[dict]]":
        prefix_df = frame_from_bars(prefix_bars) if prefix_bars else None
        out: "list[list[dict]]" = []
        for tail in tails:
            tail_df = frame_from_bars(tail)
            if prefix_df is None or len(prefix_df) == 0:
                df = tail_df
            else:
                df = pd.concat([prefix_df, tail_df.reindex(columns=prefix_df.columns)])
            out.append(compute_latest(df))
        return out

    return _run


def causal_mtf_frames(
    *,
    df_chart: Any,
    df_source: Any,
    compute_tf: str,
    bar_time_unix: Callable[[str, int], int],
    compute_latest: Callable[["pd.DataFrame"], "list[dict]"],
    fold_from: Any = None,
) -> "list[dict]":
    """DataFrame で受けて因果 MTF 系列を返す（規約の実体は ``mtf_causal``）。

    Args:
        df_chart: 出力対象のチャート足 C（limit 適用後）。
        df_source: 計算足 H の保存済みバー。
        fold_from: 畳みに使う C 足の全体（``df_chart`` が期間の途中から始まる場合に、その
            期間の先頭から畳むために渡す）。None なら ``df_chart`` だけで畳む。
    """
    window = bars_from_frame(df_chart)
    if not window:
        return []
    source = bars_from_frame(df_source)
    if not source:
        return []
    chart_all = bars_from_frame(fold_from) if fold_from is not None else window
    first = int(window[0]["time"])
    label0 = int(bar_time_unix(compute_tf, first))
    # 出力窓の先頭が属する期間は、窓より前の C 足も畳みに要る（途中から畳むと値がずれる）。
    head = [b for b in chart_all if int(b["time"]) < first
            and int(bar_time_unix(compute_tf, int(b["time"]))) == label0]
    return causal_mtf_series(
        chart_bars=[*head, *window],
        source_bars=source,
        compute_tf=compute_tf,
        bar_time_unix=bar_time_unix,
        latest_seq=latest_seq_over(compute_latest),
        window_bars=window,
    )
