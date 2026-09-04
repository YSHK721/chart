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

比較集合の持ち越し（ISSUE-464 ①・§7 の epoch 分離をこの面へも適用する）:
    比較集合は「最小単位の確定系列」と「現在が最小単位のどの周期に居るか」だけで決まる。
    したがって **1m の周期が進むまで不変**である。にもかかわらず対象の時間足ごとに 1m 全点の
    周期始端を求め直していた（実測 2026-08-30・8 足束 1 要求: 42,023 回 / 1,438 ms
    ＝ 3,000 点 × 2 回 × 7 足）。出力は正しいままなので状態検証では原理的に落ちない。
    確定素材と同じストア（:class:`MaterialStore`）へ併置し、版が変わったら丸ごと捨てる。
"""
from __future__ import annotations

from typing import Mapping, Sequence

from marketdata.tf_meta import period_start_unix

from dashboard_ui.adapter.gateway.material_store import MaterialStore
from dashboard_ui.domain.elapsed_fraction_pool import ElapsedFractionPool
from dashboard_ui.domain.material_version import fingerprint_of
from dashboard_ui.usecase.sheet_models import (
    ElapsedComparison,
    OscillatorSpec,
    SheetInstance,
)

#: 比較の最小単位（tf >= 5m の素材）。
_SUB_TIMEFRAME = "1m"


class ElapsedComparisonGateway:
    """積み上がる量の比較集合を組み立てる（P-1 を読むだけ・新しい計算を発行しない）。

    Args:
        series_port: P-1。`sub_timeframe`: 比較の最小単位。
        store: epoch 単位で持ち越すストア（ISSUE-464 ①）。**省略時はこの口だけのストア**に
            なり、共有は 1 要求で閉じる（従来と同じ費用）。要求をまたいで共有するかどうかは
            Composition Root の決定である（adapter は自分で相手を選ばない）。
    """

    def __init__(
        self, *, series_port, sub_timeframe: str = _SUB_TIMEFRAME,
        store: "MaterialStore | None" = None,
    ) -> None:
        self._series_port = series_port
        self._sub_timeframe = sub_timeframe
        self._store = store if store is not None else MaterialStore()

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
        prepared: "dict[tuple[str, str, str], tuple]" = {}
        units_by_instance: "dict[tuple[str, str, str, str], ElapsedComparison]" = {}
        for instance, spec in targets:
            fold_key = (instance.indicator_id, instance.variant, instance.params_key)
            if fold_key not in prepared:
                prepared[fold_key] = self._prepare(
                    dataset_ref, fold_key,
                    self._sub_units(dataset_ref, instance, spec), int(now_unix),
                )
            key, epoch, completed = prepared[fold_key]
            # 親足でのまとめ（O(最小単位数)）も 1m の周期が進むまで不変なので持ち越す。
            comparison = self._store.material(
                key=key, epoch=epoch, name=("comparison", instance.timeframe),
                factory=lambda timeframe=instance.timeframe: _comparison_of(
                    completed, timeframe, int(now_unix)
                ),
            )
            if comparison is not None:
                units_by_instance[instance.key] = comparison
        return units_by_instance

    # ------------------------------------------------------------------ 内部
    def _prepare(
        self, dataset_ref: str, fold_key: "tuple[str, str, str]",
        points: "Sequence[tuple[int, float]]", now_unix: int,
    ) -> tuple:
        """その (指標, 設定) の版と、完了した最小単位の列（対象の足に依らない部分）。

        版は「今どの 1m 周期に居るか」と「確定した 1m 素材の内容」の 2 つで決まる。周期だけを
        版にすると遡り訂正を 1 周期ぶん見落とす（`MaterialStore` の版と同じ理由・ISSUE-457）。
        形成中の 1m 点は完了単位に入らないので版から外す——入れるとティックごとに版が変わり、
        共有が成立しない。ただし**末尾が完了済みの単位でありうる**（最小単位の供給に現在の
        1m 周期の点が無いとき）。その場合は比較集合に入る点なので版へ戻す。周期の内か外かは
        O(1) で判るので、外すか戻すかを取り違えない。

        完了した 1m 単位の切り出しは 1m 全点の走査であり、**対象の足に依らない**。足ごとに
        切り直すと同じ走査を足の数だけ繰り返す（実測 42,023 回の半分がこれ）。
        """
        key = ("elapsed", dataset_ref, self._sub_timeframe, fold_key)
        forming_sub_unit = period_start_unix(now_unix, self._sub_timeframe)
        epoch = (
            forming_sub_unit,
            fingerprint_of(points[:-1]),
            _completed_tail(points, forming_sub_unit, self._sub_timeframe),
        )
        completed = self._store.material(
            key=key, epoch=epoch, name="completed",
            factory=lambda: _completed_units(points, now_unix, self._sub_timeframe),
        )
        return key, epoch, completed

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


def _completed_tail(
    points: "Sequence[tuple[int, float]]", forming_sub_unit: int, sub_timeframe: str
) -> "tuple[int, float] | None":
    """末尾の点が**完了済みの単位**ならそれを返す（形成中なら None）。O(1)。

    版から末尾 1 点を外すのは「末尾は形成中でありうる」ためであり、形成中の点を版に入れると
    ティックごとに版が変わって共有が成立しない。しかし最小単位の供給に現在の周期の点が無い
    とき、末尾は完了した単位であり比較集合に**入る**。そこを版から外すと、その 1 点の遡り
    訂正を最大 1 周期ぶん見落とす（古い比較集合を配る）。どちらであるかは O(1) で判る。
    """
    if not points:
        return None
    tail = points[-1]
    if period_start_unix(int(tail[0]), sub_timeframe) >= forming_sub_unit:
        return None
    return (int(tail[0]), float(tail[1]))


def _completed_units(
    points: "Sequence[tuple[int, float]]", now_unix: int, sub_timeframe: str
) -> "list[tuple[int, float]]":
    """完了した最小単位だけを取り出す（形成中の 1m は数えない・T-8）。

    最小単位の全点を走査する。**対象の時間足には依らない**ので、足ごとに切り直さない。
    """
    forming_sub_unit = period_start_unix(now_unix, sub_timeframe)
    return [
        (int(time), float(value))
        for time, value in points
        if period_start_unix(int(time), sub_timeframe) < forming_sub_unit
    ]


def _comparison_of(
    completed: "Sequence[tuple[int, float]]", timeframe: str, now_unix: int
) -> "ElapsedComparison | None":
    """完了した最小単位の列を親足でまとめ、確定した過去と形成中の部分和へ分ける。"""
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
