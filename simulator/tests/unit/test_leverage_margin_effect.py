"""`Leverage` の必要証拠金への効き方（基本設計 §5.4 の数値例・T-04）を固定する。

固定する仕様（設計書の数値例。実装の写しではない）:
    基本設計 §5.4 末尾「数値例（`leverage` の効き方の検証項目）」——
    同一ポジション（`lot=1`, `contract_size=1`, `entry=40000`）に対し
    `leverage=10` の `Margin` は `4000`、`leverage=100` の `Margin` は `400` であり、
    `leverage=10` の必要証拠金は `1:100` の **10 倍**になる。
    式は `Margin = volume × contract_size × entry_price / leverage`
    （`BACKTEST_METRICS.md §5.3`）。

接続先の固定（基本設計 §5.4 の警告・ISSUE-388 項 6）:
    現行リポジトリには口座エンジンが二系統ある。Settings 層の `leverage` は
    `run_backtest` 内の `Account` / `Position` 経路（MT5 型・`leverage` 除数）へ写す。
    本テストはその ① 経路の `domain.position.Position.required_margin` を対象とする
    （`usecase/account_engine.py` の margin_rate ベース経路ではない）。

⚠️ 本テストは既存実装（`simulator/domain/position.py`）に対する**仕様適合テスト**で
あり、対象実装は本フェーズ以前から存在する。したがって Red は観測されない
（TDD スキル §4「Red 観測ゲート」の分類 3＝実装の事前残存）。期待値は上記設計書の
数値例から取っており、実装から読み取った値ではない。
"""
from __future__ import annotations

import pytest

from simulator.domain.position import Position

#: 基本設計 §5.4 の数値例が定める同一ポジション。
LOT = 1.0
CONTRACT_SIZE = 1.0
ENTRY_PRICE = 40000.0


def _position() -> Position:
    return Position(side="buy", volume=LOT, entry_price=ENTRY_PRICE)


@pytest.mark.parametrize(
    ("leverage", "expected_margin"),
    [(10.0, 4000.0), (100.0, 400.0)],
)
def test_required_margin_matches_the_design_numeric_example(leverage, expected_margin):
    # Arrange
    position = _position()
    # Act
    margin = position.required_margin(leverage, CONTRACT_SIZE)
    # Assert: 基本設計 §5.4 の数値例
    assert margin == pytest.approx(expected_margin)


def test_leverage_10_requires_ten_times_the_margin_of_leverage_100():
    # Arrange
    position = _position()
    # Act
    margin_10 = position.required_margin(10.0, CONTRACT_SIZE)
    margin_100 = position.required_margin(100.0, CONTRACT_SIZE)
    # Assert: 比が 10 倍（§5.4「1:100 の 10 倍」）
    assert margin_10 == pytest.approx(margin_100 * 10.0)


def test_required_margin_is_inversely_proportional_to_leverage():
    # Arrange: 除数であること（§5.4「`Margin` の除数」）の境界確認
    position = _position()
    # Act
    margins = {lev: position.required_margin(lev, CONTRACT_SIZE) for lev in (1.0, 10.0, 100.0)}
    # Assert
    assert margins[1.0] == pytest.approx(LOT * CONTRACT_SIZE * ENTRY_PRICE)
    assert margins[1.0] == pytest.approx(margins[10.0] * 10.0)
    assert margins[10.0] == pytest.approx(margins[100.0] * 10.0)
