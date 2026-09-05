"""UC-01（段 1・バー確定）: 既存系列を読み、第 1 表（価格ラダー）と第 2 表を組み立てる。

計算量の規律（§7・T-1・絶対命令 CLAUDE.md §4.1）:
    (a) 同一 `(indicator_id, variant, params_key, timeframe)` の full 系列発行は **1 回以下**、
    (b) 発行した系列は必ず出力に使われる（**発行 − 使用 = 0**）。
    本シートは既存の計算結果を**読むだけ**であり、新規の計算を発行してはならない。
    実測（§7）: ラダーの 71 本は全指標 105 本の部分集合であり、別々に計算すると 2,316ms が
    丸ごと無駄になる（ISSUE-450 と同型）。P-1 は「1 呼出 = 1 計算 = 3 消費者で共有」の束契約。

到達の向き（§6.1）についての注記:
    設計書 §6.1 の式は `reached_t := value_t >= level_t` であり、ラダーの行はこの既定を使う。
    この向きだと「価格がその水準を上抜けている状態」が到達になり、現在値より下の行にも上の行にも
    非自明な到達時刻が出る（現在位置から向きを決めると、どの行も常に未到達になり定義 C が死ぬ）。
    第 2 表は §5.2 の「帯を出ているとき」に合わせ、上帯は既定・下帯は反転（`LevelSide.BELOW`）で
    使う。§6.1 の但し書き「上側水準は方向を反転」は式と読み合わせると一意に定まらないため、
    **どちらの側を反転と呼ぶか**は依頼者確認が要る（本実装は上記の解釈で統一している）。
"""
from __future__ import annotations

import math
from typing import Mapping, Sequence

import numpy as np

from dashboard_ui.domain import continuous_quantile as _cq
from dashboard_ui.domain.bar import Bar
from dashboard_ui.domain.material_version import fingerprint_of
from dashboard_ui.domain.price_ladder import LevelInput, build_ladder
from dashboard_ui.domain.reach import (
    LevelSide,
    ReachState,
    period_first_touch,
    reach_state,
)
from dashboard_ui.usecase.sheet_models import (
    Degradation,
    ElapsedComparison,
    LadderRow,
    OscCell,
    OscillatorSpec,
    ProjectedLevel,
    ReachSheetRequest,
    ReachSheetResponse,
    SeriesRole,
    SheetInstance,
    TrailingReading,
    UpdateGranularity,
)
from dashboard_ui.usecase.sheet_ports import SeriesSupplyUnavailable

#: 背景ストリップの「直近の過去区間」の本数。現在区間（セルの `p`）と合わせて
#: **10 区間**になる（依頼者指示 2026-09-04「各パネルの背景に直近の指標 10 区間分」）。
TRAILING_HISTORY_BARS: int = 9


class TailFitCache:
    """GPD の当てはめを**イベント確定のときだけ**行うためのキャッシュ（§7）。

    `p` を求めるたびに当てはめ直さない。エピソードが閉じないバーでは確定観測列が伸びないので、
    当てはめ回数は 0 になる。当てはめ自体は最尤推定（Nelder–Mead）で安くはなく、セル数・行数・
    ティック数に比例して呼ぶと ISSUE-450 と同型の浪費になる。

    key ごとに**署名別**へ持つ（直近区間の読み・§5.2 背景ストリップが同じ key で複数の
    因果窓［バーごとのイベント・プレフィックス］を引くため。単一スロットだと現在セルと
    ストリップが互いに押し出し合い、同じ窓を毎要求当てはめ直す）。署名はイベント確定の
    ときしか変わらないので、エントリはエピソード確定ごとに高々 1 つしか増えない。
    """

    def __init__(self) -> None:
        self._entries: "dict[tuple, dict[tuple, object]]" = {}

    def tail_for(
        self, key: "tuple[str, str, str, str]", events: "Sequence[float]", k_events: int
    ):
        """当てはめる窓が変わっていなければ、前回の当てはめをそのまま返す。

        署名は参照実装 `probe_tailscale.py:125` と同一の `(窓の本数, 窓の末尾, 窓の先頭)`。
        **本数だけを署名にしてはならない**: 窓は直近 `k_events` 件のローリングなので本数は
        上限で頭打ちになり、中身が入れ替わっても本数が同じままになる（古い当てはめを返す）。
        """
        window = [
            float(value) for value in list(events)[-int(k_events):]
            if math.isfinite(value)
        ]
        signature = (
            (len(window), window[-1], window[0]) if window else (0, None, None)
        )
        by_signature = self._entries.setdefault(key, {})
        if signature in by_signature:
            return by_signature[signature]
        fitted = _cq.fit_tail(events, k_events=k_events)
        by_signature[signature] = fitted
        return fitted


