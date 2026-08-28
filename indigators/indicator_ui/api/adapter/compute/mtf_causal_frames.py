"""因果 MTF 系列の DataFrame 境界（ISSUE-295）。

:mod:`adapter.compute.mtf_causal` は pandas を持たない（plain bar 列だけを扱う）。本モジュールは
その入出力と DataFrame の往復、および「確定プレフィクスを期間ごとに 1 回だけ DataFrame 化し、
末尾差分だけを結合して latest 計算する」費用最適化（ISSUE-233 と同型）を担う。

ライブ core（usecase.compute_indicators）とリプレイ core（causal_compute_gateway）は、
本モジュール 1 か所を共有する＝規約も費用特性も同一になる。
"""

from __future__ import annotations

from bisect import bisect_left
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

    さらに、呼び出しをまたいだ再構築も避ける。``causal_mtf_series`` は期間ラベルの昇順に
    本関数を呼び、``prefix_bars`` は**単調に伸びる同一の接頭辞**である（確定 H 足の
    ``time < label`` 部分）。にもかかわらず毎回ゼロから DataFrame 化していたため、変換量は
    「期間数 × 確定 H 足数」に比例していた（実測 C=1m / H=5m で 105 期間・累計 5,244,435 行）。
    直前の結果を保持し、伸びたぶんだけ結合する＝累計は確定 H 足数に比例する。

    健全性の確認は O(1) で行う: 保持している接頭辞の**末尾バーが今回の同位置と一致する**ときだけ
    差分結合を使い、一致しなければ従来どおり全体を組み直す（別系列・訂正・逆順の入力で
    誤った土台を使わない）。
    """
    cache: "dict[str, Any]" = {"n": 0, "df": None, "tail_sig": None}

    def _prefix_frame(prefix_bars: "list[dict]"):
        n = len(prefix_bars)
        if n == 0:
            return None
        cached_n, cached_df = cache["n"], cache["df"]
        if cached_df is not None and 0 < cached_n <= n \
                and cache["tail_sig"] == _bar_key(prefix_bars[cached_n - 1]):
            if n > cached_n:
                grown = pd.concat(
                    [cached_df, frame_from_bars(prefix_bars[cached_n:])
                     .reindex(columns=cached_df.columns)])
            else:
                grown = cached_df
        else:
            grown = frame_from_bars(prefix_bars)
        cache["n"], cache["df"] = n, grown
        cache["tail_sig"] = _bar_key(prefix_bars[n - 1])
        return grown

    def _run(prefix_bars: "list[dict]", tails: "list[list[dict]]") -> "list[list[dict]]":
        prefix_df = _prefix_frame(prefix_bars)
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


def _index_unix_seconds(index: Any):
    """``DatetimeIndex`` → UNIX 秒の int64 配列。解像度に依らず正しい値を返す（None は非対応）。

    ``index.asi8`` は **その索引自身の単位**（s / ms / us / ns）の生値であり、ns とは限らない。
    実データの索引は秒解像度で、ns と決め打つと値が 10^9 倍ずれる（2026-08-28 実測: 期待
    1339632000 に対し 1339632 が返り、先頭側 C 足が 1 本も選ばれなくなった）。単位を秒へ
    揃えてから取り出す。値は :func:`adapter.compute.fake_chart.to_unix_seconds` と一致する。
    """
    try:
        return index.as_unit("s").asi8
    except (AttributeError, TypeError, ValueError):
        return None


def _head_frame_of_first_period(
    fold_from: Any, *, first: int, label0: int,
    compute_tf: str, bar_time_unix: Callable[[str, int], int],
) -> Any:
    """``first`` の直前に連なる「同じ期間 ``label0``」の C 足を **DataFrame のまま**切り出す。

    畳みに要るのはこの区間だけである。``fold_from`` 全体を dict 化してから絞ると、要らない
    行まで作って捨てることになる（実測 C=1m / H=1M で 50,000 行を変換して 3 行しか使わない
    ケースがある＝ISSUE-450 E）。ここで先に行を絞り、変換は呼び出し側が切り出し後に行う。

    時刻・期間ラベルはどちらも時刻昇順に非減少なので、位置は二分探索で求まる
    （期間判定の呼び出しは O(log n)）。昇順でない入力では ``None`` を返し、呼び出し側の
    従来経路（全件走査）へ落ちる（安全側）。
    """
    if fold_from is None or len(fold_from) == 0:
        return None
    times = _index_unix_seconds(fold_from.index)
    if times is None:
        return None
    if len(times) > 1 and int(times[0]) > int(times[-1]):
        return None                                          # 昇順でない＝前提が崩れている
    hi = int(bisect_left(times, first))
    if hi <= 0:
        return fold_from.iloc[0:0]
    lo, high = 0, hi
    while lo < high:                                         # label0 が始まる位置を二分探索
        mid = (lo + high) // 2
        if int(bar_time_unix(compute_tf, int(times[mid]))) < label0:
            lo = mid + 1
        else:
            high = mid
    return fold_from.iloc[lo:hi]


def _head_of_first_period(
    chart_all: "list[dict]", *, first: int, label0: int,
    compute_tf: str, bar_time_unix: Callable[[str, int], int],
) -> "list[dict]":
    """``_head_frame_of_first_period`` の bar 列版（DataFrame を持たない経路のフォールバック）。"""
    n = len(chart_all)
    if n == 0:
        return []
    if int(chart_all[0]["time"]) > int(chart_all[-1]["time"]):
        return [b for b in chart_all if int(b["time"]) < first
                and int(bar_time_unix(compute_tf, int(b["time"]))) == label0]
    hi = bisect_left([int(b["time"]) for b in chart_all], first)
    lo = hi
    while lo > 0:
        t = int(chart_all[lo - 1]["time"])
        if int(bar_time_unix(compute_tf, t)) != label0:
            break
        lo -= 1
    return chart_all[lo:hi]


def _bar_key(bar: dict) -> tuple:
    """バーの同一性キー（時刻＋OHLCV）。接頭辞の土台が同じものかを O(1) で確かめる。"""
    return (
        int(bar["time"]),
        float(bar.get("open") or 0.0), float(bar.get("high") or 0.0),
        float(bar.get("low") or 0.0), float(bar.get("close") or 0.0),
        float(bar.get("volume") or 0.0),
    )


def causal_mtf_frames(
    *,
    df_chart: Any,
    df_source: Any,
    compute_tf: str,
    bar_time_unix: Callable[[str, int], int],
    compute_latest: Callable[["pd.DataFrame"], "list[dict]"],
    fold_from: Any = None,
    memo: Any = None,
) -> "list[dict]":
    """DataFrame で受けて因果 MTF 系列を返す（規約の実体は ``mtf_causal``）。

    Args:
        df_chart: 出力対象のチャート足 C（limit 適用後）。
        df_source: 計算足 H の保存済みバー。
        fold_from: 畳みに使う C 足の全体（``df_chart`` が期間の途中から始まる場合に、その
            期間の先頭から畳むために渡す）。None なら ``df_chart`` だけで畳む。
        memo: バー単位の記憶（ISSUE-297）。``mtf_causal.causal_mtf_series`` へ素通しする。
    """
    window = bars_from_frame(df_chart)
    if not window:
        return []
    source = bars_from_frame(df_source)
    if not source:
        return []
    first = int(window[0]["time"])
    label0 = int(bar_time_unix(compute_tf, first))
    # 出力窓の先頭が属する期間は、窓より前の C 足も畳みに要る（途中から畳むと値がずれる）。
    #   要るのは「窓の直前に連なる同一期間の連続区間」だけなので、**DataFrame のまま行を絞って
    #   から** dict 化する。先に全体を dict 化すると、要らない行まで作って捨てることになる
    #   （実測 C=1m / H=1M で 50,000 行を変換して 3 行しか使わないケースがある＝ISSUE-450 E）。
    if fold_from is None:
        head = _head_of_first_period(window, first=first, label0=label0,
                                     compute_tf=compute_tf, bar_time_unix=bar_time_unix)
    else:
        head_frame = _head_frame_of_first_period(
            fold_from, first=first, label0=label0,
            compute_tf=compute_tf, bar_time_unix=bar_time_unix)
        if head_frame is None:                    # 昇順でない等＝従来経路（全件走査）へ落ちる
            head = _head_of_first_period(
                bars_from_frame(fold_from), first=first, label0=label0,
                compute_tf=compute_tf, bar_time_unix=bar_time_unix)
        else:
            head = bars_from_frame(head_frame)
    return causal_mtf_series(
        chart_bars=[*head, *window],
        source_bars=source,
        compute_tf=compute_tf,
        bar_time_unix=bar_time_unix,
        latest_seq=latest_seq_over(compute_latest),
        window_bars=window,
        memo=memo,
    )
