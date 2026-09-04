"""MaSlopePending のロット正規化（原典 2026-03_ma-limit/ea.mq5）テスト（ISSUE-445 段階 3-B）。

参照実装（``simulator/tests/confirmation/2026-03_ma-limit/ea.mq5:299``）:

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

適用箇所（同 ``:180-195`` ``PlaceEntry``）— **発注のたびに**適用する:

    void PlaceEntry(const int direction)
      {
       double volume = NormalizeLot(Lot);
       if(volume <= 0.0) { Print(...); return; }        // 発注しない
       if(EntryType == ENTRY_MARKET) { OpenMarket(direction, volume); return; }
       OpenPending(direction, volume);
      }

本体は ``MA_Slope_EA.mq5:NormalizeLot()``（段階 1 で ``MaSlope`` へ移植済）と同一だが、
``2026-04_stop-probe/ea.mq5:159`` の同名関数とは**別物**である（``step <= 0`` の分岐と
``digits`` 式が異なる）。差異は ``test_normalize_lot_originals_diverge.py`` が固定する。
共通化してはならない（各戦略は自分の原典に忠実であること）。

銘柄仕様は ``strategy_params``（``build_interactor``）経由で ``volume_min`` / ``volume_max`` /
``volume_step`` として供給される（ISSUE-445 段階 1 で追加済・追加の供給経路は作らない）。
"""
from __future__ import annotations

import pandas as pd
import pytest


# JP225 想定: point_size=0.1・digits=1・SlopeMinPts=1.0 → threshold=0.1。
_BASE_CONFIG = {
    "slope_shift": 1,
    "slope_min_points": 1.0,
    "point_size": 0.1,
    "digits": 1,
    "stops_level": 0,
    "entry_type": "limit",
    "entry_offset_points": 50.0,
    "stop_loss_points": 200,
    "take_profit_points": 400,
    "lot_size": 0.1,
}

# 上向き傾き（ema[1]-ema[0]=0.3 > threshold 0.1）→ bar_index=2 で買いシグナル。
_UPWARD_EMA = [100.0, 100.3, 100.5]
_OPENS = [100.0, 100.0, 100.0]
_SPREADS = [50.0, 50.0, 50.0]


class _Account:
    def __init__(self, sides=()):
        self.open_positions = [type("P", (), {"side": s})() for s in sides]


def _indicators():
    return type(
        "Ind",
        (),
        {
            "_d": {
                "ema": pd.Series(_UPWARD_EMA, dtype=float),
                "open": pd.Series(_OPENS, dtype=float),
                "spread": pd.Series(_SPREADS, dtype=float),
            },
            "get": lambda self, name: self._d[name],
        },
    )()


def _orders(**spec):
    """spec（lot_size / volume_min / volume_max / volume_step）で 1 バー分の発注を得る。"""
    from simulator.adapter.strategy.ma_slope_pending import MaSlopePending

    strat = MaSlopePending()
    ind = _indicators()
    strat.on_init(dict(_BASE_CONFIG, **spec), ind)
    return strat.on_new_bar(2, ind, _Account())


# --- 特性化（Red 駆動ではない回帰固定）--------------------------------------

@pytest.mark.parametrize(
    "volume_min, volume_step, label",
    [
        (0.01, 0.01, "SymbolSpecCatalog 旧値 / reconcile.py:75-76 のリテラル"),
        (0.1, 0.1, "case.yaml 由来 fixture 値"),
    ],
)
def test_current_specs_make_normalize_lot_an_identity(volume_min, volume_step, label):
    # 現行の呼出値では NormalizeLot が恒等写像であり、confirmation golden
    # （2026-03_ma-limit reconcile: trades=1770 / net=-4610.0）が動かないことを固定する。
    # Act
    orders = _orders(
        lot_size=0.1, volume_min=volume_min, volume_max=100.0, volume_step=volume_step
    )

    # Assert: 厳密一致（1 ビットも動かないこと）
    assert len(orders) == 1
    assert orders[0].volume == 0.1, label


def test_missing_volume_spec_keys_leave_lot_untouched():
    # Arrange: 既存 config（volume_* キー未供給）との後方互換。未供給は 0.0＝制約なしとして
    # 原典の非正値分岐に載せる（min/max/step すべて 0 → 丸めも持ち上げも上限も無い）。
    # Act
    orders = _orders(lot_size=0.1)

    # Assert: digits=2（step<=0 の原典分岐）で丸めても 0.1 は不変
    assert len(orders) == 1
    assert orders[0].volume == 0.1


# --- Red 駆動 ---------------------------------------------------------------

