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
        IndicatorComputePort,
        IntrabarWindowPort,
        SourceLoadPort,
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
    """C の各バーへ「**そのバーの時点で計算できた** H の値」を載せた因果系列を返す。

    規約（ISSUE-294 / 295）:

        value(τ) = 指標( [τ の期間より前の確定 H 足] + [τ の期間の C 足を τ まで畳んだ H 足] )

    従来（ISSUE-287〜292）は「T の時点の H 系列を計算し、期間単位で C へ投影」していた。
    T における見え方としては正しいが、点の意味が「その期間の値」であるため**過去のバーの点に
    τ より後の情報が載る**（各期間がその期間の最終値で塗り潰される）。新しい規約では各点が
    自分より後のデータに依存しない＝**時刻不変**になり、後から塗り替える必要がなくなる。

    規約の実体は Port（``causal_series``）の先＝ライブ core と**同一の唯一源**
    （``adapter.compute.mtf_causal``）にある。本関数は「T で切る・窓を採る・期間の先頭まで
    遡って畳み材料を渡す」という入力合わせだけを担い、規則を写さない。
    """
    limit = request.limit
    c_all = truncate(compute_port.load_source(request.ref, request.timeframe),
                     request.until_time)
    h_all = truncate(compute_port.load_source(request.ref, compute_tf), request.until_time)
    if not c_all or not h_all:
        return []
    window = c_all[-limit:] if isinstance(limit, int) and limit > 0 else list(c_all)
    if not window:
        return []
    # 窓の先頭が属する期間は、窓より前の C 足も畳みに要る（期間の途中から畳むと値がずれる）。
    head = len(c_all) - len(window)
    first_label = compute_port.bar_time(compute_tf, int(window[0]["time"]))
    while head > 0 and compute_port.bar_time(
            compute_tf, int(c_all[head - 1]["time"])) == first_label:
        head -= 1
    return compute_port.causal_series(
        request.indicator, request.variant, c_all[head:], h_all, compute_tf, window,
        request.params,
    )


def _seq_projection_timeframe(request: "CausalComputeSeqRequest") -> "str | None":
    tf = getattr(request, "compute_timeframe", None)
    if not tf or tf == "chart" or tf == request.timeframe:
        return None
    return str(tf)


def _causal_h_window(
    *, request_ref: str, timeframe: "str | None", compute_tf: str, until_time: "int | None",
    limit: "int | None", compute_port: "CausalComputePort",
) -> "tuple[list[dict], list[dict], int, list[dict]] | None":
    """計算足 H の窓素材（確定 H 足・進行中期間の C 足・進行中 H 足の時刻）を返す（ISSUE-291）。

    **リプレイの H 源は進行中期間の足も「その期間の全 OHLC」を持つ**（保存済みロールアップ）。
    リビール T が期間の途中なら、その足には T より先の高値・安値・終値が入っている＝未来。
    したがって進行中期間の H 足は源から採ってはならず、必ず C 足（T まで）から畳んで作る。

    この窓の作り方は本関数 1 箇所を唯一源とし、リビール経路（``_compute_projected``）と
    足内経路（``_compute_seq_higher_timeframe``）の双方が使う。片方だけが畳むと、同じ瞬間に
    対して 2 つの値が生まれる（実測: 5m×1D EMA5(high) でリビール 66098.55 / 足内 64970.39 ＝
    1128 の段差。前者は当日全体の高値 66700.24 を含む未来参照だった）。

    返り値は ``(確定 H 足, 進行中期間の C 足, 進行中 H 足の時刻, T までの C 足全体)``。
    窓が空なら None。進行中期間の C 足は ``limit`` で切らない（期間の途中から畳むと形成中
    H 足が欠ける）。C 足全体を併せて返すのは、投影先の時間軸を得るための再読込を避けるため。
    """
    h_all = truncate(compute_port.load_source(request_ref, compute_tf), until_time)
    c_all = truncate(compute_port.load_source(request_ref, timeframe), until_time)
    if not h_all or not c_all:
        return None
    now = int(c_all[-1]["time"])
    # 進行中 H 足の **ラベル**（畳んだ足に載せる time）と、**期間の始端**（どの C 足がこの期間に
    #   属するかの判定）。両者は別物で、セッション足では一致しない（ISSUE-292 実測: 1D の
    #   t=2026-08-06 22:20 UTC はラベル 08-07 00:00・始端 08-06 21:00）。ラベルで属否を判定すると
    #   期間前半の C 足が 1 本も選ばれず、形成足が作られないまま確定足だけで計算してしまう。
    h_bar_time = compute_port.bar_time(compute_tf, now)
    period_start = compute_port.period_start(compute_tf, now)
    confirmed = [b for b in h_all if int(b["time"]) < h_bar_time]
    if isinstance(limit, int) and limit > 0:
        confirmed = confirmed[-limit:]
    in_period = [b for b in c_all if int(b["time"]) >= period_start]
    return confirmed, in_period, h_bar_time, c_all


def _h_bars_with_forming(confirmed, in_period, h_bar_time: int) -> "list[dict]":
    """確定 H 足に、C 足から畳んだ進行中 H 足を継ぎ足した計算窓を返す。"""
    if not in_period:
        return list(confirmed)
    return [*confirmed, _fold_bars(in_period, time=h_bar_time)]


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
    window = _causal_h_window(
        request_ref=request.ref, timeframe=request.timeframe, compute_tf=compute_tf,
        until_time=request.until_time, limit=request.limit, compute_port=compute_port,
    )
    if window is None:
        return [[] for _ in seq]
    return _seq_steps_over_h_window(
        window=window, seq=seq, indicator=request.indicator, variant=request.variant,
        params=request.params, compute_port=compute_port,
    )