class ExcessEventCache:
    """帯外イベント履歴を **epoch 単位で持ち越す**（§7・ISSUE-464 ③）。

    超過エピソードの極値列は**確定した履歴**（当該バーを除いた観測）だけから決まるので、
    epoch の中では不変である。にもかかわらず、第 2 表のセル（`_build_cell`）と §5.5.5 の
    背景の目盛り（quantile_scale_of）が**それぞれ**毎要求畳み直していた
    （実測 2026-08-30・8 足束 1 要求: 48 回 / 366 ms ＝ 24 instance × 2 消費者）。
    畳み込みは 1 点ずつの Python ループなので系列長に比例する。出力は正しいままなので
    状態検証では原理的に落ちない——ISSUE-450 / ISSUE-257 と同型である。

    版（署名）の取り方は :class:`TailFitCache` と同じ考え方だが、**本数・端だけでは足りない**:
    確定履歴の途中を遡って訂正されても本数も端も変わらないため、古い観測を配り続けることに
    なる。版の定義そのものは `dashboard_ui.domain.material_version` が唯一所有する
    （面ごとに手書きすると片方だけ弱い版になる）。
    """

    def __init__(self) -> None:
        self._entries: "dict[tuple, tuple[tuple, tuple[list[float], tuple[int, ...]]]]" = {}

    def fold_for(
        self, key: "tuple[str, str, str, str]", values, band_highs, *, excess
    ) -> "tuple[list[float], tuple[int, ...]]":
        """確定履歴が変わっていなければ、前回畳んだ (観測列, プレフィックス長) をそのまま返す。

        プレフィックス長（`counts[i]` ＝ バー i 直前までに確定した件数）は直近区間の読み
        （§5.2 背景ストリップ）の因果窓に使う。**同じ 1 回の畳み込み**から両方を出す
        （系列版とプレフィックス版を別々に畳むと、片方だけ直したときに無言で食い違う）。
        """
        signature = (fingerprint_of(values), fingerprint_of(band_highs))
        cached = self._entries.get(key)
        if cached is not None and cached[0] == signature:
            return cached[1]
        folded = _cq.excess_event_fold(values, band_highs, excess=excess)
        self._entries[key] = (signature, folded)
        return folded

    def events_for(
        self, key: "tuple[str, str, str, str]", values, band_highs, *, excess
    ) -> "list[float]":
        """確定履歴が変わっていなければ、前回畳んだ観測列をそのまま返す。"""
        return self.fold_for(key, values, band_highs, excess=excess)[0]


