"""MaSlope のロット正規化（原典 MA_Slope_EA.mq5:NormalizeLot / OpenPosition）テスト。

参照実装（simulator/tests/fixtures/mt5/ma_slope_jp225_202501/expert/MA_Slope_EA.mq5:157）:

    double NormalizeLot(const double lot)
      {
       double min  = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_MIN);
       double max  = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_MAX);
       double step = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_STEP);
       double v = lot;
       if(step > 0.0) v = MathRound(v / step) * step;
       if(v < min)    v = min;
       if(max > 0.0 && v > max) v = max;
       int digits = (step > 0.0) ? (int)MathCeil(-MathLog10(step)) : 2;
       if(digits < 0) digits = 0;
       return(NormalizeDouble(v, digits));
      }

`OpenPosition()`（同 :126）は `volume = NormalizeLot(Lot)` を通し、`volume <= 0.0` なら
発注せずに戻る。本テストは戦略の公開 IF（on_new_bar が返す Order.volume）で、
上記の分岐・境界・丸め桁が 1:1 で移植されていることを固定する（ISSUE-445 段階 1）。

銘柄仕様は `strategy_params`（`build_interactor`）経由で `volume_min` / `volume_max` /
`volume_step` として供給される。既存 config（キー未供給）との後方互換は
test_strategy_ma_slope.py が固定する（volume == lot_size のまま）。
"""
from __future__ import annotations

import pandas as pd
import pytest


# JP225 想定: point_size=0.1・SlopeMinPts=1.0 → threshold=0.1。SL/TP=0（本 EA は無し）。
_BASE_CONFIG = {
    "lot_size": 0.1,
    "slope_shift": 1,
    "slope_min_points": 1.0,
    "point_size": 0.1,
    "stop_loss_points": 0,
    "take_profit_points": 0,
}

# 上向き傾き（ema[1]-ema[0]=0.3 > threshold 0.1）→ bar_index=2 で買いシグナル。
_UPWARD_EMA = [100.0, 100.3, 100.5]


class _Account:
    def __init__(self, sides=()):
        self.open_positions = [type("P", (), {"side": s})() for s in sides]


def _registry(ema_vals):
    from simulator.adapter.indicator.registry import PandasIndicatorRegistry

    return PandasIndicatorRegistry({"ema": pd.Series(ema_vals)})


def _orders(**spec):
    """spec（lot_size / volume_min / volume_max / volume_step）で 1 バー分の発注を得る。"""
    from simulator.adapter.strategy.ma_slope import MaSlope

    strat = MaSlope()
    ind = _registry(_UPWARD_EMA)
    strat.on_init(dict(_BASE_CONFIG, **spec), ind)
    return strat.on_new_bar(2, ind, _Account())


@pytest.mark.parametrize(
    "volume_min, volume_step, label",
    [
        (0.1, 0.1, "現行 fixture 値（case.yaml / reconcile）"),
        (0.01, 0.01, "SymbolSpecCatalog のカタログ値"),
    ],
)
def test_current_specs_make_normalize_lot_an_identity(volume_min, volume_step, label):
    # 特性化テスト（Red 駆動ではない回帰固定）: 現行の銘柄仕様では NormalizeLot が
    # 恒等写像であり、golden（reconcile の trades/net/balance）が動かないことを固定する。
    # ISSUE-445 段階 1 の通過条件。
    # Act
    orders = _orders(
        lot_size=0.1, volume_min=volume_min, volume_max=100.0, volume_step=volume_step
    )

    # Assert: 厳密一致（1 ビットも動かないこと）
    assert len(orders) == 1
    assert orders[0].volume == 0.1, label


def test_true_spec_lifts_lot_below_volume_min_up_to_min():
    # Arrange: 実 MT5 の JP225 真値相当（min=1.0 / step=1.0 / max=10000）で lot=0.1。
    # 参照実装: MathRound(0.1/1.0)*1.0 = 0.0 → v < min → v = min = 1.0。
    # Act
    orders = _orders(lot_size=0.1, volume_min=1.0, volume_max=10000.0, volume_step=1.0)

    # Assert: 最小ロットへ持ち上がる（ISSUE-445 段階 2 の前提が成立することの証明）
    assert len(orders) == 1
    assert orders[0].volume == pytest.approx(1.0)


