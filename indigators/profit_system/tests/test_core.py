"""profit_system 共有プリミティブの検証。

PS_GetLevelCountValue（``ps_level_count``）/ iBandsOnArray σ12（``compute_sigma_levels``）を
手計算可能な小入力で固定する（本ライブラリの独立な手計算で挙動を担保する。集約元・各
消費者との一致比較は集約により同一コードの再公開＝トートロジー化したため削除済み）。
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pytest

# src（profit_system 配下）
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src import (  # noqa: E402
    SIGMA_LEVELS,
    compute_marod,
    compute_sigma_levels,
    level_count_score,
    ps_level_count,
)
from src.core import (  # noqa: E402
    _ema,
    _ps_average,
    _ps_std_ema,
    _unit_conversion,
)


# =========================================================== _ema（手計算固定）
def test_ema_fixed_values_alpha_two_over_period_plus_one():
    # α=2/(period+1)。period=2 → α=2/3。
    # e[0]=1, e[1]=1+2/3·(2-1)=5/3, e[2]=5/3+2/3·(3-5/3)=23/9。
    out = _ema(np.array([1.0, 2.0, 3.0]), 2)
    assert np.allclose(out, [1.0, 5.0 / 3.0, 23.0 / 9.0])


# =========================================================== _ps_std_ema（手計算固定）
def test_ps_std_ema_fixed_value_ema_anchor():
    # length=2 → α=2/3。EMA=[2, 2+2/3·(8-2)=6] → 基準 ma=6。
    # sqrt(mean([(2-6)^2, (8-6)^2])) = sqrt((16+4)/2) = sqrt(10)。
    # （算術平均 5 基準なら sqrt(9)=3 となるため EMA 基準実装と判別できる。）
    assert _ps_std_ema(np.array([2.0, 8.0])) == pytest.approx(np.sqrt(10.0))


# =========================================================== 定数
def test_sigma_levels_constant():
    # σ 水準は 75/90/95/97.5/99/99.9% 信頼区間に対応する 6 値で固定。
    assert SIGMA_LEVELS == (0.67, 1.28, 1.65, 1.96, 2.58, 3.29)


# =========================================================== _unit_conversion
def test_unit_conversion_sign_symmetry():
    # 平均超 → 正、平均未満 → 負（符号付き標準化量）。
    avg, distant = 3.0, 329.0
    up = avg + 1.0      # band > avg → length > 0
    down = avg - 1.0    # band < avg → length < 0
    above = _unit_conversion(5.0, avg, up, distant, 1)    # UPSIDE
    below = _unit_conversion(1.0, avg, down, distant, 2)  # DOWNSIDE
    assert above > 0.0
    assert below < 0.0


def test_unit_conversion_zero_length_returns_zero():
    # band == avg のとき length=0 → ガードで 0 を返す。
    assert _unit_conversion(5.0, 3.0, 3.0, 329.0, 1) == 0.0


# =========================================================== ps_level_count（手計算）
def test_ps_level_count_initialization_zeroes_then_accumulates():
    # 平均と等しい要素は 0、平均超は正、平均未満は負（符号一致）。
    x = np.array([1.0, 2.0, 3.0, 4.0, 5.0])
    avg = _ps_average(x)
    lc = ps_level_count(x, initialization=True)
    # avg=3 → x[2]==avg は 0
    assert lc[2] == 0.0
    assert lc[0] < 0.0 and lc[1] < 0.0   # avg 未満
    assert lc[3] > 0.0 and lc[4] > 0.0   # avg 超
    assert _ps_std_ema(x) >= 0.0


def test_ps_level_count_accumulates_onto_res():
    # 同一系列を 2 回加算すると初回の 2 倍になる（res への加算動作）。
    x = np.array([1.0, 2.0, 4.0, 5.0])
    once = ps_level_count(x, None, initialization=True)
    twice = ps_level_count(x, once.copy(), initialization=False)
    assert np.allclose(twice, once * 2.0)


# =========================================================== compute_sigma_levels（手計算）
def test_compute_sigma_levels_keys_and_symmetry():
    x = np.array([-2.0, -1.0, 0.0, 1.0, 2.0])
    levels = compute_sigma_levels(x)
    mean = float(np.mean(x))
    std = float(np.sqrt(np.mean((x - mean) ** 2)))
    for sigma in SIGMA_LEVELS:
        key = f"{int(round(sigma * 100)):03d}"
        assert levels[f"up_{key}"] == pytest.approx(round(mean + std * sigma, 5))
        assert levels[f"dn_{key}"] == pytest.approx(round(mean - std * sigma, 5))


# =========================================================== level_count_score（funLevelCount 手計算）
def test_level_count_score_handcalc_case0():
    # case0: r=(250-50)/200=1.0; ((70-50)/1.0)/100 = 0.2
    assert level_count_score(70.0, 250.0, 0) == pytest.approx(0.2)


def test_level_count_score_handcalc_case1():
    # case1: r=(250-50)/200=1.0; -((50-30)/1.0)/100 = -0.2
    assert level_count_score(30.0, 250.0, 1) == pytest.approx(-0.2)


def test_level_count_score_handcalc_case2():
    # case2: r=(400/2)/200=1.0; ((10-1.0)/1.0)/100 = 0.09
    assert level_count_score(10.0, 400.0, 2) == pytest.approx(0.09)


def test_level_count_score_handcalc_case3():
    # case3: r=(400/2)/200=1.0; -((1.0-(-10))/1.0)/100 = -0.11
    assert level_count_score(-10.0, 400.0, 3) == pytest.approx(-0.11)


def test_level_count_score_degenerate_is_inf():
    # 退化入力（span==50 で r==0）はガードせず inf を返す（1:1 再現）。
    v = level_count_score(70.0, 50.0, 0)
    assert np.isinf(v)


# =========================================================== compute_marod（手計算）
def test_compute_marod_handcalc():
    # (typical-ma)/ma*100。typical=[110,90], ma=[100,100] -> [10.0, -10.0]
    typical = np.array([110.0, 90.0])
    ma = np.array([100.0, 100.0])
    np.testing.assert_array_equal(compute_marod(typical, ma), np.array([10.0, -10.0]))

