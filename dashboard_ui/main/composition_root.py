"""Composition Root（唯一の束縛点・CLEAN_ARCH §8）。

具象を組み立ててよいのはここだけである。usecase / domain は Protocol 越しにしか外を
知らず、adapter は自分で相手を選ばない。

素材の鮮度と計算量の両立:
    要求ごとに口（gateway）を組み直す。gateway は自分の生存期間だけ計算を畳む（同一キーの
    full 系列は 1 回・足は 1 回）ので、**1 枚のシートを作る間の畳み込み**（T-1）は保たれ、
    次の要求では新しい足が読まれる。

    要求をまたいで持ち越すのはプロセス寿命の 2 つだけである（どちらも「足の鮮度と無関係な
    量」＝epoch の中で不変な量である点で同型）:
      - `SheetState`     … 当てはめの epoch と GPD の当てはめ結果。
      - `MaterialStore`  … **確定足ぶんの full 系列**（ISSUE-457）。形成中足の 1 点は
                           gateway が毎要求作って継ぐので、現在値・走行 H/L は古くならない。
    これが §7 の 2 段（段 1＝バー確定で作り直す／段 2＝ティックでは末尾だけ動かす）を
    そのまま構造にしたものである。共有しないと epoch 不変のティックでも同じ確定系列を
    毎秒作り直す（§9-4 実測: 要求の 78%）。

読む本数の上限:
    参照実装 tools/measure/issue449/probe_inverse.py:41-42 の本数表をそのまま使う。
    上限は「症状の回避」ではなく素材の量の宣言であり、ライブのチャートが読む本数と同じ
    考え方（足ごとに必要な履歴長が違う）である。
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping

from dashboard_ui.adapter.breakpoints import BreakpointRegistry
from dashboard_ui.adapter.controller.reach_sheet_controller import (
    ReachSheetController,
    SheetState,
)
from dashboard_ui.adapter.gateway.elapsed_comparison_gateway import (
    ElapsedComparisonGateway,
)
from dashboard_ui.adapter.gateway.forward_evaluation_gateway import (
    ForwardEvaluationGateway,
)
from dashboard_ui.adapter.gateway.indicator_ui_compute_gateway import (
    IndicatorUiComputeGateway,
)
from dashboard_ui.adapter.gateway.intrabar_capability_gateway import (
    IntrabarCapabilityGateway,
)
from dashboard_ui.adapter.gateway.material_store import MaterialStore
from dashboard_ui.adapter.series_role_table import SeriesRoleTable
from dashboard_ui.framework.serve_dashboard import DashboardApp
from dashboard_ui.usecase.sheet_models import SheetInstance

# repo 根 = dashboard_ui/main/composition_root.py の parents[2]。
_REPO_ROOT = Path(__file__).resolve().parents[2]

#: 足ごとに読む本数（参照実装 probe_inverse.py:41-42 の本数表と同値）。
BAR_LIMITS: "Mapping[str, int]" = {
    "1m": 3000, "5m": 3000, "15m": 3000, "1h": 2000,
    "4h": 2000, "1D": 2000, "1W": 1500, "1M": 800,
}

#: 表示に使うデータセット（T-10: ライブと同一の `jp225_tick` 固定）。参照は要求が運ぶ。
DATASET_REF = "jp225_tick"


def build_dashboard_app(
    *,
    repo_root: Any = None,
    web_dir: Any = None,
    shared_js_root: Any = None,
    bar_limits: "Mapping[str, int] | None" = None,
) -> DashboardApp:
    """dashboard core のアプリケーションを組み立てる。

    Args:
        repo_root: リポジトリ根（既定は本ファイルから解決）。
        web_dir: フロントの配信根（既定は `dashboard_ui/web`。無ければ静的配信無効）。
        shared_js_root: 単一ソース共有の根（既定は `indigators/indicator_ui/web`）。
        bar_limits: 足ごとに読む本数（既定は :data:`BAR_LIMITS`）。
    """
    root = Path(repo_root).resolve() if repo_root is not None else _REPO_ROOT
    limits = dict(BAR_LIMITS if bar_limits is None else bar_limits)
    web = Path(web_dir).resolve() if web_dir is not None else root / "dashboard_ui" / "web"
    shared = (
        Path(shared_js_root).resolve()
        if shared_js_root is not None
        else root / "indigators" / "indicator_ui" / "web"
    )
    roles = SeriesRoleTable()
    registry = BreakpointRegistry()
    capability = IntrabarCapabilityGateway()
    state = SheetState()
    materials = MaterialStore()

    def controller_factory() -> ReachSheetController:
        series_gateway = IndicatorUiComputeGateway(bar_limits=limits, store=materials)
        return ReachSheetController(
            series_port=series_gateway,
            bar_port=series_gateway,
            roles=roles,
            registry=registry,
            forward_port=ForwardEvaluationGateway(
                value_series_of=_value_series_of(roles), bar_limits=limits
            ),
            elapsed_gateway=ElapsedComparisonGateway(series_port=series_gateway),
            is_intrabar_capable=capability,
            state=state,
        )

    return DashboardApp(
        controller_factory=controller_factory,
        web_dir=web if web.is_dir() else None,
        shared_js_root=shared if shared.is_dir() else None,
    )


def _value_series_of(roles: SeriesRoleTable):
    """指標 → 「到達する量」の系列名（宣言の唯一源は第 2 表のセル宣言）。"""

    def resolve(indicator_id: str, variant: str, params: "Mapping[str, object]") -> str:
        spec = roles.oscillator_spec(
            instance=SheetInstance.of(
                indicator_id, variant, dict(params), chart_timeframe="1m"
            ),
            series_names=frozenset(),
        )
        if spec is None:
            raise ValueError(
                f"到達する量が宣言されていません: indicatorId={indicator_id!r}"
            )
        return spec.value_series

    return resolve
