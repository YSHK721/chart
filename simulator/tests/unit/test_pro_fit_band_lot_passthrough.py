"""ProFitBand は lot を素通しする — 原典に正規化が無いことの回帰固定（ISSUE-445 段階 3-B）。

**性格**: 特性化テスト（Red 駆動ではない）。現行実装は既に原典に忠実であり、本ファイルは
「将来これを『バグだ』と誤認して ``NormalizeLot`` 相当を足す退行」を赤にするための検定である。

原典 ``simulator/experts/PRO!fit_Band.mq5``（実測・grep で全出現を確認）:

* ``:31``  ``input double   Lot=0.1;          // Lots to Trade``
* ``:419`` ``mrequest.volume = Lot;           // number of lots to trade``（買い）
* ``:513`` ``mrequest.volume = Lot;           // number of lots to trade``（売り）

``NormalizeLot`` / ``SYMBOL_VOLUME_MIN`` / ``SYMBOL_VOLUME_STEP`` / ``SYMBOL_VOLUME_MAX`` の
**出現は 0 件**であり、原典は入力 ``Lot`` を一切加工せず ``mrequest.volume`` に代入する。
``MA_Slope_EA.mq5`` / ``2026-03_ma-limit/ea.mq5`` / ``2026-04_stop-probe/ea.mq5`` が持つ
正規化は本 EA には**存在しない**。原典に無いものを足すことは参照実装違反であり、
ISSUE-445 の是正対象からも明示的に除外されている。

本検定は空虚ではない（負の対照）: 供給元 JP225 真値（volume_min=1.0 / volume_step=1.0）を
config に載せた状態で ``lot_size=0.1`` を検証しているため、正規化が実装されれば
``volume`` は 1.0 へ持ち上がって本テストは落ちる。
"""
from __future__ import annotations

import pandas as pd
import pytest


# 既存 test_strategy_pro_fit_band.py の _CONFIG と同値（buy 条件成立系列）に、
# 供給元 JP225 真値の銘柄仕様 3 キーを載せたもの。
_CONFIG_WITH_TRUE_VOLUME_SPEC = {
    "lot_size": 0.1,
    "stop_loss_points": 30,
    "take_profit_points": 100,
    "adx_min": 22.0,
    "point_size": 0.0001,
    "digits": 5,
    "min_bars": 2,
    # 供給元スナップショット marketdata/symbol_specs/OANDA-Japan-MT5-Live/JP225.json の真値。
    "volume_min": 1.0,
    "volume_max": 10000.0,
    "volume_step": 1.0,
}


class _Account:
    def __init__(self, sides=()):
        self.open_positions = [type("P", (), {"side": s})() for s in sides]


def _registry():
    from simulator.adapter.indicator.registry import PandasIndicatorRegistry

    # 買い条件 AND 充足（既存 test_strategy_pro_fit_band.py と同一の系列）。
    return PandasIndicatorRegistry(
        {
            "ema": pd.Series([1.0, 1.1, 1.2]),
            "adx": pd.Series([25.0, 25.0, 25.0]),
            "plus_di": pd.Series([30.0, 30.0, 30.0]),
            "minus_di": pd.Series([10.0, 10.0, 10.0]),
            "close": pd.Series([1.05, 1.15, 1.25]),
        }
    )


def test_lot_is_passed_through_unnormalized_even_under_true_symbol_spec():
    # Arrange: 原典 PRO!fit_Band.mq5 は Lot を素通しする（:419 / :513）。銘柄仕様は真値
    # （volume_min=1.0 / volume_step=1.0）を供給しているため、正規化があれば 1.0 になる。
    from simulator.adapter.strategy.pro_fit_band import ProFitBand

    strat = ProFitBand()
    ind = _registry()
    strat.on_init(_CONFIG_WITH_TRUE_VOLUME_SPEC, ind)

    # Act
    orders = strat.on_new_bar(2, ind, _Account())

    # Assert: 入力 Lot がそのまま volume になる（厳密一致）
    assert len(orders) == 1
    assert orders[0].volume == 0.1


def test_source_expert_contains_no_lot_normalization():
    # Arrange: 「原典に正規化が無い」という本ファイルの前提そのものを機械的に固定する
    # （原典が将来書き換わったら本テストが落ち、移植可否を再判断させる）。
    from pathlib import Path

    expert = (
        Path(__file__).resolve().parents[2] / "experts" / "PRO!fit_Band.mq5"
    )
    source = expert.read_text(encoding="utf-8", errors="replace")

    # Act / Assert: 正規化に関わる識別子が 1 つも現れない
    for token in (
        "NormalizeLot",
        "SYMBOL_VOLUME_MIN",
        "SYMBOL_VOLUME_MAX",
        "SYMBOL_VOLUME_STEP",
    ):
        assert token not in source, token
    # 負の対照: 素通しの代入は実在する（探索対象を取り違えていないことの証明）
    assert source.count("mrequest.volume = Lot;") == 2


@pytest.mark.parametrize("volume_min, volume_step", [(1.0, 1.0), (0.01, 0.01)])
def test_volume_is_independent_of_supplied_symbol_volume_spec(volume_min, volume_step):
    # Arrange: 銘柄仕様をどう供給しても volume は lot_size のまま（参照が存在しない）。
    from simulator.adapter.strategy.pro_fit_band import ProFitBand

    cfg = dict(
        _CONFIG_WITH_TRUE_VOLUME_SPEC,
        volume_min=volume_min,
        volume_step=volume_step,
    )
    strat = ProFitBand()
    ind = _registry()
    strat.on_init(cfg, ind)

    # Act
    orders = strat.on_new_bar(2, ind, _Account())

    # Assert
    assert orders[0].volume == 0.1
