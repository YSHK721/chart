"""水準到達シートの Input / Output Model。

畳み込みキー `(indicator_id, variant, params_key, timeframe)`（§7・T-1）:
    同一キーの full 系列は **1 回しか発行してはならない**。表は既存の計算結果を**読むだけ**で
    あり、新規の計算を発行しない（§8 OCP）。段 1 の実測では、ラダーの 71 本が全指標 105 本の
    部分集合であるのに別々に計算すると 2,316ms が丸ごと無駄になる（ISSUE-450 と同型）。
    キーが不安定（辞書順・hash 乱数化に依存）だと、同じ計算を 2 回発行しても検査が通る。
    そのため `params_key` は `json.dumps(..., sort_keys=True)` で決定的に作る。
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from enum import Enum
from typing import Callable, Mapping

from dashboard_ui.domain.elapsed_fraction_pool import ElapsedFractionPool
from dashboard_ui.domain.horizon import Horizon
from dashboard_ui.domain.reach import ReachState

#: 畳み込みキー。
InstanceKey = "tuple[str, str, str, str]"


class SeriesRole(Enum):
    """系列の役割（§3.1: 除外は名前ではなく**実値の桁**で判定する。判定表は adapter が持つ）。"""

    PRICE_LEVEL = "price_level"
    NOT_LEVEL = "not_level"


class UpdateGranularity(Enum):
    """更新粒度（§7）。差を隠して「リアルタイム」と称さない。"""

    TICK = "tick"
    BAR_CLOSE = "bar_close"
    #: 更新されない（その instance の価格投影が出せない＝背景が塗られない）。
    #: バー確定でも回復しないので `BAR_CLOSE` とは別物である（レビュー 🟡-2）。
    NONE = "none"


@dataclass(frozen=True)
class SheetInstance:
    """指標インスタンス 1 本（`timeframe` は**解決済みの軸**）。"""

    indicator_id: str
    variant: str
    params: Mapping[str, object]
    timeframe: str
    intrabar_capable: bool = False

    @classmethod
    def of(
        cls,
        indicator_id: str,
        variant: str,
        params: Mapping[str, object],
        *,
        chart_timeframe: str,
        intrabar_capable: bool = False,
    ) -> "SheetInstance":
        """params の `timeframe` から軸を解決する（§2 chart 追従水準 / MTF 固定水準）。

        `"chart"`（未指定含む）は表示時間足に従い、明示された足は表示足に依らず同一値。
        軸を解決してからキーを作るので、同じ MTF 水準が表示足ごとに重複発行されない。
        """
        rest = {k: v for k, v in params.items() if k != "timeframe"}
        own = params.get("timeframe") or "chart"
        axis = chart_timeframe if own == "chart" else str(own)
        return cls(indicator_id, variant, rest, axis, intrabar_capable)

    @property
    def params_key(self) -> str:
        return json.dumps(dict(self.params), sort_keys=True, ensure_ascii=False,
                          default=str)

    @property
    def key(self) -> "tuple[str, str, str, str]":
        return (self.indicator_id, self.variant, self.params_key, self.timeframe)


@dataclass(frozen=True)
class OscillatorSpec:
    """第 2 表のセルで `p` を出すために必要な宣言（adapter が指標設定から作る）。

    `excess` は超過分の定義（RSI は `(v-u)/(100-u)`・`levels.py` ③）。usecase / domain が
    指標名で分岐しないための注入点である（§8 OCP）。
    """

    value_series: str
    band_high_series: str
    q_high: float
    window_n: int
    k_events: int
    #: 下帯（q_low）の系列名と分位。設定にもカタログにも q_low が無い指標では None
    #: （発明しない・依頼者承認 2026-08-30: 分位水準到達価格の上下 2 値表示）。
    band_low_series: "str | None" = None
    q_low: "float | None" = None
    cumulative: bool = False
    excess: Callable[[float, float], float] = field(
        default=lambda value, band_high: value - band_high
    )


@dataclass(frozen=True)
class ReachSheetRequest:
    """段 1 の入力（束＝instances は Input Model の一部としてサーバへ送られる・T-2）。"""

    dataset_ref: str
    instances: "tuple[SheetInstance, ...]"
    chart_timeframe: str = "1m"

    def unique_instances(self) -> "tuple[SheetInstance, ...]":
        """重複キーを 1 本へ畳む（初出の順序を保つ）。"""
        seen: "set[tuple[str, str, str, str]]" = set()
        folded: "list[SheetInstance]" = []
        for instance in self.instances:
            if instance.key in seen:
                continue
            seen.add(instance.key)
            folded.append(instance)
        return tuple(folded)


@dataclass(frozen=True)
class LadderRow:
    """第 1 表の 1 行（§4.7 の版面＋§5.5.5 の地平別背景）。"""

    price: float
    timeframe: str
    label: str
    distance: float
    gap_to_previous: "float | None"
    horizon_marks: "frozenset[Horizon]"
    reach: ReachState
    horizon_p: "Mapping[Horizon, float | None]" = field(default_factory=dict)
    #: この行を出した instance の畳み込みキー（`Degradation.instance_key` と同じ形）。
    #: §7 の「足内更新を持たない指標の行には更新粒度がバー確定であることを表示する」を
    #: 行単位で解けるようにするための紐付けであり、行と縮退の告知を同じキーで突き合わせる。
    instance_key: "tuple[str, str, str, str] | None" = None
    #: 表示 3 分割 {name, period, source, extra}（依頼者指示 2026-08-30）。識別は従来どおり
    #: `label` が担い、こちらは版面の読みやすさのためだけに使う。
    naming: "Mapping[str, object] | None" = None
    #: この行の水準を出した**系列の名前**（instance の中の 1 本）。フロントのなめらか再生
    #: （/live_ticks の tails・依頼者指示 2026-08-31）が「どの系列の末尾値をこの行の
    #: 価格へ流すか」を選ぶための宣言。表示専用（None＝流さない）。
    series: "str | None" = None


@dataclass(frozen=True)
class ElapsedComparison:
    """§5.3.3 の比較集合（形成中の積み上がる量を同経過の過去へ当てるための材料）。

    `pool` は**確定した過去の足だけ**を保持していること（因果境界）。`completed_units` は
    形成中の足で完了したサブ単位数（tf >= 5m なら完了した 1m 本数＝T-8 の k）。
    """

    pool: ElapsedFractionPool
    completed_units: int
    forming_sum: float


@dataclass(frozen=True)
class OscCell:
    """第 2 表の 1 セル（§5.2: 色から絶対量は読めないため現在値の数字を必ず併記する）。"""

    indicator_id: str
    timeframe: str
    value: "float | None"
    p: "float | None"
    tail_unscaled: bool
    reach: "ReachState | None" = None
    unavailable_reason: "str | None" = None
    #: この セルを出した instance の畳み込みキー（LadderRow.instance_key と同じ形）。
    #: §5.5 の価格射影（分位水準に達する価格・依頼者指示 2026-08-30）をセルへ紐付けるための
    #: 識別子。(indicator_id, timeframe) はキーにならない（§5.1: ma_marod は 1D に 2 本）。
    instance_key: "tuple[str, str, str, str] | None" = None
    #: 現在値が読んでいる系列の名前（OscillatorSpec.value_series）。フロントのなめらか再生
    #: （/live_ticks の tails・依頼者指示 2026-08-31）が「どの系列の末尾値をこのセルへ
    #: 流すか」を選ぶための宣言。表示専用で、無くても版面は成立する（None＝流さない）。
    value_series: "str | None" = None


@dataclass(frozen=True)
class Degradation:
    """更新粒度の縮退（§7）。無言で落とさず、表へ明示する。"""

    instance_key: "tuple[str, str, str, str]"
    granularity: UpdateGranularity
    reason: str


@dataclass(frozen=True)
class ReachSheetResponse:
    """段 1 の出力。"""

    current_price: float
    rows: "tuple[LadderRow, ...]"
    current_index: int
    cells: "tuple[OscCell, ...]"
    degradations: "tuple[Degradation, ...]"