@pytest.mark.parametrize(
    "lot, expected",
    [
        (2.5, 3.0),  # Python round(2.5)=2（銀行家丸め）と分岐する境界
        (0.5, 1.0),  # Python round(0.5)=0 と分岐する境界
        (1.4, 1.0),  # 0.5 未満は切り捨て
    ],
)
def test_step_rounding_follows_math_round_half_away_from_zero(lot, expected):
    # Arrange: step=1.0・min=0.0（min 持ち上げが丸め結果を隠さない）・max=100.0。
    # 原典は MathRound（絶対値 0.5 を切り上げ）。Python 組込み round は銀行家丸めで
    # 2.5→2 / 0.5→0 となり原典と食い違う（実測: round(2.5)==2, round(0.5)==0）。
    # Act
    orders = _orders(lot_size=lot, volume_min=0.0, volume_max=100.0, volume_step=1.0)

    # Assert
    assert len(orders) == 1
    assert orders[0].volume == pytest.approx(expected)


def test_build_interactor_supplies_volume_spec_to_strategy_params(tmp_path):
    # Arrange: MaSlope は _normalize_lot で config["volume_min"/"volume_max"/"volume_step"]
    # を参照する。Composition Root（build_interactor）が strategy_params へ供給していなければ
    # 銘柄仕様が戦略に届かない（既存 point_size / digits / stops_level と同じ扱い）。
    # 重複定義を避けるため既存テストの kwargs ビルダを再利用する。
    from simulator.main import build_interactor
    from simulator.tests.unit.test_ea_factory_registry import _mt5_kwargs, _write_mt5_csv

    csv = _write_mt5_csv(tmp_path / "mt5.csv")

    # Act
    _controller, request = build_interactor(**_mt5_kwargs(csv, "MA_Slope_EA"))

    # Assert: build_interactor の引数（volume_min=0.1 / volume_max=100.0 / volume_step=0.1）が
    # そのまま解決できる
    assert request.config["volume_min"] == pytest.approx(0.1)
    assert request.config["volume_max"] == pytest.approx(100.0)
    assert request.config["volume_step"] == pytest.approx(0.1)


def test_non_positive_normalized_volume_places_no_order():
    # Arrange: 原典 OpenPosition は `volume = NormalizeLot(Lot); if(volume <= 0.0) return;`。
    # lot=0.0・min=0.0（持ち上げなし）・max=0.0（上限なし）・step=1.0 → volume=0.0。
    # Act
    orders = _orders(lot_size=0.0, volume_min=0.0, volume_max=0.0, volume_step=1.0)

    # Assert: 発注しない（Order を 1 件も返さない）
    assert orders == []


def test_step_digits_normalization_removes_binary_float_residue():
    # Arrange: step=0.1・lot=0.7。0.7/0.1 は 6.999999999999999（実測）、MathRound で 7、
    # 7*0.1 は 0.7000000000000001（実測）。原典は step 桁数
    # digits=ceil(-log10(0.1))=1 で NormalizeDouble し誤差を除去する。
    # Act
    orders = _orders(lot_size=0.7, volume_min=0.1, volume_max=100.0, volume_step=0.1)

    # Assert: 近似ではなく厳密一致（誤差が残らないこと自体が原典の要求）
    assert len(orders) == 1
    assert orders[0].volume == 0.7


def test_lot_above_volume_max_is_clamped_to_max():
    # Arrange: 原典 `if(max > 0.0 && v > max) v = max;`。step=1.0 で lot=5.0・max=2.0。
    # Act
    orders = _orders(lot_size=5.0, volume_min=1.0, volume_max=2.0, volume_step=1.0)

    # Assert: 上限で頭打ち
    assert len(orders) == 1
    assert orders[0].volume == pytest.approx(2.0)
