"""domain 内部共有プリミティブの単体テスト（CLEAN_ARCH §4 / METRICS §5.1・§5.2）。

回帰テスト（🟡-3）: sign_of は不正 side（大文字・タイポ等）を黙って sell(-1)
扱いせず、明示的に domain 例外を送出しなければならない。損益符号の静かな反転
（"BUY" → -1）を禁止する。
"""
from __future__ import annotations

import pytest

from backtest.domain._shared import SIDES, round_profit, sign_of
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


class TestRoundProfit:
    """ISSUE-020: 約定損益の口座通貨丸め（digits=None は素値・half-away-from-zero）。"""

    def test_none_digits_returns_raw_value(self):
        # digits=None は丸めず素値（byte-identical 後方互換）。
        assert round_profit(200.4, None) == 200.4
        assert round_profit(-6.2, None) == -6.2

    def test_none_digits_returns_same_object(self):
        # byte-identical 厳密化（レビュー🟡）: None 経路は同一オブジェクト素通し
        # （+0.0 等の演算を挟まない）。退行（value+0.0 化等）を検知する。
        v = 12345.678901
        assert round_profit(v, None) is v

    def test_digits_gt_zero_no_float_misrounding(self):
        # float 精度起因の誤丸め防止（レビュー🟡・Decimal 実装）。
        # 0.285→0.29・1.005→1.01（素朴な value*100 では 0.28・1.0 に誤丸めする値）。
        assert round_profit(0.285, 2) == 0.29
        assert round_profit(1.005, 2) == 1.01
        assert round_profit(-0.285, 2) == -0.29

    def test_jpy_integer_rounding(self):
        # 0 桁丸め（JPY）。実 MT5 突合の実例 200.4→200・-6.2→-6。
        assert round_profit(200.4, 0) == 200.0
        assert round_profit(-6.2, 0) == -6.0

    def test_half_away_from_zero(self):
        # 商習慣丸め（half-away-from-zero）: .5 はゼロから遠い側へ。banker's とは異なる。
        assert round_profit(2.5, 0) == 3.0
        assert round_profit(-2.5, 0) == -3.0
        assert round_profit(0.5, 0) == 1.0

    def test_multiple_digits(self):
        assert round_profit(1.2345, 2) == 1.23
        assert round_profit(1.235, 2) == 1.24
