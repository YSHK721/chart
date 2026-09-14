"""StopEntryProbe のロット正規化（原典 2026-04_stop-probe/ea.mq5）テスト（ISSUE-445 段階 3-B）。

参照実装（``simulator/tests/confirmation/2026-04_stop-probe/ea.mq5:159``）— **`MA_Slope_EA.mq5` /
`2026-03_ma-limit/ea.mq5` の同名関数とは別物**である::

    double NormalizeLot(const double lot, const double vmin, const double vmax, double vstep)
      {
       if(vstep <= 0.0)
          vstep = (vmin > 0.0) ? vmin : 0.01;      // ← 丸めをスキップせず必ず丸める
       double v = MathRound(lot / vstep) * vstep;
       if(v < vmin) v = vmin;
       if(vmax > 0.0 && v > vmax) v = vmax;
       int digits = (int)MathMax(0.0, MathCeil(-MathLog10(vstep) - 1e-9));  // ← 1e-9 を引く
       return(NormalizeDouble(v, digits));
      }

適用箇所（同 ``:51-91`` ``OnInit``）— **起動時に 1 回**適用して ``g_lot`` に保持する::

    if(Lot <= 0.0) { Print(...); return(INIT_PARAMETERS_INCORRECT); }
    ...
    g_lot = NormalizeLot(Lot, vmin, vmax, vstep);
    if(g_lot <= 0.0) { PrintFormat(...); return(INIT_PARAMETERS_INCORRECT); }

発注（``:138`` / ``:149``）は ``trade.BuyStop(g_lot, ...)`` / ``trade.SellStop(g_lot, ...)`` で
保持値を使う。よって「発注のたびに正規化する」``MaSlopePending`` とは適用箇所が異なる。
``INIT_PARAMETERS_INCORRECT`` は Python 側では ``ConfigError``（``MaSlope.on_init`` が
SL/TP>0 を拒否するのと同じ扱い）に対応させる。

銘柄仕様は ``strategy_params``（``build_interactor``）経由で ``volume_min`` / ``volume_max`` /
``volume_step`` として供給される（ISSUE-445 段階 1 で追加済・追加の供給経路は作らない）。
"""
from __future__ import annotations

import pytest

from simulator.domain.exceptions import ConfigError


# JP225 想定（既存 test_stop_entry_probe.py の _cfg と同値）。
_BASE_CONFIG = {
    "point_size": 0.1,
    "digits": 1,
    "stops_level": 0,
    "entry_offset_points": 100.0,  # ×0.1 = 10
    "stop_loss_points": 200,
    "take_profit_points": 500,
    "lot_size": 0.1,
}

_BID = 52969.8
_ASK = 52974.8


def _volumes(**spec):
    """spec（lot_size / volume_min / volume_max / volume_step）で装填 2 件の volume を得る。"""
    from simulator.adapter.strategy.stop_entry_probe import StopEntryProbe

    strat = StopEntryProbe()
    strat.on_init(dict(_BASE_CONFIG, **spec), None)
    orders = strat.on_tick(0, _BID, _ASK, None)
    return [o.volume for o in orders]


# --- 特性化（Red 駆動ではない回帰固定）--------------------------------------

def test_current_spec_makes_normalize_lot_an_identity():
    # 現行の呼出値（reconcile.py:103-104 / test_is_oos_stop_probe.py のリテラル
    # volume_min=0.01 / volume_step=0.01 / volume_max=100.0・lot=0.1）では恒等写像であり、
    # confirmation golden（2026-04_stop-probe: trades=10100 / net=9990.0）が動かない。
    # Act
    volumes = _volumes(lot_size=0.1, volume_min=0.01, volume_max=100.0, volume_step=0.01)

    # Assert: 厳密一致（1 ビットも動かないこと）
    assert volumes == [0.1, 0.1]


def test_missing_volume_spec_keys_leave_lot_untouched():
    # Arrange: 既存 config（volume_* キー未供給＝すべて 0.0）との後方互換。
    # 原典は vstep<=0 かつ vmin<=0 で vstep=0.01 に置換する →
    # MathRound(0.1/0.01)*0.01 = 0.1・digits=2 → 0.1（恒等）。
    # Act
    volumes = _volumes(lot_size=0.1)

    # Assert
    assert volumes == [0.1, 0.1]


# --- Red 駆動 ---------------------------------------------------------------

def test_true_spec_lifts_lot_below_volume_min_up_to_min():
    # Arrange: 実 MT5 の JP225 真値（min=1.0 / step=1.0 / max=10000.0・供給元
    # marketdata/symbol_specs/OANDA-Japan-MT5-Live/JP225.json）で lot=0.1。
    # 参照実装: MathRound(0.1/1.0)*1.0 = 0.0 → v < vmin → v = 1.0。
    # Act
    volumes = _volumes(lot_size=0.1, volume_min=1.0, volume_max=10000.0, volume_step=1.0)

    # Assert: 最小ロットへ持ち上がる（ライブで発注不成立な 0.1 を出さない）
    assert volumes == pytest.approx([1.0, 1.0])


