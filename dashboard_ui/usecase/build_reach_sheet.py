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
    非自明な到達時刻が出る（現在位置から向きを決めると、どの行も常に未到達になり定義 A が死ぬ）。
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
from dashboard_ui.domain.price_ladder import LevelInput, build_ladder
from dashboard_ui.domain.reach import LevelSide, ReachState, reach_state
from dashboard_ui.usecase.sheet_models import (
    Degradation,
    ElapsedComparison,
    LadderRow,
    OscCell,
    OscillatorSpec,
    ReachSheetRequest,
    ReachSheetResponse,
    SeriesRole,
    SheetInstance,
    UpdateGranularity,
)

class TailFitCache:
    """GPD の当てはめを**イベント確定のときだけ**行うためのキャッシュ（§7）。

    `p` を求めるたびに当てはめ直さない。エピソードが閉じないバーでは確定観測列が伸びないので、
    当てはめ回数は 0 になる。当てはめ自体は最尤推定（Nelder–Mead）で安くはなく、セル数・行数・
    ティック数に比例して呼ぶと ISSUE-450 と同型の浪費になる。
    """

    def __init__(self) -> None:
        self._entries: "dict[tuple, tuple[int, object]]" = {}

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
        cached = self._entries.get(key)
        if cached is not None and cached[0] == signature:
            return cached[1]
        fitted = _cq.fit_tail(events, k_events=k_events)
        self._entries[key] = (signature, fitted)
        return fitted


def build_reach_sheet(
    request: ReachSheetRequest,
    *,
    series_port,
    bar_port,
    roles,
    elapsed_comparisons: "Mapping[tuple[str, str, str, str], ElapsedComparison] | None" = None,
    tail_fit_cache: "TailFitCache | None" = None,
) -> ReachSheetResponse:
    """段 1 のシートを組み立てる。

    Raises:
        ValueError: 表示時間足の足が 1 本も供給されないとき（現在値が決まらない）。
    """
    comparisons = dict(elapsed_comparisons or {})
    tails = tail_fit_cache if tail_fit_cache is not None else TailFitCache()
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
    cells: "list[OscCell]" = []
    degradations: "list[Degradation]" = []

    for instance in instances:
        series = dict(
            series_port.full_series(
                indicator_id=instance.indicator_id,
                variant=instance.variant,
                params=instance.params,
                dataset_ref=request.dataset_ref,
                timeframe=instance.timeframe,
            )
        )
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
                _build_cell(instance, spec, series, comparisons.get(instance.key), tails)
            )
        closes = _closes_by_time(bars_by_timeframe.get(instance.timeframe) or ())
        for series_name, points in series.items():
            level = _as_level(
                instance, series_name, tuple(points), roles, current_price
            )
            if level is None:
                continue
            levels.append(level)
            reach_by_row[level.row_key] = _level_reach(tuple(points), closes)
            instance_by_row[level.row_key] = instance.key

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


def _level_reach(
    points: "tuple[tuple[int, float], ...]", closes: "Mapping[int, float]"
) -> ReachState:
    """水準系列と価格系列を時刻で突き合わせ、定義 A の到達時刻を導く（§6.2 / §6.3）。"""
    times: "list[int]" = []
    values: "list[float]" = []
    levels: "list[float]" = []
    for time, level in points:
        close = closes.get(int(time))
        if close is None:
            continue
        times.append(int(time))
        values.append(float(close))
        levels.append(float(level))
    return reach_state(times, values, levels, side=LevelSide.ABOVE)


def _build_cell(
    instance: SheetInstance,
    spec: OscillatorSpec,
    series: "Mapping[str, tuple[tuple[int, float], ...]]",
    comparison: "ElapsedComparison | None",
    tails: TailFitCache,
) -> OscCell:
    """第 2 表のセル 1 つ（§5.2 / §5.3 / §5.3.3）。"""
    value_points = tuple(series.get(spec.value_series) or ())
    band_points = tuple(series.get(spec.band_high_series) or ())
    if not value_points:
        return OscCell(
            indicator_id=instance.indicator_id,
            timeframe=instance.timeframe,
            value=None,
            p=None,
            tail_unscaled=False,
            unavailable_reason=(
                f"系列 {spec.value_series!r} が供給されていない（水準なし・§5.2）"
            ),
        )

    # 突き合わせと因果境界は domain の観測が唯一の所有者（背景色の目盛りと同じ観測を使う）。
    observed = _cq.BandObservations.of(value_points, band_points)
    values, bands = observed.values, observed.bands
    reach = reach_state(list(observed.times), list(values), list(bands),
                        side=LevelSide.ABOVE)

    if spec.cumulative:
        return _cumulative_cell(instance, spec, values, reach, comparison)

    # 順位は**末尾 1 点だけ**発行する（系列版は n−1 個を作って捨てる・レビュー 🔴-1）。
    rank = _cq.in_band_rank_latest(values, spec.window_n)
    history_values, history_bands = observed.history
    events = _cq.excess_event_history(history_values, history_bands,
                                      excess=spec.excess)
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
        value=float(values[-1]),
        p=reading.p,
        tail_unscaled=reading.tail_unscaled,
        reach=reach,
    )


def _cumulative_cell(
    instance: SheetInstance,
    spec: OscillatorSpec,
    values: np.ndarray,
    reach: ReachState,
    comparison: "ElapsedComparison | None",
) -> OscCell:
    """積み上がる量のセル（§5.3.3: 部分和は**同じ経過**の過去の部分和へ当てる）。

    比較集合が無いときに確定足の分布へ当てて済ませない。それが §5.3.3 のバイアスそのもの
    （1 時間足の最初の 20 分がどんなに活況でも最も冷たい色になる）であり、症状の回避は禁止。
    """
    if comparison is None:
        return OscCell(
            indicator_id=instance.indicator_id,
            timeframe=instance.timeframe,
            value=float(values[-1]),
            p=None,
            tail_unscaled=False,
            reach=reach,
            unavailable_reason=(
                "同じ経過の比較集合が供給されていない（確定足の分布へは当てない・§5.3.3）"
            ),
        )
    window = np.asarray(
        comparison.pool.partial_sums_at(comparison.completed_units)[-spec.window_n:],
        dtype=np.float64,
    )
    window = window[np.isfinite(window)]
    if window.size < 2:
        return OscCell(
            indicator_id=instance.indicator_id,
            timeframe=instance.timeframe,
            value=float(comparison.forming_sum),
            p=None,
            tail_unscaled=False,
            reach=reach,
            unavailable_reason="同じ経過まで進んだ過去の足が足りない（水準なし・§5.2）",
        )
    return OscCell(
        indicator_id=instance.indicator_id,
        timeframe=instance.timeframe,
        value=float(comparison.forming_sum),
        p=_cq.empirical_rank(window, float(comparison.forming_sum)),
        tail_unscaled=False,
        reach=reach,
    )