def _seq_steps_over_h_window(
    *, window, seq: "list[dict]", indicator: str, variant: str, params: dict,
    compute_port: "IndicatorComputePort",
) -> "list[list[dict]]":
    """H 窓素材を 1 つ受け取り、足内の各時点の latest を同順で返す（唯一の実体）。

    ISSUE-300: 単発（1 指標＝``_compute_seq_higher_timeframe``）と一括（複数指標＝
    ``causal_compute_seq_multi``）が **同じ H 窓を共有して** 本関数を呼ぶ。窓素材の構築
    （``_causal_h_window``）は計算足ごとに 1 回でよく、指標ごとに作り直すのは捨てられる計算。
    """
    confirmed, in_period, h_bar_time, _c_all = window
    out: "list[list[dict]]" = []
    for f in seq:
        snapshot = dict(f)
        # 足内では最終 C 足だけがその時点のスナップショットへ差し替わる（他は確定済み）。
        parts = in_period[:-1] + [snapshot] if in_period else [snapshot]
        series = compute_port.compute(
            indicator, variant, "latest",
            _h_bars_with_forming(confirmed, parts, h_bar_time), params
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
    bars = _seq_chart_window(
        ref=request.ref, timeframe=request.timeframe, until_time=request.until_time,
        limit=request.limit, compute_port=compute_port,
    )
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


def _seq_chart_window(
    *, ref: str, timeframe: "str | None", until_time: "int | None", limit: "int | None",
    compute_port: "SourceLoadPort",
) -> "list[dict]":
    """チャート足 C の計算窓（load → truncate → tail）。単発と一括が共有する唯一の実体。

    受けるのは**ロード面だけ**である（ISSUE-479 S-5）。この関数が指標計算を呼ばないことは
    型に現れているべきで、広い型で受けると読み手にも Decorator にもそれが伝わらない。
    """
    bars = compute_port.load_source(ref, timeframe)
    bars = truncate(bars, until_time)
    if isinstance(limit, int) and limit > 0:
        bars = bars[-limit:]
    return bars


@dataclass
class CausalComputeSeqSpec:
    """一括足内計算の 1 指標ぶんの申告（ISSUE-300）。"""
    instance_id: str
    indicator: str
    variant: str
    params: dict
    compute_timeframe: "str | None" = None


@dataclass
class CausalComputeSeqMultiRequest:
    """/compute mode='latest_seq_multi' の入力（足内一括計算の複数指標版・ISSUE-300）。

    ``causal_compute_seq`` を指標ごとに呼ぶのと **同値** の結果を返す。差は共有できる仕事を
    1 回に畳む点だけである:
      - チャート足 C の窓（load_source → truncate → tail）を 1 回
      - 実 tick 数の読み取り（``_tick_counts_for``）を 1 回
      - 計算足 H の窓素材（``_causal_h_window``）を **計算足ごとに 1 回**（指標ごとではない）
    実測（2026-08-08・指標 14 本）: 指標ごとに発行すると 1 足 2.6 秒で、その大半は指標ごとに
    払っていた窓ロードの固定費（0.14〜0.28 秒/指標）だった。
    """
    ref: str
    timeframe: "str | None"
    limit: "int | None"
    until_time: "int | None"
    forming_seq: "list[dict]"
    specs: "list[CausalComputeSeqSpec]"
    win_start: "int | None" = None
    win_end: "int | None" = None


def causal_compute_seq_multi(
    *, request: CausalComputeSeqMultiRequest, compute_port: "CausalComputePort",
    window_port: "IntrabarWindowPort | None" = None,
) -> "dict[str, list[list[dict]]]":
    """instanceId → 足内推移の各時点の series（``causal_compute_seq`` と同値）を返す。

    空 ``forming_seq`` / 空 ``specs`` は ``{}``（呼び出し自体を無害化）。
    """
    seq = request.forming_seq or []
    specs = request.specs or []
    if not seq or not specs:
        return {}
    # 実 tick 数は窓が共通＝1 回だけ数える（指標ごとに読み直さない）。
    counts = _tick_counts_for(seq, window_port, request.win_start, request.win_end)
    seq_with_volume = [with_tick_volume(f, c) for f, c in zip(seq, counts)]

    chart_bars: "list[dict] | None" = None
    h_windows: dict = {}
    out: "dict[str, list[list[dict]]]" = {}
    for spec in specs:
        tf = spec.compute_timeframe
        compute_tf = None if (not tf or tf == "chart" or tf == request.timeframe) else str(tf)
        if compute_tf is None:
            if chart_bars is None:
                chart_bars = _seq_chart_window(
                    ref=request.ref, timeframe=request.timeframe,
                    until_time=request.until_time, limit=request.limit,
                    compute_port=compute_port,
                )
            if len(chart_bars) == 0:
                out[spec.instance_id] = []
                continue
            prefix, tails = split_prefix_tails(chart_bars, seq_with_volume)
            out[spec.instance_id] = compute_port.compute_latest_seq(
                spec.indicator, spec.variant, prefix, tails, spec.params
            )
            continue
        if compute_tf not in h_windows:
            h_windows[compute_tf] = _causal_h_window(
                request_ref=request.ref, timeframe=request.timeframe, compute_tf=compute_tf,
                until_time=request.until_time, limit=request.limit, compute_port=compute_port,
            )
        window = h_windows[compute_tf]
        if window is None:
            out[spec.instance_id] = [[] for _ in seq]
            continue
        # 単発（_compute_seq_higher_timeframe）と同じく、H 経路は実 tick 数を載せない。
        out[spec.instance_id] = _seq_steps_over_h_window(
            window=window, seq=seq, indicator=spec.indicator, variant=spec.variant,
            params=spec.params, compute_port=compute_port,
        )
    return out


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
