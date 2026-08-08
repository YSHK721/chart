"""UC-R2 causal_compute — /compute の usecase 結線（proto do_compute 忠実）。

手順（proto do_compute と同順）:
    1. CausalComputePort.load_source(ref, tf)   （未知 ref/tf は ValueError）
    2. RevealClock.truncate(bars, untilTime)     （再生のその時点まで＝因果不変）
    3. tail(limit)                               （limit>0 のとき末尾 limit 本）
    4. 空なら [] を返す
    5. mode=='latest' なら FormingBar.apply(forming) して latest 計算、それ以外は full 計算

domain（reveal_clock / forming_bar）のみ依存。numpy/pandas を import しない。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from common.forming_window import apply_forming, split_prefix_tails
from simulator.replay_ui.domain.reveal_clock import truncate
from simulator.replay_ui.usecase.forming_tickvol import forming_tick_counts, with_tick_volume

if TYPE_CHECKING:
    from simulator.replay_ui.usecase.replay_ports import (
        CausalComputePort,
        IntrabarWindowPort,
    )


@dataclass
class CausalComputeSeqRequest:
    """/compute mode='latest_seq' の入力（足内一括計算・ISSUE-232）。

    ``forming_seq`` は 1 本のバーの足内推移（ティックごとの暫定 OHLC）を昇順に並べたもの。
    各要素へ ``latest`` 計算を適用した結果を同順で返す。窓のロード・truncate・tail は
    **1 回だけ**行い、以降は forming の差し替えのみを繰り返す（1 ステップあたりの限界費用が
    指標計算そのものだけになる＝実測 load_source 242ms / latest 6.6ms）。
    """
    indicator: str
    variant: str
    ref: str
    timeframe: "str | None"
    limit: "int | None"
    until_time: "int | None"
    forming_seq: "list[dict]"
    params: dict
    #: 足内窓（ISSUE-238）。各 forming の ``to`` と併せて実 tick 数を数えるために使う。
    #:   規則源はフロントの ``intrabarWindow`` 1 箇所（サーバへ写さない）。未指定は従来挙動。
    win_start: "int | None" = None
    win_end: "int | None" = None
    #: 計算足 H（ISSUE-290）。指定時はライブと同一設計＝H の形成中バーへ畳んでから計算する。
    compute_timeframe: "str | None" = None


@dataclass
class CausalComputeRequest:
    """/compute の入力（proto body 準拠）。"""
    indicator: str
    variant: str
    ref: str
    timeframe: "str | None"
    limit: "int | None"
    until_time: "int | None"
    mode: "str | None"
    forming: "dict | None"
    params: dict
    #: 足内窓（ISSUE-238）。``forming["to"]`` と併せて実 tick 数を数えるために使う。
    win_start: "int | None" = None
    win_end: "int | None" = None
    #: 計算足 H（ISSUE-287）。``timeframe``（チャート足 C）と異なるとき、計算は H で行い
    #:   結果を C の時間軸へ投影する。None / 'chart' / C と同値なら従来どおり投影しない。
    compute_timeframe: "str | None" = None


def causal_compute(
    *, request: CausalComputeRequest, compute_port: "CausalComputePort",
    window_port: "IntrabarWindowPort | None" = None,
) -> "list[dict]":
    """series（plain dict の list）を返す。空窓は ``[]``。

    ``window_port`` を渡すと、形成中バーへ **その時点までの実 tick 数** を ``volume`` として
    載せてから適用する（ISSUE-238）。未指定・``forming["to"]`` 無し・ティック不明のときは
    載せない＝従来挙動と 1 ビットも変わらない。
    """
    # ISSUE-287: 上位足計算（計算.時間足）。ライブ core と同一の規約で「H で計算 → C の
    #   時間軸へ投影」する。従来はリプレイだけがこの分岐を持たず、`computeTimeframe` を
    #   **無言で捨てて** C 足で計算していた（front は投影済みのつもりで描く＝無言の縮退）。
    compute_tf = _projection_timeframe(request)
    if compute_tf is not None:
        return _compute_projected(
            request=request, compute_port=compute_port, compute_tf=compute_tf,
        )
    bars = compute_port.load_source(request.ref, request.timeframe)
    bars = truncate(bars, request.until_time)
    limit = request.limit
    if isinstance(limit, int) and limit > 0:
        bars = bars[-limit:]
    if len(bars) == 0:
        return []
    if request.mode == "latest":
        forming = _with_tick_volume_one(
            request.forming, window_port, request.win_start, request.win_end
        )
        bars = apply_forming(bars, forming)
        return compute_port.compute(
            request.indicator, request.variant, "latest", bars, request.params
        )
    return compute_port.compute(
        request.indicator, request.variant, "full", bars, request.params
    )


def _projection_timeframe(request: "CausalComputeRequest") -> "str | None":
    """投影が要る計算足 H を返す（不要なら None）。判定はライブ core と同一規約。"""
    tf = request.compute_timeframe
    if not tf or tf == "chart" or tf == request.timeframe:
        return None
    return str(tf)


def _bar_times(bars) -> "list[int]":
    """bar 列（plain dict）から UNIX 秒の時刻列を取り出す。"""
    return [int(b["time"]) for b in bars]


def _compute_projected(
    *, request: "CausalComputeRequest", compute_port: "CausalComputePort", compute_tf: str,
) -> "list[dict]":
    """H で計算し、C（チャート足）の時間軸へ投影して返す（ISSUE-287）。

    因果性: H・C とも ``truncate(until_time)`` を通す（リビール T 以前だけを使う）。
    投影規約（確定済み期間＝その時点の確定値／進行中期間＝形成値）は
    ``adapter.compute.mtf_projection`` が唯一源で、本関数は入力を合わせて呼ぶだけ。
    """
    chart_bars = truncate(compute_port.load_source(request.ref, request.timeframe),
                          request.until_time)
    limit = request.limit
    if isinstance(limit, int) and limit > 0:
        chart_bars = chart_bars[-limit:]
    if len(chart_bars) == 0:
        return []
    source_bars = truncate(compute_port.load_source(request.ref, compute_tf),
                           request.until_time)
    if isinstance(limit, int) and limit > 0:
        source_bars = source_bars[-limit:]
    if len(source_bars) == 0:
        return []
    series = compute_port.compute(
        request.indicator, request.variant, "full", source_bars, request.params
    )
    return compute_port.project(series, _bar_times(chart_bars), compute_tf)


def _seq_projection_timeframe(request: "CausalComputeSeqRequest") -> "str | None":
    tf = getattr(request, "compute_timeframe", None)
    if not tf or tf == "chart" or tf == request.timeframe:
        return None
    return str(tf)


def _fold_bars(bars, *, time: int) -> dict:
    """バー列を 1 本へ畳む（open=先頭 open・high=最大・low=最小・close=末尾 close）。"""
    return {
        "time": int(time),
        "open": float(bars[0]["open"]),
        "high": max(float(b["high"]) for b in bars),
        "low": min(float(b["low"]) for b in bars),
        "close": float(bars[-1]["close"]),
        "volume": float(sum(float(b.get("volume") or 0.0) for b in bars)),
    }


def _compute_seq_higher_timeframe(
    *, request: "CausalComputeSeqRequest", compute_port: "CausalComputePort", compute_tf: str,
) -> "list[list[dict]]":
    """足内の各時点について、**計算足 H の形成中バー**を作って latest 計算する（ISSUE-290）。

    ライブ（ISSUE-274 D-4）と同一設計:
      1. H の確定足だけを窓に採る（進行中の H 足はデータ由来の全期間 OHLC＝未来を含むため捨てる）。
      2. 進行中 H 足を、C 足（リビール T までの確定足）＋その時点の足内スナップショットから畳んで作る。
      3. その窓で latest 計算し、値を C の形成足時刻へ載せる。
    畳み方（open/high/low/close の合成）は 1 箇所（``_fold_bars``）に閉じる。
    """
    seq = request.forming_seq or []
    h_all = truncate(compute_port.load_source(request.ref, compute_tf), request.until_time)
    c_all = truncate(compute_port.load_source(request.ref, request.timeframe), request.until_time)
    if not h_all or not c_all:
        return [[] for _ in seq]
    limit = request.limit
    if isinstance(limit, int) and limit > 0:
        h_all = h_all[-limit:]
        c_all = c_all[-limit:]
    # 進行中 H 期間の始端（＝その足の time）。C 足の最終時刻がどの H 足に属するかで決める。
    h_bar_time = compute_port.bar_time(compute_tf, int(c_all[-1]["time"]))
    confirmed = [b for b in h_all if int(b["time"]) < h_bar_time]
    in_period = [b for b in c_all if int(b["time"]) >= h_bar_time]
    out: "list[list[dict]]" = []
    for f in seq:
        snapshot = dict(f)
        parts = in_period[:-1] + [snapshot] if in_period else [snapshot]
        forming_h = _fold_bars(parts, time=h_bar_time)
        series = compute_port.compute(
            request.indicator, request.variant, "latest", [*confirmed, forming_h], request.params
        )
        # 値は C の形成足時刻へ載せる（front は末尾差分として描く）。
        step: "list[dict]" = []
        for p in series or []:
            data = p.get("data") or []
            if not data:
                step.append(p)
                continue
            step.append({**p, "data": [{**data[-1], "time": int(snapshot["time"])}]})
        out.append(step)
    return out


def causal_compute_seq(
    *, request: CausalComputeSeqRequest, compute_port: "CausalComputePort",
    window_port: "IntrabarWindowPort | None" = None,
) -> "list[list[dict]]":
    """足内推移の各時点の latest series を同順で返す（ISSUE-232）。

    ``causal_compute`` の mode='latest' を ``forming_seq`` の各要素へ繰り返し適用したものと
    **完全同値**である（同一の窓・同一の apply_forming・同一の latest 計算を通す）。差は
    「窓のロード / truncate / tail を 1 回に畳む」点だけで、値には影響しない。同値性は
    ``tests/unit/test_causal_compute_seq.py`` と実データのゲート実測で固定する。

    空 ``forming_seq`` は ``[]``（呼び出し自体を無害化）。空窓も ``[]``。
    """
    seq = request.forming_seq or []
    if not seq:
        return []
    # ISSUE-290: 上位足計算（計算.時間足）はライブと同一設計＝「計算足ごとに、その足の
    #   形成中バーへ畳んでから latest 計算する」。チャート足の窓で計算して投影する形にはしない
    #   （足内では H の形成足そのものが動くため、投影では表現できない）。
    compute_tf = _seq_projection_timeframe(request)
    if compute_tf is not None:
        return _compute_seq_higher_timeframe(
            request=request, compute_port=compute_port, compute_tf=compute_tf,
        )
    bars = compute_port.load_source(request.ref, request.timeframe)
    bars = truncate(bars, request.until_time)
    limit = request.limit
    if isinstance(limit, int) and limit > 0:
        bars = bars[-limit:]
    if len(bars) == 0:
        return []
    # ISSUE-233: 形成中バーの適用は **末尾しか変えない**（apply は先頭側を触らない）。
    #   よって「共通の確定プレフィクス」と「時点ごとの末尾差分」に分けて渡し、計算側が窓を
    #   1 回だけ変換できるようにする。値は apply_forming(bars, forming) 全体を渡すのと同値
    #   （同値性は tests/unit/test_causal_compute_seq.py と forming_bar のテストで固定）。
    # ISSUE-238: 各時点の実 tick 数を 1 回のティック読込でまとめて数え、volume として載せる。
    #   時点ごとに読み直さない（窓は共通）。不明なら None＝従来どおり載せない。
    counts = _tick_counts_for(seq, window_port, request.win_start, request.win_end)
    # ISSUE-250 Phase 1: prefix/tails 分割は中立共有核 common.forming_window の唯一の定義。
    prefix, tails = split_prefix_tails(
        bars, [with_tick_volume(f, c) for f, c in zip(seq, counts)]
    )
    return compute_port.compute_latest_seq(
        request.indicator, request.variant, prefix, tails, request.params
    )


def _tick_counts_for(
    seq: "list[dict]",
    window_port: "IntrabarWindowPort | None",
    win_start: "int | None",
    win_end: "int | None",
) -> "list[int | None]":
    """各 forming 状態の ``to`` に対する実 tick 数（不明は None）。"""
    if window_port is None:
        return [None] * len(seq)
    tos = [(f or {}).get("to") for f in seq]
    if all(t is None for t in tos):
        return [None] * len(seq)
    return forming_tick_counts(
        window_port=window_port, win_start=win_start, win_end=win_end, tos=tos
    )


def _with_tick_volume_one(
    forming: "dict | None",
    window_port: "IntrabarWindowPort | None",
    win_start: "int | None",
    win_end: "int | None",
) -> "dict | None":
    """単発 forming（mode='latest'）へ実 tick 数を載せる。"""
    counts = _tick_counts_for([forming or {}], window_port, win_start, win_end)
    return with_tick_volume(forming, counts[0])