def test_true_spec_lifts_lot_below_volume_min_up_to_min():
    # Arrange: 実 MT5 の JP225 真値（min=1.0 / step=1.0 / max=10000.0・供給元
    # marketdata/symbol_specs/OANDA-Japan-MT5-Live/JP225.json）で lot=0.1。
    # 参照実装: MathRound(0.1/1.0)*1.0 = 0.0 → v < min → v = min = 1.0。
    # Act
    orders = _orders(lot_size=0.1, volume_min=1.0, volume_max=10000.0, volume_step=1.0)

    # Assert: 最小ロットへ持ち上がる（ライブで発注不成立な 0.1 を出さない）
    assert len(orders) == 1
    assert orders[0].volume == pytest.approx(1.0)


def test_lot_above_volume_max_is_clamped_to_max():
    # Arrange: 原典 `if(max > 0.0 && v > max) v = max;`。step=1.0 で lot=5.0・max=2.0。
    # Act
    orders = _orders(lot_size=5.0, volume_min=1.0, volume_max=2.0, volume_step=1.0)

    # Assert: 上限で頭打ち
    assert len(orders) == 1
    assert orders[0].volume == pytest.approx(2.0)


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
    # 原典は MathRound（絶対値 0.5 を切り上げ）。
    # Act
    orders = _orders(lot_size=lot, volume_min=0.0, volume_max=100.0, volume_step=1.0)

    # Assert
    assert len(orders) == 1
    assert orders[0].volume == pytest.approx(expected)


def test_non_positive_step_normalizes_to_two_digits():
    # Arrange: **本原典固有の分岐**。`int digits = (step > 0.0) ? ... : 2;` により
    # step<=0 では digits=2 固定になる。step=0.0 / min=0.0 / max=0.0 / lot=0.123 →
    # 丸め無し → v=0.123 → NormalizeDouble(0.123, 2) = 0.12。
    # Act
    orders = _orders(lot_size=0.123, volume_min=0.0, volume_max=0.0, volume_step=0.0)

    # Assert
    assert len(orders) == 1
    assert orders[0].volume == pytest.approx(0.12)


def test_step_digits_normalization_removes_binary_float_residue():
    # Arrange: step=0.1・lot=0.7。0.7/0.1 は 6.999999999999999（実測）、MathRound で 7、
    # 7*0.1 は 0.7000000000000001（実測）。原典は digits=ceil(-log10(0.1))=1 で
    # NormalizeDouble し誤差を除去する。
    # Act
    orders = _orders(lot_size=0.7, volume_min=0.1, volume_max=100.0, volume_step=0.1)

    # Assert: 近似ではなく厳密一致（誤差が残らないこと自体が原典の要求）
    assert len(orders) == 1
    assert orders[0].volume == 0.7


def test_non_positive_normalized_volume_places_no_pending_order():
    # Arrange: 原典 PlaceEntry は `volume = NormalizeLot(Lot); if(volume <= 0.0) return;`
    # ＝**ペンディングを設置しない**（stop_entry_probe と違い起動失敗ではない）。
    # lot=0.4・min=0.0（持ち上げなし）・max=0.0（上限なし）・step=1.0 → MathRound(0.4)=0 → 0.0。
    # Act
    orders = _orders(lot_size=0.4, volume_min=0.0, volume_max=0.0, volume_step=1.0)

    # Assert: 発注しない（Order を 1 件も返さない）
    assert orders == []


def test_normalization_is_applied_per_order_not_cached_at_on_init():
    # Arrange: 原典は PlaceEntry（＝発注のたび）で NormalizeLot を呼ぶ。on_init で 1 回
    # 確定して保持する 2026-04_stop-probe とは適用箇所が異なる。config を差し替えると
    # 次の発注に反映されることで「毎回適用」を観測する。
    from simulator.adapter.strategy.ma_slope_pending import MaSlopePending

    cfg = dict(_BASE_CONFIG, lot_size=0.1, volume_min=0.0, volume_max=0.0, volume_step=0.0)
    ind = _indicators()
    strat = MaSlopePending()
    strat.on_init(cfg, ind)
    assert strat.on_new_bar(2, ind, _Account())[0].volume == pytest.approx(0.1)

    # Act: on_init を再度呼ばずに銘柄仕様を真値へ差し替える
    cfg["volume_min"] = 1.0
    cfg["volume_step"] = 1.0
    cfg["volume_max"] = 10000.0

    # Assert: 発注時点の仕様で正規化される（on_init 時点の値に固定されない）
    assert strat.on_new_bar(2, ind, _Account())[0].volume == pytest.approx(1.0)
