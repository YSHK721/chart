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


def _bar_signature(bar: dict) -> tuple:
    """バーの値そのもの（時刻＋OHLCV）。指紋の材料（ISSUE-297）。"""
    return (
        int(bar["time"]),
        float(bar["open"]), float(bar["high"]), float(bar["low"]), float(bar["close"]),
        float(bar.get("volume") or 0.0),
    )


#: 空の前置き（0 本）の指紋。連鎖の種であり、逐次版と一括版で必ず同じ値を使う。
_EMPTY_PREFIX_FP = hash(("mtf_causal", 0))


def _prefix_fingerprints(bars: "list[dict]") -> "list[int]":
    """``bars`` の各前置き（0..n 本）の指紋。``out[i]`` は先頭 i 本ぶんの指紋。

    本体（:func:`causal_mtf_series`）は読む位置までしか連鎖を伸ばさない逐次版を使う
    （ISSUE-450 F）。本関数は連鎖の**定義**であり、逐次版が同じ値を出すことを
    ``tests/test_mtf_causal_memo.py`` が突合する。
    """
    out = [_EMPTY_PREFIX_FP]
    acc = out[0]
    for b in bars:
        acc = hash((acc, _bar_signature(b)))
        out.append(acc)
    return out


def _compact(series: "list[dict]") -> "list[dict]":
    """1 時点ぶんの計算結果を、系列ごとに**末尾の 1 点だけ**へ畳む（記録・出力の共通形）。"""
    out = []
    for p in series or []:
        data = p.get("data") or []
        if p.get("kind") not in _SERIES_KINDS or not data:
            continue
        out.append({**p, "data": [data[-1]]})
    return out


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
    memo: Any = None,
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
        memo: バー単位の記憶（``get(τ, 指紋)`` / ``put(τ, 指紋, 点)``。省略時は記憶しない＝
            従来どおり全バーを計算する）。``value(τ)`` は τ より後のデータに依存しない
            （ISSUE-294 / 295）ため、同じ入力の同じ τ を計算し直す必要がない（ISSUE-297）。
            指紋は ``value(τ)`` を決める入力そのもの（確定 H 足の前置き＋τ まで畳んだ H 足）から
            作るため、形成中バー・``untilTime`` 途中・データ訂正はすべて別物として扱われる。

    Returns:
        ``[{name, kind, data:[{time, value...}], stepped: True, ...}]``。期間境界の跳ねを
        斜線で結ばないため ``stepped`` を立てる（ISSUE-289）。
    """
    if not chart_bars or not source_bars:
        return []
    keep = {int(b["time"]) for b in (window_bars if window_bars is not None else chart_bars)}
    # 接頭辞の指紋は**期間の切れ目でしか読まれない**。全位置ぶんを先に作ると、読まれない位置の
    #   指紋を作って捨てることになる（実測 C=1m / H=1h で 50,001 個作って 10 個しか読まない
    #   ＝ISSUE-450 F）。連鎖は切れ目まで前進させれば足りるので、走る累算器 1 本で持つ。
    #   値は `_prefix_fingerprints(source_bars)[cut]` と同一である（同じ順序・同じ合成）。
    fp_acc = _EMPTY_PREFIX_FP if memo is not None else None
    fp_pos = 0
    out: "dict[Any, dict]" = {}
    order: "list[Any]" = []
    # 確定 H 足の切れ目は期間ラベルの昇順に単調前進する（group_by_period は chart_bars の順序を
    #   保ち、chart_bars は時刻昇順）。期間ごとに source_bars を全走査すると走査量が
    #   「期間数 × H 足数」に膨らむ（実測 C=1m / H=5m で 5,244,435 行）ため、切れ目は
    #   ポインタで前進させる。昇順でない入力が来たときだけ従来の全走査へ落ちる（安全側）。
    src_times = [int(b["time"]) for b in source_bars]
    cut = 0
    prev_label: "int | None" = None
    for label, part in group_by_period(
            chart_bars, compute_tf=compute_tf, bar_time_unix=bar_time_unix):
        if prev_label is not None and label < prev_label:
            cut = 0                                   # 昇順が崩れた＝ポインタを捨てて数え直す
        prev_label = label
        while cut < len(src_times) and src_times[cut] < label:
            cut += 1
        confirmed = source_bars[:cut]
        confirmed_fp = None
        if fp_acc is not None:
            if cut < fp_pos:                          # 切れ目が戻った＝連鎖を先頭から作り直す
                fp_acc, fp_pos = _EMPTY_PREFIX_FP, 0
            while fp_pos < cut:                       # 読む位置まで**だけ**連鎖を伸ばす
                fp_acc = hash((fp_acc, _bar_signature(source_bars[fp_pos])))
                fp_pos += 1
            confirmed_fp = fp_acc
        tails: "list[list[dict]]" = []
        times: "list[int]" = []
        plan: "list[tuple[int, Any, Any]]" = []   # (τ, 記憶にあった点 or None, 指紋 or None)
        acc: "dict | None" = None
        for b in part:
            acc = fold_bars([acc, b] if acc else [b], time=label)
            t = int(b["time"])
            # 出力窓の外のバー（fold_from が足す期間先頭側の C 足）は、畳み acc へ寄与させる
            #   ためだけに必要で、その時点の指標値は出力に使わない。ここで計算を発行すると
            #   結果を作ってから捨てることになる（実測 C=1m / H=1M で発行 25,124 件のうち
            #   24,624 件＝98.0% が破棄）。畳みは上で済んでいるので、発行せずに次へ進む。
            if t not in keep:
                continue
            fingerprint = None
            cached = None
            if memo is not None:
                fingerprint = hash((confirmed_fp, _bar_signature(acc)))
                cached = memo.get(t, fingerprint)
            if cached is not None:
                plan.append((t, cached, None))
                continue        # 記憶にある＝この時点は計算を発行しない
            tails.append([dict(acc)])
            times.append(t)
            plan.append((t, None, fingerprint))
        steps = latest_seq(confirmed, tails) if tails else []
        fresh = dict(zip(times, steps or []))
        for t, cached, fingerprint in plan:
            if t not in keep:
                continue
            series = cached if cached is not None else _compact(fresh.get(t) or [])
            if not series:
                continue
            if cached is None and memo is not None and fingerprint is not None:
                memo.put(t, fingerprint, series)
            for p in series:
                name = p.get("name")
                if name not in out:
                    out[name] = {**p, "data": [], "stepped": True}
                    order.append(name)
                out[name]["data"].append({**p["data"][-1], "time": t})
    return [out[name] for name in order]
