"""`POST /reach_sheet` の Controller（arch-spec §9 の JSON 契約）。

責務は**形の変換と失敗の翻訳だけ**である。`p` の算出・並び替え・到達判定はすべて usecase /
domain が持つ（フロントも再計算しない＝単一ソース）。シリアライズは
`dashboard_ui.usecase.sheet_models` のフィールド名をそのまま使い、別名を発明しない。

段の対応（§7）:
    `mode="full"`（バー確定）… 係数を当て直す。
    `mode="tick"` … epoch `(bar_time, run_hi, run_lo)` が不変なら前進評価の発行は **0**。
    実測（survey-facts）: H/L 更新ティック率は bid 7.8% / mid 13.0% であり、ティックの
    87〜92% は発行 0 回になる。ここを取り違えると ISSUE-450 と同型の浪費になる。

係数の当てはめは**時間足ごと**に行う。区分の境目は当該足の走行 H / L で決まるので、表示足の
形成中バーで 1h の instance を当てはめると別の足の極値で区分を切ることになる。
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Callable, Mapping, Sequence

from dashboard_ui.adapter.quantile_scale_builder import quantile_scale_of
from dashboard_ui.domain.horizon import Horizon
from dashboard_ui.usecase.build_reach_sheet import (
    ExcessEventCache,
    TailFitCache,
    build_reach_sheet,
)
from dashboard_ui.usecase.project_quantiles_to_price import (
    InstanceProjection,
    project_quantiles_to_price,
)
from dashboard_ui.usecase.sheet_models import (
    Degradation,
    OscillatorSpec,
    ReachSheetRequest,
    SheetInstance,
    UpdateGranularity,
)
from dashboard_ui.usecase.sheet_ports import (
    ForwardEvaluationUnavailable,
    SeriesSupplyUnavailable,
)
from dashboard_ui.usecase.update_reach_sheet import ProjectionCache, refresh_projection

#: 受け付ける更新モード（§7 の 2 段）。
_MODES: "frozenset[str]" = frozenset({"full", "tick"})


class RequestError(ValueError):
    """入力の形が契約に合っていない（呼び出し側の誤り）。"""


@dataclass
class SheetState:
    """要求をまたいで持ち越す状態（epoch の中で不変な量）。

    サーバは素材の鮮度のために**要求ごとに口（gateway）を組み直す**（古い足を配らない）。
    一方、係数の当てはめ契機は epoch `(bar_time, run_hi, run_lo)` で決まり、口の寿命とは
    無関係である。両者を分けるためにここへ置く（§7 段 2: epoch 不変なら発行 0 回）。

    帯外イベント履歴（ISSUE-464 ③）も同じ理由で同じ場所へ置く: 確定履歴だけから決まる量で
    あり、形成中バーが動いても変わらない。
    """

    tails: TailFitCache = field(default_factory=TailFitCache)
    events: ExcessEventCache = field(default_factory=ExcessEventCache)
    projections: "dict[str, ProjectionCache]" = field(default_factory=dict)
    #: 分位水準到達価格の往復検証の持ち越し（instance キー → (epoch, 帯値, 検証済み価格)）。
    #: 検証は前進評価 1 回を要するため、同じ epoch・同じ帯値では再検証しない
    #: （§7 段 2: epoch 不変なら発行 0 回、を本表示でも守るための口）。
    level_prices: "dict[tuple, tuple[object, float, float | None]]" = field(
        default_factory=dict
    )


@dataclass(frozen=True)
class _Parsed:
    """Input Model（束＝instances は要求の一部として届く・T-2）。"""

    dataset_ref: str
    chart_timeframe: str
    mode: str
    instances: "tuple[SheetInstance, ...]"


class ReachSheetController:
    """P-1〜P-4 と usecase を束ねて 1 枚のシートを返す。

    Args:
        series_port: P-1。`bar_port`: P-2。`forward_port`: P-3。`registry`: P-4。
        roles: 役割宣言（水準判定・行ラベル・第 2 表のセル宣言）。
        elapsed_gateway: 積み上がる量の比較集合（§5.3.3）。
        is_intrabar_capable: 足内更新の可否（増分器の宣言有無）。無言の縮退を作らないため、
            できない instance は応答の `degradations` に必ず現れる。
        state: 要求をまたいで持ち越す状態（省略時はこの controller 専用の状態を持つ）。
    """

    def __init__(
        self,
        *,
        series_port,
        bar_port,
        roles,
        registry,
        forward_port,
        elapsed_gateway,
        is_intrabar_capable: Callable[[str, str, "Mapping[str, object]"], bool],
        state: "SheetState | None" = None,
    ) -> None:
        self._series_port = series_port
        self._bar_port = bar_port
        self._roles = roles
        self._registry = registry
        self._forward_port = forward_port
        self._elapsed_gateway = elapsed_gateway
        self._is_intrabar_capable = is_intrabar_capable
        self._state = state if state is not None else SheetState()

    # ------------------------------------------------------------------ 入口
    def handle(self, request: "Mapping[str, Any]") -> "dict[str, Any]":
        """要求 1 件を処理する（例外を素通しして 500 にしない）。"""
        try:
            parsed = self._parse(request)
        except RequestError as error:
            return _failure("validation", str(error))
        try:
            return self._build(parsed)
        except (ValueError, KeyError) as error:
            return _failure("supply", str(error))

    # ------------------------------------------------------------------ 解釈
    def _parse(self, request: "Mapping[str, Any]") -> _Parsed:
        if not isinstance(request, Mapping):
            raise RequestError("要求は JSON オブジェクトである必要があります")
        dataset_ref = request.get("dataset_ref")
        if not isinstance(dataset_ref, str) or not dataset_ref:
            raise RequestError("dataset_ref が必要です")
        chart_timeframe = request.get("chart_timeframe")
        if not isinstance(chart_timeframe, str) or not chart_timeframe:
            raise RequestError("chart_timeframe が必要です")
        mode = request.get("mode", "full")
        if mode not in _MODES:
            raise RequestError(f"mode は {sorted(_MODES)} のいずれかです: {mode!r}")
        raw = request.get("instances")
        if not isinstance(raw, Sequence) or isinstance(raw, (str, bytes)):
            raise RequestError("instances は配列である必要があります")
        return _Parsed(
            dataset_ref=dataset_ref,
            chart_timeframe=chart_timeframe,
            mode=str(mode),
            instances=tuple(
                self._instance_of(entry, chart_timeframe) for entry in raw
            ),
        )

    def _instance_of(self, entry: Any, chart_timeframe: str) -> SheetInstance:
        if not isinstance(entry, Mapping):
            raise RequestError("instances の要素は JSON オブジェクトである必要があります")
        indicator_id = entry.get("indicator_id")
        if not isinstance(indicator_id, str) or not indicator_id:
            raise RequestError("instances[].indicator_id が必要です")
        variant = str(entry.get("variant") or "default")
        params = entry.get("params") or {}
        if not isinstance(params, Mapping):
            raise RequestError("instances[].params は JSON オブジェクトである必要があります")
        # カタログに無いキー（撤去済みパラメータの残骸。保存済みテンプレート由来）を
        #   入口で除去する。残すと実質同一の instance が別キーに割れ、`unique_instances` で
        #   畳まれず行ラベルの §11-2 一意性検査と衝突して全滅 400 になる（2026-08-30 実測:
        #   `wait_for_close` だけが違う MA 2 本）。カタログに無い指標は判定材料が無いので
        #   そのまま通す（発明しない）。
        known = self._roles.known_params(indicator_id=indicator_id)
        settings = {
            k: v for k, v in params.items() if known is None or k in known
        }
        if entry.get("timeframe"):
            settings["timeframe"] = entry["timeframe"]
        return SheetInstance.of(
            indicator_id, variant, settings, chart_timeframe=chart_timeframe,
            intrabar_capable=bool(
                self._is_intrabar_capable(indicator_id, variant, dict(params))
            ),
        )

    # ------------------------------------------------------------------ 構築
    def _build(self, parsed: _Parsed) -> "dict[str, Any]":
        request = ReachSheetRequest(
            dataset_ref=parsed.dataset_ref,
            instances=parsed.instances,
            chart_timeframe=parsed.chart_timeframe,
        )
        instances = request.unique_instances()
        chart_bars = self._bar_port.bars(
            dataset_ref=parsed.dataset_ref, timeframe=parsed.chart_timeframe
        )
        if not chart_bars:
            raise ValueError(
                f"表示時間足の足が供給されていません: timeframe={parsed.chart_timeframe!r}"
            )
        now_unix = int(chart_bars[-1].time)

        # 供給不能な instance はここで畳まず素通しする: 除外の判断と縮退の記録は
        #   `build_reach_sheet` が一元的に持つ（§5.5.1・二重記録を作らない）。ここは
        #   投影・比較の材料からその instance を外すだけでよい。
        series_by_key: "dict[tuple, dict]" = {}
        for instance in instances:
            try:
                series_by_key[instance.key] = dict(
                    self._series_port.full_series(
                        indicator_id=instance.indicator_id, variant=instance.variant,
                        params=instance.params, dataset_ref=parsed.dataset_ref,
                        timeframe=instance.timeframe,
                    )
                )
            except SeriesSupplyUnavailable:
                continue
        instances = [
            instance for instance in instances if instance.key in series_by_key
        ]
        specs = {
            instance.key: self._roles.oscillator_spec(
                instance=instance,
                series_names=frozenset(series_by_key[instance.key]),
            )
            for instance in instances
        }
        comparisons = self._elapsed_gateway.comparisons(
            dataset_ref=parsed.dataset_ref,
            entries=[
                (instance, specs[instance.key])
                for instance in instances
                if specs[instance.key] is not None
            ],
            now_unix=now_unix,
        )
        sheet = build_reach_sheet(
            request,
            series_port=self._series_port,
            bar_port=self._bar_port,
            roles=self._roles,
            elapsed_comparisons=comparisons,
            tail_fit_cache=self._state.tails,
            event_cache=self._state.events,
        )
        projections, unprojectable, level_prices = self._projections_of(
            parsed, instances, series_by_key, specs, now_unix
        )
        background = project_quantiles_to_price(sheet.rows, projections=projections)
        degradations = [*sheet.degradations, *_unprojectable_degradations(unprojectable)]
        return {
            "ok": True,
            "current_price": float(sheet.current_price),
            "rows": [
                _row_json(row, horizon_p)
                for row, horizon_p in zip(sheet.rows, background)
            ],
            "current_index": int(sheet.current_index),
            "cells": [
                _cell_json(cell, level_prices.get(cell.instance_key))
                for cell in sheet.cells
            ],
            "degradations": [_degradation_json(entry) for entry in degradations],
        }

    # -------------------------------------------------------------- 価格投影
    def _projections_of(
        self,
        parsed: _Parsed,
        instances: "Sequence[SheetInstance]",
        series_by_key: "Mapping[tuple, Mapping[str, tuple]]",
        specs: "Mapping[tuple, OscillatorSpec | None]",
        now_unix: int,
    ) -> "tuple[list[InstanceProjection], dict[tuple, str], dict[tuple, float]]":
        """§5.5 の係数を（時間足ごとに）用意し、投影材料・**出せなかった理由**・
        分位水準（帯上端）に達する価格（依頼者指示 2026-08-30・第 2 表セルへ表示）を返す。

        価格は既存の係数（value_map）の閉形式逆写像 `price_at` だけで求める——前進評価の
        追加発行は 0 回（§5.5.4 の不変条件。ラダー背景と同じ係数を使い回す）。"""
        if parsed.mode == "full":
            self._state.projections.clear()

        maps: "dict[tuple, Any]" = {}
        unprojectable: "dict[tuple, str]" = {}
        for timeframe in sorted({instance.timeframe for instance in instances}):
            group = [
                instance for instance in instances if instance.timeframe == timeframe
            ]
            cache = refresh_projection(
                self._covering_cache(timeframe, group),
                forming_bar=self._bar_port.forming_bar(
                    dataset_ref=parsed.dataset_ref, timeframe=timeframe,
                    now_unix=now_unix,
                ),
                instances=group,
                dataset_ref=parsed.dataset_ref,
                forward_port=self._forward_port,
                registry=self._registry,
                prev_values=self._prev_values(parsed.dataset_ref, timeframe, group),
            )
            self._state.projections[timeframe] = cache
            maps.update(cache.maps)
            unprojectable.update(cache.unprojectable)

        projections: "list[InstanceProjection]" = []
        level_prices: "dict[tuple, float]" = {}
        for instance in instances:
            value_map = maps.get(instance.key)
            spec = specs.get(instance.key)
            if value_map is None or spec is None:
                continue
            scale = quantile_scale_of(
                spec=spec, series=series_by_key[instance.key], tails=self._state.tails,
                key=instance.key, events=self._state.events,
            )
            if scale is None:
                continue
            projections.append(
                InstanceProjection(
                    timeframe=instance.timeframe, value_map=value_map, scale=scale
                )
            )
            # 分位水準に達する価格（依頼者指示 2026-08-30・上下 2 値は同日承認）。
            #   上帯 = scale.band_high（q_high）・下帯 = band_low 系列の末尾値（q_low）。
            #   `level` は第 1 表の水準列と同じ語彙（q95 / q5・依頼者指摘 2026-08-30:
            #   矢印だけでは判断に迷う→どの分位かを名前で示す）。
            sides: "dict[str, dict | None]" = {"q_high": None, "q_low": None}
            if math.isfinite(float(scale.band_high)):
                price_high = self._level_price_of(
                    instance, parsed.dataset_ref, value_map,
                    band=float(scale.band_high), side="q_high",
                )
                if price_high is not None:
                    sides["q_high"] = {
                        "price": float(price_high),
                        "level": _quantile_label(spec.q_high),
                    }
            band_low = _latest_of(
                series_by_key[instance.key].get(spec.band_low_series or "")
            )
            if band_low is not None:
                price_low = self._level_price_of(
                    instance, parsed.dataset_ref, value_map,
                    band=band_low, side="q_low",
                )
                if price_low is not None:
                    sides["q_low"] = {
                        "price": float(price_low),
                        "level": _quantile_label(spec.q_low),
                    }
            if sides["q_high"] is not None or sides["q_low"] is not None:
                level_prices[instance.key] = sides
        return projections, unprojectable, level_prices

    def _level_price_of(
        self, instance, dataset_ref: str, value_map, *, band: float, side: str
    ) -> "float | None":
        """帯上端 `band` に達する価格。

        1. 探針範囲の内側なら閉形式の逆写像だけで確定（発行 0 回・当てはめ実測済みの範囲）。
        2. 外側は**名目区分へ外挿した候補を前進評価 1 回の往復で実測検証**してから使う
           （§10 の発散例のような外挿の作り話を表示しない）。休場等でバーの値幅が狭いと
           探針範囲が数点しかなく、帯到達価格はほぼ常に外側になる（依頼者報告 2026-08-30
           「表示されていない価格がある」の原因）。
        3. 検証結果は (epoch, 帯値) で持ち越す＝同じ epoch のティックでは発行 0 回のまま。
        4. 価格の定義域は正（JP225）。非正の解は「この帯にはこの価格域では到達しない」＝None。
        """
        trusted = value_map.price_at(band)
        if trusted is not None:
            return trusted if trusted > 0.0 else None
        epoch = getattr(
            self._state.projections.get(instance.timeframe), "epoch", None
        )
        cache_key = (*instance.key, side)   # 上下は別スロット（同居させると毎要求再検証になる）。
        cached = self._state.level_prices.get(cache_key)
        if cached is not None and cached[0] == epoch and cached[1] == band:
            return cached[2]
        price: "float | None" = value_map.price_at_nominal(band)
        if price is not None and price > 0.0:
            try:
                round_trip = float(
                    self._forward_port.value_at_close(
                        indicator_id=instance.indicator_id,
                        variant=instance.variant,
                        params=instance.params,
                        dataset_ref=dataset_ref,
                        timeframe=instance.timeframe,
                        close=price,
                    )
                )
            except ForwardEvaluationUnavailable:
                price = None
            else:
                tolerance = 1e-6 * max(1.0, abs(band))
                if not (
                    math.isfinite(round_trip) and abs(round_trip - band) <= tolerance
                ):
                    price = None
        else:
            price = None
        self._state.level_prices[cache_key] = (epoch, band, price)
        return price

    def _covering_cache(
        self, timeframe: str, group: "Sequence[SheetInstance]"
    ) -> "ProjectionCache | None":
        """その足の持ち越しキャッシュ（束が増えていれば捨てて当て直させる）。

        epoch は「素材が変わったか」を答える量であって「何を表示するか」ではない。束の追加を
        epoch 不変で素通しすると、その instance だけ背景が塗られないまま次のバー確定まで残る
        （無言の欠落）。逆に、束が同じで epoch も同じなら発行は 0 回のままである。
        """
        cache = self._state.projections.get(timeframe)
        if cache is None:
            return None
        wanted = {
            instance.key
            for instance in group
            if self._registry.resolve(instance.indicator_id) is not None
        }
        # 「覆えている」は**答えが出ている**ことであり、係数が在ることではない。前進評価できない
        # instance のキーは maps に永遠に現れないので、ここで数え落とすとキャッシュを毎回捨てて
        # 同じ時間足を丸ごと当て直すことになる（epoch 不変でも発行が毎ティック起きる＝
        # ISSUE-450 と同型。出力は正しいままなので状態検証では落ちない）。
        covered = set(cache.maps) | set(cache.unprojectable)
        return None if wanted - covered else cache

    def _prev_values(
        self, dataset_ref: str, timeframe: str, instances: "Sequence[SheetInstance]"
    ) -> "dict[tuple, float]":
        """上下分岐の高さ（前バーの適用価格）。要らない指標は None を返すので入らない。

        `bars[-2]` を取るのは、P-2 の `bars()` の**末尾が形成中の足**（`forming_bar()` と
        同一物）だからである。参照実装 `tools/measure/issue449/probe_heatmap.py:131-132` も
        同じ位置を取る（`x_prev = (h[-2] + l[-2] + c[-2]) / 3`。同 `:128` の `H0/L0 = h[-1]/l[-1]`
        が形成中バーの走行極値であることと対になっている）。`bars[-1]` を使うと、まだ動く
        値を「前バーの確定値」として区分の境目に据えることになる。
        """
        bars = self._bar_port.bars(dataset_ref=dataset_ref, timeframe=timeframe)
        if len(bars) < 2:
            return {}
        previous = bars[-2]
        values: "dict[tuple, float]" = {}
        for instance in instances:
            source = self._registry.resolve(instance.indicator_id)
            if source is None:
                continue
            value = source.previous_value(bar=previous, params=instance.params)
            if value is not None:
                values[instance.key] = float(value)
        return values


# ------------------------------------------------------------------ 直列化
def _unprojectable_degradations(
    reasons: "Mapping[tuple, str]",
) -> "list[Degradation]":
    """価格投影を出せなかった instance を縮退として持ち出す（無言で外さない・§7）。

    `UpdateGranularity.NONE` は「バー確定でも回復しない」を意味し、UpdateGranularity.BAR_CLOSE（ティックでは
    更新されないが確定では更新される）とは別物である。同じ値にすると、回復しない欠落が
    「次の確定で直る」と読める（無言の縮退と同じ害になる）。
    """
    return [
        Degradation(
            instance_key=key,
            granularity=UpdateGranularity.NONE,
            reason=reason,
        )
        for key, reason in reasons.items()
    ]


def _failure(kind: str, message: str) -> "dict[str, Any]":
    return {"ok": False, "error": {"type": kind, "message": message}}


def _row_json(row, horizon_p: "Mapping[Horizon, float | None]") -> "dict[str, Any]":
    return {
        "price": float(row.price),
        "timeframe": row.timeframe,
        "label": row.label,
        "distance": float(row.distance),
        "gap_to_previous": (
            None if row.gap_to_previous is None else float(row.gap_to_previous)
        ),
        # 地平は宣言順（短い順）で並べる。集合のままでは順序が不定になる。
        "horizon_marks": [
            horizon.value for horizon in Horizon if horizon in row.horizon_marks
        ],
        "reach": _reach_json(row.reach),
        "horizon_p": _horizon_json(horizon_p),
        # `degradations[].instance_key` と同じ形。行から「この行はバー確定でしか動かない」
        # を辿れるようにする（§7: 更新粒度の差を隠さない）。
        "instance_key": (
            None if row.instance_key is None else list(row.instance_key)
        ),
        # 表示 3 分割（依頼者指示 2026-08-30: 指標名 / 期間 / ソース）。識別は label が担う。
        "naming": (None if row.naming is None else dict(row.naming)),
    }


def _quantile_label(quantile: "float | None") -> "str | None":
    """分位の表示名（第 1 表の水準列・系列名 `_q{pct}` と同じ丸め＝語彙を増やさない）。"""
    if quantile is None:
        return None
    return f"q{int(round(float(quantile) * 100))}"


def _latest_of(points) -> "float | None":
    """系列の末尾値（系列なし・空・非有限は None）。"""
    if not points:
        return None
    value = float(points[-1][1])
    return value if math.isfinite(value) else None


def _cell_json(cell, level_prices: "Mapping[str, float | None] | None" = None) -> "dict[str, Any]":
    sides = level_prices or {}
    return {
        "indicator_id": cell.indicator_id,
        "timeframe": cell.timeframe,
        "value": None if cell.value is None else float(cell.value),
        "p": None if cell.p is None else float(cell.p),
        "tail_unscaled": bool(cell.tail_unscaled),
        "reach": None if cell.reach is None else _reach_json(cell.reach),
        "unavailable_reason": cell.unavailable_reason,
        # 分位水準に達する価格（依頼者指示 2026-08-30・上下 2 値は同日承認。§5.5 の係数の
        #   閉形式逆写像＋往復検証。逆算不能＝tickvol 等・検証不成立は None）。各側は
        #   {price, level}（level は第 1 表の水準列と同じ分位名・矢印だけでは判断に迷うため）。
        "level_prices": {
            "q_high": sides.get("q_high"),
            "q_low": sides.get("q_low"),
        },
    }


def _degradation_json(entry) -> "dict[str, Any]":
    return {
        "instance_key": list(entry.instance_key),
        "granularity": entry.granularity.value,
        "reason": entry.reason,
    }


def _reach_json(reach) -> "dict[str, Any]":
    return {
        "reached": reach.reached,
        "since_time": None if reach.since_time is None else int(reach.since_time),
        "truncated": bool(reach.truncated),
    }


def _horizon_json(horizon_p: "Mapping[Horizon, float | None]") -> "dict[str, Any]":
    values: "dict[str, Any]" = {}
    for horizon in Horizon:
        value = horizon_p.get(horizon)
        values[horizon.value] = None if value is None else float(value)
    return values
