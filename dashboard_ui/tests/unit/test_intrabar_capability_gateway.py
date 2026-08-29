"""足内更新の可否（§3.1「足内更新」列・§7）を、ライブ側の判定から引くことを固定する。

判定の唯一源はライブ側 `adapter/compute/live_tick_tails.py` の増分器宣言である。ここに写しを
作ると、増分器が付いた／外れたときに表示だけが古くなる（cvfe の更新粒度は §7 で表に出す
と決めた項目なので、食い違うと「無言の縮退」になる）。

契約テスト（§7.1 `intrabar_update`）が読むのもこの面である。
"""
from __future__ import annotations

from dashboard_ui.adapter.gateway.intrabar_capability_gateway import (
    IntrabarCapabilityGateway,
)


def test_an_indicator_with_an_incremental_engine_can_update_intrabar() -> None:
    capable = IntrabarCapabilityGateway()

    assert capable("ma_marod", "default", {"source": "hlc3", "length": 50}) is True


def test_cvfe_cannot_update_intrabar() -> None:
    """§3.1 / §7: cvfe は増分器が無く、バー確定でしか動かない（実測で固定する）。"""
    capable = IntrabarCapabilityGateway()

    assert capable("cvfe", "default", {}) is False


def test_the_four_oscillators_can_all_update_intrabar() -> None:
    """§3.2: 第 2 表の 4 種はすべて足内更新できる（更新粒度の差が無い）。"""
    capable = IntrabarCapabilityGateway()

    assert all(
        capable(indicator_id, "default", {}) is True
        for indicator_id in ("ma_marod", "btlm_trail_marod", "profit_rsi", "tickvol")
    )
