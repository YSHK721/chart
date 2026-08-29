"""UC-03（§5.5.5）: オシレータの分位を価格へ投影し、ラダー各行の背景を決める。

読み方は 1 つに固定する: **この価格で引けたら、各地平の `p` はどこになるか。**

**分位水準は行ではなく、価格軸を区切る境界である。** 第 1 表の行構造・並び・列は変えず、
各行の価格セルに背景色が付くだけである（行として足す案は §10 で実測により破棄した:
変換後の水準 145 本の距離は中央 3,252 点で、ラダーの実効レンジ ±350 点に入るのは 11.7% のみ）。

塗る単位は**地平 3 段**（§4.3 と同じ区分なので新しい概念が増えない＝認知負荷の最小化）。
各地平のセルには、その地平に属する instance のうち **`p` が 0.5 から最も離れたもの**を採る。

1 色にしない根拠（実測 2026-08-29・ラダー 82 行 × instance 25 本・`p` 単位）:
    1 色  … `p <= 0.1` が 47 行・`p >= 0.9` が 35 行 ＝ **82/82 行（100%）が両端**。
            中央 8 ビンがすべて空で色が 2 値に潰れる（短期の instance が常に最も 0.5 から
            離れており `max` がそれを全行へ伝播させるため）。
    3 分割 … 3 地平の `p` が 0.1 超ずれる行が 35/82（43%）。1 色にするとこの情報が消える。

計算量（§7）: ここは**閉形式だけ**で動く。行数・水準数が増えても前進評価は 1 回も増えない。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping, Sequence

from dashboard_ui.domain.continuous_quantile import QuantileScale
from dashboard_ui.domain.horizon import Horizon, includes
from dashboard_ui.domain.price_value_map import PriceValueMap
from dashboard_ui.usecase.sheet_models import LadderRow


@dataclass(frozen=True)
class InstanceProjection:
    """1 instance ぶんの投影材料（係数は UC-02 が epoch ごとに決めたものを使い回す）。"""

    timeframe: str
    value_map: PriceValueMap
    scale: QuantileScale


def project_quantiles_to_price(
    rows: "Sequence[LadderRow]",
    *,
    projections: "Sequence[InstanceProjection]",
) -> "tuple[Mapping[Horizon, float | None], ...]":
    """ラダー各行 × 地平 3 段の `p` を返す（行と同じ順序・同じ長さ）。

    候補が 1 つも残らない地平は None（**空にして色を置かない**。無言で 0.5 を埋めない）。
    """
    background: "list[Mapping[Horizon, float | None]]" = []
    for row in rows:
        cell: "dict[Horizon, float | None]" = {}
        for horizon in Horizon:
            candidates = [
                value
                for value in (
                    _p_of(projection, row.price)
                    for projection in projections
                    if includes(horizon, projection.timeframe)
                )
                if value is not None
            ]
            cell[horizon] = (
                max(candidates, key=lambda value: abs(value - 0.5))
                if candidates
                else None
            )
        background.append(cell)
    return tuple(background)


def _p_of(projection: InstanceProjection, price: float) -> "float | None":
    """その価格で引けたときの `p`（前進評価は発行しない＝閉形式のみ）。"""
    return projection.scale.p_of(projection.value_map.value_at(price)).p
