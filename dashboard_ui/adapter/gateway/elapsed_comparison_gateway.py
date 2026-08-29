"""§5.3.3 / T-8 積み上がる量の比較集合（同じ経過の過去）を P-1 から組み立てる。

なぜ必要か（§5.3.3 実測）: tick 数は足の中で積み上がるため、形成途中の足を**確定足の分布**へ
当てると必ず極小に出る（1h の経過 10% で分位の中央値 0.000。バイアスは 8 時間足すべてに在る）。
根本原因は「部分和を完全和の分布へ当てている」＝比較集合の取り違えである。

T-8（丸め禁止）: 経過割合を 0.05 / 0.10 刻みで丸める案は不採用（実測 90 パーセンタイルの
分位差 0.10〜0.15 ＝ バイアスの再導入）。素材の最小単位で**厳密に同経過**を突き合わせる。
5m 以上の最小単位は 1m 足であり、経過は形成中の親足で**完了した 1m 本数**である
（形成中の 1m は数えない）。

1m 自身は最小単位の供給を持たない（秒境界を作るティック供給が要る）。ここでは比較集合を
**作らない**。作らなければ usecase が「水準なし」と理由を出す（§5.2・無言の縮退禁止）。
確定足の分布へ当てて埋めるのは §5.3.3 のバイアスそのものであり、症状の回避に当たる。

計算量（§7）: 最小単位の系列は **1 回だけ**発行し、対象の全時間足で共有する（束契約 T-1）。
保持は `ElapsedFractionPool` の prefix cumsum 1 本であり、ティック数に比例しない。
"""
from __future__ import annotations

from typing import Mapping, Sequence

from marketdata.tf_meta import period_start_unix

from dashboard_ui.domain.elapsed_fraction_pool import ElapsedFractionPool
from dashboard_ui.usecase.sheet_models import (
    ElapsedComparison,
    OscillatorSpec,
    SheetInstance,
)

#: 比較の最小単位（tf >= 5m の素材）。
_SUB_TIMEFRAME = "1m"


class ElapsedComparisonGateway:
    """積み上がる量の比較集合を組み立てる（P-1 を読むだけ・新しい計算を発行しない）。"""

    def __init__(self, *, series_port, sub_timeframe: str = _SUB_TIMEFRAME) -> None:
        self._series_port = series_port
        self._sub_timeframe = sub_timeframe

    def comparisons(
        self,
        *,
        dataset_ref: str,
        entries: "Sequence[tuple[SheetInstance, OscillatorSpec]]",
        now_unix: int,
    ) -> "Mapping[tuple[str, str, str, str], ElapsedComparison]":
        """`instance.key -> ElapsedComparison`。作れない instance はキーが無い。

        Raises:
            ValueError: 宣言された系列が最小単位の供給に無いとき（黙って空にしない）。
        """
        targets = [
            (instance, spec)
            for instance, spec in entries
            if spec.cumulative and instance.timeframe != self._sub_timeframe
        ]
        if not targets:
            return {}

        # 最小単位の系列は (指標, variant, params) ごとに 1 回だけ発行する。足の数だけ
        # 呼ぶと、同じ 1m 系列を 8 回計算することになる（T-1 の畳み込みはここが持つ）。
        folded: "dict[tuple[str, str, str], tuple[tuple[int, float], ...]]" = {}
        units_by_instance: "dict[tuple[str, str, str, str], ElapsedComparison]" = {}
        for instance, spec in targets:
            fold_key = (instance.indicator_id, instance.variant, instance.params_key)
            if fold_key not in folded:
                folded[fold_key] = self._sub_units(dataset_ref, instance, spec)
            points = folded[fold_key]
            comparison = _comparison_of(points, instance.timeframe, int(now_unix))
            if comparison is not None:
                units_by_instance[instance.key] = comparison
        return units_by_instance

    # ------------------------------------------------------------------ 内部
    def _sub_units(
        self, dataset_ref: str, instance: SheetInstance, spec: OscillatorSpec
    ) -> "tuple[tuple[int, float], ...]":
        """最小単位の系列（P-1 が同一キーを 1 回しか計算しないので、足ごとに再発行しない）。"""
        series = self._series_port.full_series(
            indicator_id=instance.indicator_id,
            variant=instance.variant,
            params=instance.params,
            dataset_ref=dataset_ref,
            timeframe=self._sub_timeframe,
        )
        points = series.get(spec.value_series)
        if points is None:
            raise ValueError(
                f"最小単位の系列が供給されていません: series={spec.value_series!r} "
                f"timeframe={self._sub_timeframe!r}"
            )
        return tuple(points)


def _comparison_of(
    points: "Sequence[tuple[int, float]]", timeframe: str, now_unix: int
) -> "ElapsedComparison | None":
    """最小単位の列を親足でまとめ、確定した過去と形成中の部分和へ分ける。"""
    forming_sub_unit = period_start_unix(now_unix, _SUB_TIMEFRAME)
    completed = [
        (int(time), float(value))
        for time, value in points
        if period_start_unix(int(time), _SUB_TIMEFRAME) < forming_sub_unit
    ]
    if not completed:
        return None

    parents = [period_start_unix(time, timeframe) for time, _ in completed]
    forming_parent = period_start_unix(now_unix, timeframe)
    past = [
        (parent, value)
        for parent, (_time, value) in zip(parents, completed)
        if parent != forming_parent
    ]
    current = [
        value
        for parent, (_time, value) in zip(parents, completed)
        if parent == forming_parent
    ]
    if not past or not current:
        # 過去の親足が無い（当てる先が無い）／形成中の親足に完了単位が無い（経過 0）。
        return None
    return ElapsedComparison(
        pool=ElapsedFractionPool.from_units(
            [parent for parent, _ in past], [value for _, value in past]
        ),
        completed_units=len(current),
        forming_sum=float(sum(current)),
    )
