"""UC-S4 指標一覧（台帳 → selectable + reason つき系列一覧）の単体検定。

固定する規則（Phase 3 構造設計 §新規ファイル #5・契約改訂裁定 A/C）:
    1. 台帳の全**系列**を返す。**不一致・未検定の系列も `selectable=False` として
       必ず含める**。一覧から黙って消すと、利用者には「その系列は存在しない」としか
       見えず、「検定に落ちた」「測り切れなかった」という事実と理由が届かない。
    2. 選択可否の単位は系列（指標単位でまとめると使える系列が巻き添えで落ちる）。
    3. reason は 3 値固定（`mismatch` / `supply_cost_exceeded` /
       `verification_incomplete`）。自由文は detail。
    4. 台帳が読めないときは fail-closed（例外をそのまま上へ）。空一覧や「全部使える」
       へ倒さない。どちらも未検定の指標を使わせる誤りを黙って通す。
    5. 並び順は (indicator, variant, series) 昇順で決定的にする。
    6. 測定条件（供給窓・検定窓・coverage・tolerance 等）を添えて返す。

方式: 合成データ（`FakeCausalityLedger`）のみ。
"""
from __future__ import annotations

import pytest

from simulator.sim_ui.tests.integration._fake_indicator_ports import FakeCausalityLedger
from simulator.sim_ui.usecase.indicator_models import (
    REASON_MISMATCH,
    REASON_SUPPLY_COST_EXCEEDED,
    REASON_VERIFICATION_INCOMPLETE,
    CausalityFinding,
    CausalityLedgerUnavailableError,
    IndicatorSpec,
    LedgerConditions,
    LedgerSnapshot,
)
from simulator.sim_ui.usecase.list_indicators import list_indicators

_CONDITIONS = LedgerConditions(
    ref="jp225_tick", timeframe="5m", supply_bars=10_000, verify_bars=1_000,
    verify_coverage=1.0, timeout=600.0, supply_budget=1.0, limit=None,
    tolerance=0.0, probe_mode="full",
)


def _finding(indicator, series="MA", *, variant="default", ok=True, reason=None,
             detail=None):
    return CausalityFinding(
        spec=IndicatorSpec(indicator=indicator, variant=variant, params={"length": 20}),
        series_name=series,
        selectable=ok,
        reason=reason,
        detail=detail,
        bars_compared=10_000 if ok else 0,
        max_abs_diff=0.0 if ok else None,
    )


def _snapshot(findings) -> LedgerSnapshot:
    return LedgerSnapshot(
        schema=1, measured_at="2026-08-11T00:00:00Z",
        conditions=_CONDITIONS, findings=tuple(findings),
    )


# --- 1. 一覧（規則 1・2）--------------------------------------------------

def test_台帳の系列が一覧に載る() -> None:
    # Arrange
    ledger = FakeCausalityLedger(_snapshot([_finding("moving_averages")]))
    # Act
    listing = list_indicators(ledger=ledger)
    # Assert
    item = listing.items[0]
    assert (item.indicator, item.variant, item.series_name) == (
        "moving_averages", "default", "MA"
    )
    assert item.selectable is True
    assert item.reason is None
    assert item.params == {"length": 20}


def test_同じ指標の系列が別々に載る() -> None:
    """規則 2。使える系列を巻き添えで落とさない。"""
    # Arrange
    ledger = FakeCausalityLedger(_snapshot([
        _finding("profit_band", "UPPER", variant="robust"),
        _finding("profit_band", "LOWER", variant="robust", ok=False,
                 reason=REASON_MISMATCH, detail="最初の不一致 time=1"),
    ]))
    # Act
    listing = list_indicators(ledger=ledger)
    # Assert
    by_series = {i.series_name: i for i in listing.items}
    assert by_series["UPPER"].selectable is True
    assert by_series["LOWER"].selectable is False


@pytest.mark.parametrize("reason", [
    REASON_MISMATCH, REASON_SUPPLY_COST_EXCEEDED, REASON_VERIFICATION_INCOMPLETE,
])
def test_選択不可の理由は3値で載る(reason: str) -> None:
    """規則 3。機械判定が自由文の表記ゆれに依存しないようにする。"""
    # Arrange
    ledger = FakeCausalityLedger(_snapshot([
        _finding("cvfe", ok=False, reason=reason, detail="測定の詳細"),
    ]))
    # Act
    listing = list_indicators(ledger=ledger)
    # Assert
    assert listing.items[0].reason == reason
    assert listing.items[0].detail == "測定の詳細"


def test_空の台帳は空一覧になる() -> None:
    """境界値: 検定結果 0 件。読めてはいるので例外ではない。"""
    # Arrange
    ledger = FakeCausalityLedger(_snapshot([]))
    # Act
    listing = list_indicators(ledger=ledger)
    # Assert
    assert listing.items == ()
    assert listing.measured_at == "2026-08-11T00:00:00Z"


# --- 2. fail-closed（規則 4）----------------------------------------------

def test_台帳が読めないときは例外を伝播する() -> None:
    """空一覧や「全部使える」に倒さない。"""
    # Arrange
    ledger = FakeCausalityLedger(None)
    # Act / Assert
    with pytest.raises(CausalityLedgerUnavailableError):
        list_indicators(ledger=ledger)


# --- 3. 決定的な並び（規則 5）---------------------------------------------

def test_並び順はindicatorとvariantと系列名の昇順() -> None:
    # Arrange（記録順は昇順ではない）
    ledger = FakeCausalityLedger(_snapshot([
        _finding("profit_band", "UPPER", variant="robust"),
        _finding("moving_averages", "MA"),
        _finding("profit_band", "LOWER", variant="global"),
        _finding("profit_band", "BASE", variant="global"),
    ]))
    # Act
    listing = list_indicators(ledger=ledger)
    # Assert
    assert [(i.indicator, i.variant, i.series_name) for i in listing.items] == [
        ("moving_averages", "default", "MA"),
        ("profit_band", "global", "BASE"),
        ("profit_band", "global", "LOWER"),
        ("profit_band", "robust", "UPPER"),
    ]


# --- 4. 測定条件（規則 6）--------------------------------------------------

def test_測定条件が一覧に添えられる() -> None:
    # Arrange
    ledger = FakeCausalityLedger(_snapshot([_finding("moving_averages")]))
    # Act
    listing = list_indicators(ledger=ledger)
    # Assert
    assert listing.conditions.ref == "jp225_tick"
    assert listing.conditions.supply_bars == 10_000
    assert listing.conditions.verify_bars == 1_000
    assert listing.conditions.verify_coverage == 1.0
    assert listing.conditions.timeout == 600.0
    assert listing.conditions.supply_budget == 1.0
    assert listing.conditions.limit is None
    assert listing.conditions.tolerance == 0.0
    assert listing.conditions.probe_mode == "full"