class HistoryStripCache:
    """直近区間の読み（§5.2 背景ストリップ）を **epoch 単位で持ち越す**（§7）。

    ストリップは**確定した履歴**（当該バーを除いた観測）だけから決まるので、epoch の中では
    不変である。毎要求（毎秒ポーリング）計算し直すと、出力は正しいまま同じ読みを作り続ける
    ——状態検証では原理的に落ちない ISSUE-450 / ISSUE-464 と同型の浪費になる。
    版（署名）の定義は :class:`ExcessEventCache` と同じ `fingerprint_of`（唯一源）。
    """

    def __init__(self) -> None:
        self._entries: "dict[tuple, tuple[tuple, tuple]]" = {}

    def strip_for(
        self,
        key: "tuple[str, str, str, str]",
        observed,
        spec: OscillatorSpec,
        *,
        events_cache: ExcessEventCache,
        tails: TailFitCache,
    ) -> "tuple[TrailingReading, ...]":
        """確定履歴が変わっていなければ、前回の読みをそのまま返す。"""
        history_values, history_bands = observed.history
        signature = (fingerprint_of(history_values), fingerprint_of(history_bands))
        cached = self._entries.get(key)
        if cached is not None and cached[0] == signature:
            return cached[1]
        if spec.cumulative:
            readings = _cq.trailing_ranks(
                history_values, window_n=spec.window_n, n_bars=TRAILING_HISTORY_BARS,
            )
        else:
            # 畳み込み・当てはめは既存の持ち越しの口を通す（同じ観測を 2 人が別々に
            #   畳まない・同じ窓を 2 人が別々に当てはめない）。
            events, counts = events_cache.fold_for(
                key, history_values, history_bands, excess=spec.excess
            )
            readings = _cq.trailing_readings(
                history_values, history_bands,
                window_n=spec.window_n, q_high=spec.q_high,
                events=events, event_counts=counts, k_events=spec.k_events,
                n_bars=TRAILING_HISTORY_BARS, excess=spec.excess,
                fit=lambda observed_events: tails.tail_for(
                    key, observed_events, spec.k_events
                ),
            )
        # 下層（指標ミニ描画）の実値は同じ確定バーの系列値そのもの（依頼者明確化
        #   2026-09-04: ヒートの下に指標ペインが見えるイメージ）。読みと同じ末尾を添える。
        tail_values = history_values[len(history_values) - len(readings):]
        strip = tuple(
            TrailingReading(
                value=float(value) if math.isfinite(float(value)) else None,
                p=reading.p,
                tail_unscaled=reading.tail_unscaled,
            )
            for value, reading in zip(tail_values, readings)
        )
        self._entries[key] = (signature, strip)
        return strip


