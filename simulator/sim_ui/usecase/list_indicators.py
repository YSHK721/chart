"""UC-S4 指標一覧（台帳 → selectable + reason つき系列一覧・usecase 層・Phase 3 F-5）。

規則:
    1. 台帳の全**系列**を返す。**不一致・未検定の系列も ``selectable=False`` として
       必ず含める**。一覧から黙って落とすと、利用者には「その系列は無い」としか見えず、
       「検定に落ちた」「測り切れなかった」という事実と理由が届かない（§3.5.4「明示する」）。
    2. 単位は系列（裁定 A）。戦略は系列名で指標値を参照するため、指標単位で可否を
       まとめると、使える系列まで巻き添えで落ちる。
    3. 台帳が読めないときは fail-closed。例外をそのまま上へ通す（adapter が 503 へ翻訳）。
       空一覧や「全部使える」に倒すと、未検定の指標を使わせる誤りを黙って通す。
    4. 並びは (indicator, variant, series) 昇順で決定的にする（台帳の記録順に依存させない）。
    5. 測定条件を添える。条件の無い一致主張は再現できない。

CLEAN_ARCH: usecase 層。Port にのみ依存する。
"""
from __future__ import annotations

from simulator.sim_ui.usecase.indicator_models import (
    IndicatorListing,
    IndicatorListingItem,
)
from simulator.sim_ui.usecase.indicator_ports import IndicatorCausalityLedgerPort


def list_indicators(*, ledger: IndicatorCausalityLedgerPort) -> IndicatorListing:
    """台帳の検定結果を一覧へ写す（読めないときは例外を伝播する）。"""
    snapshot = ledger.read()
    items = [
        IndicatorListingItem(
            indicator=finding.spec.indicator,
            variant=finding.spec.variant,
            params=finding.spec.params,
            series_name=finding.series_name,
            selectable=finding.selectable,
            reason=finding.reason,
            detail=finding.detail,
        )
        for finding in snapshot.findings
    ]
    items.sort(key=lambda item: (item.indicator, item.variant, item.series_name))
    return IndicatorListing(
        measured_at=snapshot.measured_at,
        conditions=snapshot.conditions,
        items=tuple(items),
    )
