"""UC-02（段 2・ティック）: 価格投影の当てはめ契機を epoch で判定する。

§5.5.4 の実測: `v(C)` はメビウスなので、**係数を決めた後の価格評価は前進評価を一切呼ばない**
（ラダー 82 行の評価で発行 0 回。参照実装を直接呼ぶなら 2,050 回）。しかも係数は現在の価格に
依存せず、**前バーの状態と走行 H / L だけ**で決まる。

    epoch := (bar_time, run_hi, run_lo)

| 契機 | 係数の再当てはめ | 背景色の再計算 |
|---|---|---|
| バー確定 | 要 | 要 |
| 走行 H / L が更新された | 要 | 要 |
| 上記以外のティック | **不要（0 回）** | 要（閉形式のみ） |
| ラダーの行が増減した | **不要（0 回）** | 要（閉形式のみ） |

実測（§9-8・survey-facts）: H/L 更新ティック率は bid 7.8% / mid 13.0%。ティックの 87〜92% は
発行 0 回になる。ここを取り違えると ISSUE-450 と同型の「作ってから捨てる」浪費になる。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Mapping, Sequence

from dashboard_ui.domain.bar import Bar, RunningExtreme
from dashboard_ui.domain.price_value_map import PriceValueMap
from dashboard_ui.usecase.sheet_models import SheetInstance

#: 無限端の区分を伸ばす幅の下限（参照実装 probe_heatmap.py:189 の `max(H0 - L0, 1.0)`）。
_MIN_SPAN: float = 1.0


@dataclass(frozen=True)
class Epoch:
    """当てはめが有効な期間の識別子。**終値は含めない**（係数は `C` に依存しない）。"""

    bar_time: int
    running: RunningExtreme

    @classmethod
    def of(cls, bar: Bar) -> "Epoch":
        return cls(bar_time=int(bar.time), running=RunningExtreme.of(bar))


@dataclass(frozen=True)
class ProjectionCache:
    """epoch とその epoch で決めた係数の束。"""

    epoch: "Epoch | None"
    maps: "Mapping[tuple[str, str, str, str], PriceValueMap]" = field(
        default_factory=dict
    )


def refresh_projection(
    cache: "ProjectionCache | None",
    *,
    forming_bar: "Bar | None",
    instances: "Sequence[SheetInstance]",
    dataset_ref: str,
    forward_port,
    registry,
    prev_values: "Mapping[tuple[str, str, str, str], float] | None" = None,
) -> ProjectionCache:
    """必要なときだけ係数を当て直す。

    epoch が不変なら**渡されたキャッシュをそのまま返す**（前進評価の発行は 0 回）。
    価格の評価・並び替え・背景色は閉形式だけで済むため、ここを通らない。

    Args:
        cache: 直前のキャッシュ（初回は None）。
        forming_bar: 形成中の足。None なら投影の対象が無い。
        instances: 対象インスタンス。`registry.resolve` が None を返すものは対象外
            （§5.5.1: 除外を列挙で書かず、`breakpoints()` を提供できない形で現れる）。
        forward_port: P-3。前進評価はこの面からしか発行しない。
        registry: P-4 のレジストリ。
        prev_values: 区分の境目に要る前バーの適用価格（指標側が使う。無ければ None）。
    """
    if forming_bar is None:
        return ProjectionCache(epoch=None, maps={})

    epoch = Epoch.of(forming_bar)
    if cache is not None and cache.epoch == epoch:
        return cache

    previous = dict(prev_values or {})
    span = max(forming_bar.high - forming_bar.low, _MIN_SPAN)
    maps: "dict[tuple[str, str, str, str], PriceValueMap]" = {}
    for instance in instances:
        source = registry.resolve(instance.indicator_id)
        if source is None:
            continue
        cuts = source.breakpoints(
            bar=forming_bar,
            params=instance.params,
            prev_value=previous.get(instance.key),
        )
        maps[instance.key] = PriceValueMap.fit(
            _forward_for(instance, dataset_ref, forward_port), cuts, span=span
        )
    return ProjectionCache(epoch=epoch, maps=maps)


def _forward_for(instance: SheetInstance, dataset_ref: str, forward_port):
    """P-3 を `forward(C) -> value` の形へ束ねる（指標の core は 1 行も変えない）。"""

    def forward(close: float) -> float:
        return float(
            forward_port.value_at_close(
                indicator_id=instance.indicator_id,
                variant=instance.variant,
                params=instance.params,
                dataset_ref=dataset_ref,
                timeframe=instance.timeframe,
                close=close,
            )
        )

    return forward