def build_reach_sheet(
    request: ReachSheetRequest,
    *,
    series_port,
    bar_port,
    roles,
    elapsed_comparisons: "Mapping[tuple[str, str, str, str], ElapsedComparison] | None" = None,
    tail_fit_cache: "TailFitCache | None" = None,
    event_cache: "ExcessEventCache | None" = None,
    history_cache: "HistoryStripCache | None" = None,
    projected_levels: "Sequence[ProjectedLevel]" = (),
) -> ReachSheetResponse:
    """段 1 のシートを組み立てる。

    Raises:
        ValueError: 表示時間足の足が 1 本も供給されないとき（現在値が決まらない）。
    """
    comparisons = dict(elapsed_comparisons or {})
    tails = tail_fit_cache if tail_fit_cache is not None else TailFitCache()
    events = event_cache if event_cache is not None else ExcessEventCache()
    history = history_cache if history_cache is not None else HistoryStripCache()
    instances = request.unique_instances()
    bars_by_timeframe = _load_bars(request, instances, bar_port)

    chart_bars = bars_by_timeframe.get(request.chart_timeframe) or ()
    if not chart_bars:
        raise ValueError(
            f"表示時間足の足が供給されていません: timeframe={request.chart_timeframe!r}"
        )
    current_price = float(chart_bars[-1].close)

    levels: "list[LevelInput]" = []
    reach_by_row: "dict[tuple[str, str], ReachState]" = {}
    instance_by_row: "dict[tuple[str, str], tuple[str, str, str, str]]" = {}
    naming_by_row: "dict[tuple[str, str], dict[str, object]]" = {}
    series_by_row: "dict[tuple[str, str], str | None]" = {}
    cells: "list[OscCell]" = []
    degradations: "list[Degradation]" = []

    for instance in instances:
        try:
            series = dict(
                series_port.full_series(
                    indicator_id=instance.indicator_id,
                    variant=instance.variant,
                    params=instance.params,
                    dataset_ref=request.dataset_ref,
                    timeframe=instance.timeframe,
                )
            )
        except SeriesSupplyUnavailable as error:
            # 供給不能はその instance の構造的除外（§5.5.1）。シート全体を落とさず、
            #   除外した instance と理由を必ず応答へ出す（§7・無言の縮退禁止）。
            degradations.append(
                Degradation(
                    instance_key=instance.key,
                    granularity=UpdateGranularity.NONE,
                    reason=f"系列を供給できないため除外した: {error}",
                )
            )
            continue
        if not instance.intrabar_capable:
            degradations.append(
                Degradation(
                    instance_key=instance.key,
                    granularity=UpdateGranularity.BAR_CLOSE,
                    reason="増分器が未登録のため足内更新できない（§7・無言の縮退を作らない）",
                )
            )
        spec = roles.oscillator_spec(
            instance=instance, series_names=frozenset(series)
        )
        if spec is not None:
            cells.append(
                _build_cell(instance, spec, series, comparisons.get(instance.key),
                            tails, events, history)
            )
        own_bars = bars_by_timeframe.get(instance.timeframe) or ()
        for series_name, points in series.items():
            level = _as_level(
                instance, series_name, tuple(points), roles, current_price
            )
            if level is None:
                continue
            levels.append(level)
            # 到達時間は定義 D（当該足の現在バー期間内の最初の接触・依頼者裁定 2026-08-31。
            #   D なら今日・1m ならこの 1 分間＝期間が変わればリセット）。期間の始端は
            #   当該足の**最新バーの time**（バー識別はサーバの足が唯一源＝暦計算を持たない）。
            reach_by_row[level.row_key] = _level_period_touch(
                level.price, fine_bars=chart_bars,
                period_start=(int(own_bars[-1].time) if own_bars else None),
            )
            instance_by_row[level.row_key] = instance.key
            series_by_row[level.row_key] = series_name
            naming_by_row[level.row_key] = roles.row_naming(
                instance=instance, series_name=series_name
            )

    # 分位水準に達する価格の射影行（依頼者指示 2026-08-31）。価格は adapter が §5.5 の
    #   閉形式逆写像で算出済み。並び・距離・差・地平の印は既存の ladder 構築へ**合流**させる
    #   だけ（第 2 の並び規則を作らない）。reach は持たない（系列が無い＝発明しない）。
    #   series も None＝なめらか tails の申告対象にならない（tick 再計算は射影では不能）。
    for extra in projected_levels:
        label = (f"{extra.indicator_id} {extra.level} 射影 "
                 f"{extra.instance_key[1]} {extra.instance_key[2]}")
        level = LevelInput(
            price=float(extra.price), timeframe=extra.timeframe, label=label,
        )
        levels.append(level)
        reach_by_row[level.row_key] = ReachState(
            reached=None, since_time=None, truncated=False,
        )
        instance_by_row[level.row_key] = extra.instance_key
        series_by_row[level.row_key] = None
        naming_by_row[level.row_key] = (
            dict(extra.naming) if extra.naming is not None else {
                "name": extra.indicator_id, "level": extra.level,
                "level_p": extra.level_p, "period": None, "source": None, "extra": "",
            }
        )

    ladder = build_ladder(levels, current_price=current_price)
    rows = tuple(
        LadderRow(
            price=row.price,
            timeframe=row.timeframe,
            label=row.label,
            distance=row.distance,
            gap_to_previous=row.gap_to_previous,
            horizon_marks=row.horizon_marks,
            reach=reach_by_row[(row.label, row.timeframe)],
            instance_key=instance_by_row[(row.label, row.timeframe)],
            naming=naming_by_row[(row.label, row.timeframe)],
            series=series_by_row[(row.label, row.timeframe)],
        )
        for row in ladder.rows
    )
    return ReachSheetResponse(
        current_price=current_price,
        rows=rows,
        current_index=ladder.current_index,
        cells=tuple(cells),
        degradations=tuple(degradations),
    )


# --------------------------------------------------------------------- 部品
def _load_bars(
    request: ReachSheetRequest, instances: "Sequence[SheetInstance]", bar_port
) -> "dict[str, tuple[Bar, ...]]":
    """時間足ごとに **1 回だけ** 足を取る（同じ足を 2 回取らない＝発行の畳み込み）。"""
    wanted: "list[str]" = [request.chart_timeframe]
    wanted.extend(instance.timeframe for instance in instances)
    bars: "dict[str, tuple[Bar, ...]]" = {}
    for timeframe in wanted:
        if timeframe in bars:
            continue
        bars[timeframe] = tuple(
            bar_port.bars(dataset_ref=request.dataset_ref, timeframe=timeframe)
        )
    return bars


