"""IndicatorDef / ComputeEntry / CategoryRef と列挙（内部設計書 §3.1.3）。

レジストリ 1 件のメタデータ。検索・フィルタ（UC-01）は id/display_name を対象とし、
series/params は読まない（基本設計 §4.6）。

標準ライブラリのみ。`@dataclass(frozen=True)`（DTO は不変）。
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Mapping

from domain.param_def import ParamDef
from domain.series_def import SeriesDef
from domain.validation import ConstraintEvaluator, Violation


class Tab(Enum):
    """ダイアログのタブ（基本設計 §6.1 相当）。"""

    INDICATOR = "indicator"
    STRATEGY = "strategy"
    PROFILE = "profile"
    PATTERN = "pattern"


class Placement(Enum):
    """描画配置（主チャート重畳 / 別ペイン）。"""

    OVERLAY = "overlay"
    PANE = "pane"


class Group(Enum):
    """サイドバー分類グループ。"""

    PERSONAL = "personal"
    BUILTIN = "builtin"
    COMMUNITY = "community"


@dataclass(frozen=True)
class CategoryRef:
    """サイドバーカテゴリ参照（内部設計書 §3.1.3）。"""

    group: Group
    name_key: str


@dataclass(frozen=True)
class ComputeEntry:
    """計算 API 上の論理識別子と必要入力（内部設計書 §3.1.3）。

    required_columns: 必要価格列（全 3 指標 ("open","high","low","close")）。
    time_required: line=true / price_range_power=false。
    backend_param: tgp_btlm="fitter"、他 None。
    variants: profit_band=("global","robust")、他は ("default",)。
    """

    compute_id: str
    required_columns: tuple[str, ...]
    time_required: bool
    backend_param: str | None = None
    variants: tuple[str, ...] = ("default",)


@dataclass(frozen=True)
class IndicatorDef:
    """レジストリ 1 件の統一メタデータ（内部設計書 §3.1.3・基本設計 §5.2）。"""

    id: str
    display_name_key: str
    category: CategoryRef
    tab: Tab
    placement: Placement
    params: tuple[ParamDef, ...]
    series: tuple[SeriesDef, ...]
    compute: ComputeEntry
    description_key: str | None = None

    def __post_init__(self) -> None:
        """不変条件を検証する（構築時に違反なら ValueError）。

        series>=1: 描画系列を 1 件も持たない指標は不正（§3.1.3）。
        """
        if not self.series:
            raise ValueError(
                f"IndicatorDef '{self.id}' は series を 1 件以上持つ必要がある"
            )

    def validate_params(self, values: Mapping[str, object]) -> list[Violation]:
        """パラメータ値の妥当性を ConstraintEvaluator.evaluate に委譲する（§3.1.5 単一定義）。

        二重実装せず第1増分の評価器を呼ぶだけ。空 list = 妥当。
        """
        return ConstraintEvaluator.evaluate(self.params, values)

    def matches(self, query: str, display_name: str) -> bool:
        """検索一致（§4.6）: 表示名+id を対象、小文字化、部分一致、複数語は論理積。

        display_name は i18n 解決後の表示文字列（domain は解決器を持たないため値で受ける）。
        空クエリは全件通過（語が無い＝論理積は真）。
        """
        haystack = f"{display_name} {self.id}".lower()
        return all(term in haystack for term in query.lower().split())
