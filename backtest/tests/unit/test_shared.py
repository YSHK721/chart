"""domain 内部共有プリミティブの単体テスト（CLEAN_ARCH §4 / METRICS §5.1・§5.2）。

回帰テスト（🟡-3）: sign_of は不正 side（大文字・タイポ等）を黙って sell(-1)
扱いせず、明示的に domain 例外を送出しなければならない。損益符号の静かな反転
（"BUY" → -1）を禁止する。
"""
from __future__ import annotations

import pytest

from backtest.domain._shared import SIDES, sign_of
from backtest.domain.exceptions import ExecutionError


class TestSignOf:
    def test_buy_returns_positive_one(self):
        # Arrange / Act / Assert
        assert sign_of("buy") == 1

    def test_sell_returns_negative_one(self):
        # Arrange / Act / Assert
        assert sign_of("sell") == -1

    @pytest.mark.parametrize("bad_side", ["BUY", "Sell", "long", "short", "", "x"])
    def test_invalid_side_raises_execution_error(self, bad_side):
        # 回帰（🟡-3）: 不正 side を sell(-1) に倒さず明示拒否する
        with pytest.raises(ExecutionError):
            sign_of(bad_side)

    def test_valid_sides_match_canonical_set(self):
        # SIDES の全要素が例外なく符号を返す（正準語彙との整合）
        for side in SIDES:
            assert sign_of(side) in (1, -1)