def test_lot_above_volume_max_is_clamped_to_max():
    # Arrange: 原典 `if(vmax > 0.0 && v > vmax) v = vmax;`。step=1.0 で lot=5.0・max=2.0。
    # Act
    volumes = _volumes(lot_size=5.0, volume_min=1.0, volume_max=2.0, volume_step=1.0)

    # Assert
    assert volumes == pytest.approx([2.0, 2.0])


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
    # Act
    volumes = _volumes(lot_size=lot, volume_min=0.0, volume_max=100.0, volume_step=1.0)

    # Assert
    assert volumes == pytest.approx([expected, expected])


def test_non_positive_step_is_replaced_by_volume_min_and_still_rounds():
    # Arrange: **本原典固有の分岐**。`if(vstep <= 0.0) vstep = (vmin > 0.0) ? vmin : 0.01;`
    # により丸めはスキップされない。step=0.0 / min=3.0 / max=0.0 / lot=10.0 →
    # vstep=3.0 → MathRound(10/3)*3 = 3*3 = 9.0 → v<vmin(3.0) でない →
    # digits = max(0, ceil(-log10(3.0) - 1e-9)) = 0 → 9.0。
    # （2026-03_ma-limit/ea.mq5 は丸めをスキップするため 10.0 になる。
    #   差異は test_normalize_lot_originals_diverge.py が固定する。）
    # Act
    volumes = _volumes(lot_size=10.0, volume_min=3.0, volume_max=0.0, volume_step=0.0)

    # Assert
    assert volumes == pytest.approx([9.0, 9.0])


def test_non_positive_step_falls_back_to_one_hundredth_when_min_is_also_non_positive():
    # Arrange: 原典 `(vmin > 0.0) ? vmin : 0.01`。step=0.0 / min=0.0 / lot=0.125 →
    # vstep=0.01 → MathRound(12.5)=13 → 0.13・digits=2 → 0.13。
    # Act
    volumes = _volumes(lot_size=0.125, volume_min=0.0, volume_max=0.0, volume_step=0.0)

    # Assert
    assert volumes == pytest.approx([0.13, 0.13])


def test_step_digits_normalization_removes_binary_float_residue():
    # Arrange: step=0.1・lot=0.7。0.7/0.1 は 6.999999999999999（実測）、MathRound で 7、
    # 7*0.1 は 0.7000000000000001（実測）。digits = max(0, ceil(-log10(0.1)-1e-9)) = 1。
    # Act
    volumes = _volumes(lot_size=0.7, volume_min=0.1, volume_max=100.0, volume_step=0.1)

    # Assert: 近似ではなく厳密一致（誤差が残らないこと自体が原典の要求）
    assert volumes == [0.7, 0.7]


# --- OnInit の分岐（INIT_PARAMETERS_INCORRECT → ConfigError）----------------

def test_non_positive_lot_input_is_rejected_at_on_init():
    # Arrange: 原典 OnInit:53-57 `if(Lot <= 0.0) return(INIT_PARAMETERS_INCORRECT);`
    # ＝正規化する前の入力値そのものの検査。
    from simulator.adapter.strategy.stop_entry_probe import StopEntryProbe

    strat = StopEntryProbe()

    # Act / Assert: 起動失敗（発注スキップではない）
    with pytest.raises(ConfigError):
        strat.on_init(dict(_BASE_CONFIG, lot_size=0.0), None)


def test_normalized_lot_collapsing_to_zero_is_rejected_at_on_init():
    # Arrange: 原典 OnInit:69-73 `g_lot = NormalizeLot(...); if(g_lot <= 0.0)
    # return(INIT_PARAMETERS_INCORRECT);`。lot=0.4（>0 なので前段の検査は通る）/
    # min=0.0 / max=0.0 / step=1.0 → MathRound(0.4)=0 → g_lot=0.0。
    from simulator.adapter.strategy.stop_entry_probe import StopEntryProbe

    strat = StopEntryProbe()
    cfg = dict(
        _BASE_CONFIG, lot_size=0.4, volume_min=0.0, volume_max=0.0, volume_step=1.0
    )

    # Act / Assert
    with pytest.raises(ConfigError):
        strat.on_init(cfg, None)


def test_lot_is_normalized_once_at_on_init_and_held():
    # Arrange: 原典は OnInit で g_lot を確定し、以後 trade.BuyStop(g_lot, ...) が保持値を使う
    # （発注のたびに正規化する MaSlopePending との適用箇所の差）。on_init 後に config の
    # 銘柄仕様を差し替えても発注ロットが変わらないことで「1 回だけ適用」を観測する。
    from simulator.adapter.strategy.stop_entry_probe import StopEntryProbe

    # 起動時の仕様は真値（min=1.0 / step=1.0）→ g_lot は 0.1 から 1.0 へ持ち上がる。
    cfg = dict(
        _BASE_CONFIG, lot_size=0.1, volume_min=1.0, volume_max=10000.0, volume_step=1.0
    )
    strat = StopEntryProbe()
    strat.on_init(cfg, None)
    assert [o.volume for o in strat.on_tick(0, _BID, _ASK, None)] == pytest.approx(
        [1.0, 1.0]
    )

    # Act: on_init を再度呼ばずに銘柄仕様を旧カタログ値へ差し替える
    cfg["volume_min"] = 0.01
    cfg["volume_step"] = 0.01
    cfg["volume_max"] = 100.0

    # Assert: 起動時に確定した g_lot のまま（発注ごとに再正規化しない）
    assert [o.volume for o in strat.on_tick(0, _BID, _ASK, None)] == pytest.approx(
        [1.0, 1.0]
    )