def _closes_by_time(bars: "Sequence[Bar]") -> "dict[int, float]":
    return {int(bar.time): float(bar.close) for bar in bars}


def _as_level(
    instance: SheetInstance,
    series_name: str,
    points: "tuple[tuple[int, float], ...]",
    roles,
    current_price: float,
) -> "LevelInput | None":
    """系列 1 本をラダー行の水準へ変換する（水準でない・最新値が無い系列は None）。"""
    values = tuple(float(value) for _time, value in points)
    role = roles.role_of(
        instance=instance,
        series_name=series_name,
        values=values,
        reference_price=current_price,
    )
    if role is not SeriesRole.PRICE_LEVEL or not values:
        return None
    latest = values[-1]
    if not math.isfinite(latest):
        return None          # NaN の水準はラダーへ入れない（並びを壊す）
    return LevelInput(
        price=latest,
        timeframe=instance.timeframe,
        label=roles.row_label(instance=instance, series_name=series_name),
    )


def _level_period_touch(
    level_price: float, *, fine_bars: "Sequence[Bar]", period_start: "int | None"
) -> ReachState:
    """ラダー行の到達時間（§6.2 定義 D）。式の唯一源は domain の period_first_touch。"""
    if period_start is None or not fine_bars:
        return ReachState(reached=None, since_time=None, truncated=False)
    return period_first_touch(
        [int(bar.time) for bar in fine_bars],
        [float(bar.high) for bar in fine_bars],
        [float(bar.low) for bar in fine_bars],
        level=float(level_price),
        period_start=int(period_start),
    )


def _build_cell(
    instance: SheetInstance,
    spec: OscillatorSpec,
    series: "Mapping[str, tuple[tuple[int, float], ...]]",
    comparison: "ElapsedComparison | None",
    tails: TailFitCache,
    events_cache: ExcessEventCache,
    history_cache: HistoryStripCache,
) -> OscCell:
    """第 2 表のセル 1 つ（§5.2 / §5.3 / §5.3.3）。"""
    value_points = tuple(series.get(spec.value_series) or ())
    band_points = tuple(series.get(spec.band_high_series) or ())
    band_low_points = tuple(
        series.get(spec.band_low_series) or ()
    ) if spec.band_low_series else ()
    if not value_points:
        return OscCell(
            indicator_id=instance.indicator_id,
            timeframe=instance.timeframe,
            instance_key=instance.key,
            value_series=spec.value_series,
            value=None,
            p=None,
            tail_unscaled=False,
            unavailable_reason=(
                f"系列 {spec.value_series!r} が供給されていない（水準なし・§5.2）"
            ),
            cumulative=spec.cumulative,
        )

    # 突き合わせと因果境界は domain の観測が唯一の所有者（背景色の目盛りと同じ観測を使う）。
    observed = _cq.BandObservations.of(value_points, band_points)
    values, bands = observed.values, observed.bands
    reach = reach_state(list(observed.times), list(values), list(bands),
                        side=LevelSide.ABOVE)
    # 閾値（§5.2・依頼者指示 2026-09-05・一本化承認 2026-09-05）: 到達判定が現在バーで
    #   使っている帯上端と、宣言があれば帯下端。既に発行済みの系列の当該時刻値を読むだけで、
    #   新規の系列発行は 0（複雑度 T-1 を変えない）。突き合わせは band_high と同じ観測の口
    #   （BandObservations）＝因果境界の第 2 定義を作らない。
    band_high = (
        float(bands[-1]) if bands.size and math.isfinite(float(bands[-1])) else None
    )
    band_low = None
    if band_low_points:
        lows = _cq.BandObservations.of(value_points, band_low_points).bands
        if lows.size and math.isfinite(float(lows[-1])):
            band_low = float(lows[-1])
    # 直近区間の読み（§5.2 背景ストリップ・依頼者指示 2026-09-04）。確定履歴だけから決まる
    #   量なので epoch 持ち越し。現在区間はセルの `p` が持ち主（重複して持たない）。
    strip = history_cache.strip_for(
        instance.key, observed, spec, events_cache=events_cache, tails=tails,
    )

    if spec.cumulative:
        return _cumulative_cell(instance, spec, values, reach, comparison, strip,
                                band_high=band_high, band_low=band_low)

    # 順位は**末尾 1 点だけ**発行する（系列版は n−1 個を作って捨てる・レビュー 🔴-1）。
    rank = _cq.in_band_rank_latest(values, spec.window_n)
    history_values, history_bands = observed.history
    # 確定履歴の畳み込みは epoch の中で不変（§7・ISSUE-464 ③）。背景の目盛りと同じ観測を
    #   読むので、持ち越しの口も 1 つにする（同じ観測を 2 人が別々に畳まない）。
    events = events_cache.events_for(
        instance.key, history_values, history_bands, excess=spec.excess
    )
    reading = _cq.p_at(
        value=float(values[-1]),
        band_high=float(bands[-1]),
        q_high=spec.q_high,
        in_band_rank=rank,
        tail=tails.tail_for(instance.key, events, spec.k_events),
        excess=spec.excess,
    )
    return OscCell(
        indicator_id=instance.indicator_id,
        timeframe=instance.timeframe,
        instance_key=instance.key,
        value_series=spec.value_series,
        value=float(values[-1]),
        p=reading.p,
        tail_unscaled=reading.tail_unscaled,
        band_high=band_high,
        band_low=band_low,
        reach=reach,
        history=strip,
        cumulative=spec.cumulative,
    )


