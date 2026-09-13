"""気配 1 つ（bid, ask）から価格を得る規則（ISSUE-515 対策 2）。

ライブ tick バッファはティックを 1 つずつ受けて価格へ畳む（frame ではない）。その規則を
バッファ側で手書きすると、frame 側の規則（``tick_m1.ts_and_price``）と 2 つに割れる。
本検定は、単一ティックの規則が frame の規則と同じ答えを返すことを固定する。

構造: Arrange-Act-Assert（AAA）。
"""
from __future__ import annotations

import pandas as pd
import pytest

from marketdata import tick_m1


def _one_tick(bid: float, ask: float) -> pd.DataFrame:
    return pd.DataFrame({
        "timestamp": pd.to_datetime([1_700_000_000_000], unit="ms", utc=True),
        "bidPrice": [bid],
        "askPrice": [ask],
    })


@pytest.mark.parametrize("basis", [tick_m1.PRICE_BASIS_MID, tick_m1.PRICE_BASIS_BID])
def test_a_single_quote_folds_as_the_frame_rule_does(basis):
    """単一ティックの価格は、同じティックを frame の規則で畳んだ価格と一致する。"""
    # Arrange
    bid, ask = 39000.0, 39010.0
    _, from_frame = tick_m1.ts_and_price(_one_tick(bid, ask), price_basis=basis)

    # Act
    got = tick_m1.quote_price(bid, ask, price_basis=basis)

    # Assert
    assert got == float(from_frame.iloc[0])


def test_the_bid_basis_is_the_bid_and_mid_is_the_midpoint():
    """値ピン: bid 基準は bid、mid 基準は中値（規則そのものの確認）。"""
    # Arrange / Act / Assert
    assert tick_m1.quote_price(10.0, 20.0, price_basis=tick_m1.PRICE_BASIS_BID) == 10.0
    assert tick_m1.quote_price(10.0, 20.0, price_basis=tick_m1.PRICE_BASIS_MID) == 15.0


def test_an_unknown_basis_is_refused():
    """未知の基準は拒否する（既定の mid へ落とさない）。"""
    # Arrange / Act / Assert
    with pytest.raises(ValueError):
        tick_m1.quote_price(10.0, 20.0, price_basis="ask")