def _cumulative_cell(
    instance: SheetInstance,
    spec: OscillatorSpec,
    values: np.ndarray,
    reach: ReachState,
    comparison: "ElapsedComparison | None",
    strip: "tuple[TrailingReading, ...]" = (),
    *,
    band_high: "float | None" = None,
    band_low: "float | None" = None,
) -> OscCell:
    """積み上がる量のセル（§5.3.3: 部分和は**同じ経過**の過去の部分和へ当てる）。

    比較集合が無いときに確定足の分布へ当てて済ませない。それが §5.3.3 のバイアスそのもの
    （1 時間足の最初の 20 分がどんなに活況でも最も冷たい色になる）であり、症状の回避は禁止。
    直近区間の読み（`strip`）は**確定バーの全量**＝経過 100% の部分和なので、このバイアスは
    生じない（現在区間が水準なしでも過去の動きは出せる・§5.2）。
    """
    if comparison is None:
        return OscCell(
            indicator_id=instance.indicator_id,
            timeframe=instance.timeframe,
            instance_key=instance.key,
            value_series=spec.value_series,
            value=float(values[-1]),
            p=None,
            tail_unscaled=False,
            band_high=band_high,
            band_low=band_low,
            reach=reach,
            unavailable_reason=(
                "同じ経過の比較集合が供給されていない（確定足の分布へは当てない・§5.3.3）"
            ),
            history=strip,
            cumulative=True,
        )
    window = np.asarray(
        comparison.pool.partial_sums_at(comparison.completed_units)[-spec.window_n:],
        dtype=np.float64,
    )
    window = window[np.isfinite(window)]
    # 下限は因果窓の規約と同じ量（domain が再公開する単一ソース）。ここへ数字を書くと
    # 第 2 定義になり、片方だけ直したときに無言で食い違う。
    if window.size < _cq.MIN_STAT_OBS:
        return OscCell(
            indicator_id=instance.indicator_id,
            timeframe=instance.timeframe,
            instance_key=instance.key,
            value_series=spec.value_series,
            value=float(comparison.forming_sum),
            p=None,
            tail_unscaled=False,
            band_high=band_high,
            band_low=band_low,
            reach=reach,
            unavailable_reason="同じ経過まで進んだ過去の足が足りない（水準なし・§5.2）",
            history=strip,
            cumulative=True,
        )
    return OscCell(
        indicator_id=instance.indicator_id,
        timeframe=instance.timeframe,
        instance_key=instance.key,
        value_series=spec.value_series,
        value=float(comparison.forming_sum),
        p=_cq.empirical_rank(window, float(comparison.forming_sum)),
        tail_unscaled=False,
        band_high=band_high,
        band_low=band_low,
        reach=reach,
        history=strip,
        cumulative=True,
    )
