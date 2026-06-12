"""PRO!fit_OSI_MA core 層（純粋計算）の検証。

元 MQL4 ``PRO!fit_OSI_MA.mq4`` の MAKairi 計算を 1:1 で固定する。

元（series indexing, index0=最新）::

    ma = iMA(NULL,0,MAPeriod,0,MAMode,PRICE_CLOSE,i);
    if (ma != 0) MAKairi[i] = (Close[i+1] - ma) / ma * 100;

昇順（古→新）へ変換した一意定義（実装対象）::

    kairi[a] = (close[a-1] - ma_a) / ma_a * 100

分子は close[a] ではなく close[a-1]（1 本古い終値）であり、元コードの
``Close[i+1]`` 非対称性（PORTING_GUIDE §4.4）を意図的に再現する。

NaN 条件: ① ma_a == 0（ゼロ除算ガード）② a == 0（close[a-1] 不在）
③ ma_a が NaN（MA 未確定区間）。
"""

import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src import (  # noqa: E402
    DEFAULT_MA_MODE,
    DEFAULT_MA_PERIOD,
    MA_MODES,
    compute_osi_ma,
)


# --------------------------------------------------------------------------- 定数
def test_default_constants():
    # MAMode 既定=1(EMA), MAPeriod 既定=21（移植元 confirmed）。
    assert DEFAULT_MA_MODE == 1
    assert DEFAULT_MA_PERIOD == 21
    assert MA_MODES == {0: "SMA", 1: "EMA", 2: "SMMA", 3: "LWMA"}


# --------------------------------------------------------------------------- SMA 手計算固定
def test_sma_manual_fixture():
    # Arrange: N=6, period=3。MA(SMA) buffer = [0,0,20,25,31.6667,33.3333]。
    #   a<2 は ma==0（未確定）→ NaN。a==0 も close[a-1] 不在で NaN。
    #   kairi[a] = (close[a-1]-ma_a)/ma_a*100。
    close = np.array([10.0, 20.0, 30.0, 25.0, 40.0, 35.0])
    # Act
    out = compute_osi_ma(close, ma_mode=0, ma_period=3)
    # Assert
    assert np.isnan(out[0])  # a==0（最古バー）
    assert np.isnan(out[1])  # ma==0（未確定区間）
    assert out[2] == pytest.approx(0.0)  # (close[1]=20 - 20)/20*100 = 0
    assert out[3] == pytest.approx(20.0)  # (close[2]=30 - 25)/25*100 = 20
    assert out[4] == pytest.approx(-21.052631578947366)  # (25-31.6667)/31.6667*100
    assert out[5] == pytest.approx(20.0)  # (close[4]=40 - 33.3333)/33.3333*100


# --------------------------------------------------------------------------- 1 本ずれ保証
def test_numerator_uses_close_a_minus_1_not_close_a():
    # close[a-1] を使う実装と close[a] を使う実装で値が変わる入力。
    #   正しい (close[a-1]):  a=2 -> 0,  a=3 -> 20
    #   誤り   (close[a]  ):  a=2 -> 50, a=3 -> 0
    close = np.array([10.0, 20.0, 30.0, 25.0, 40.0, 35.0])
    out = compute_osi_ma(close, ma_mode=0, ma_period=3)
    # 正しい値（close[a-1]）であることを固定。誤実装なら 50.0 / 0.0 になる。
    assert out[2] == pytest.approx(0.0)
    assert out[3] == pytest.approx(20.0)
    # 念のため「誤実装の値」と一致しないことも明示。
    assert out[2] != pytest.approx(50.0)
    assert out[3] != pytest.approx(0.0)


# --------------------------------------------------------------------------- ma==0 ガード
def test_ma_zero_guard_all_zero_close():
    # 全 close=0 → SMA buffer 全要素 0.0 → 全 a で ma==0 → 全 NaN。
    close = np.zeros(6)
    out = compute_osi_ma(close, ma_mode=0, ma_period=3)
    assert np.all(np.isnan(out))


# --------------------------------------------------------------------------- EMA 整合
def test_ema_matches_on_buffer_derivation():
    # EMA は exponential_ma_on_buffer 出力から導出した期待値で固定。
    #   EMA ma = [10,15,22.5,23.75,31.875,33.4375]。
    #   EMA は index0 が price[0]=10≠0 だが a==0 ガードで out[0] は NaN。
    close = np.array([10.0, 20.0, 30.0, 25.0, 40.0, 35.0])
    out = compute_osi_ma(close, ma_mode=1, ma_period=3)
    assert np.isnan(out[0])  # a==0
    assert out[1] == pytest.approx(-33.33333333333333)  # (10-15)/15*100
    assert out[2] == pytest.approx(-11.11111111111111)  # (20-22.5)/22.5*100
    assert out[3] == pytest.approx(26.31578947368421)   # (30-23.75)/23.75*100
    assert out[4] == pytest.approx(-21.568627450980394)  # (25-31.875)/31.875*100
    assert out[5] == pytest.approx(19.626168224299064)   # (40-33.4375)/33.4375*100


# --------------------------------------------------------------------------- SMMA 整合
def test_smma_matches_on_buffer_derivation():
    # SMMA ma = [0,0,20,21.6667,27.7778,30.1852]。a<2 は ma==0→NaN。
    close = np.array([10.0, 20.0, 30.0, 25.0, 40.0, 35.0])
    out = compute_osi_ma(close, ma_mode=2, ma_period=3)
    assert np.isnan(out[0])
    assert np.isnan(out[1])
    assert out[2] == pytest.approx((20.0 - 20.0) / 20.0 * 100)
    assert out[3] == pytest.approx((30.0 - 21.666666666666668) / 21.666666666666668 * 100)
    assert out[4] == pytest.approx((25.0 - 27.77777777777778) / 27.77777777777778 * 100)
    assert out[5] == pytest.approx((40.0 - 30.185185185185187) / 30.185185185185187 * 100)


# --------------------------------------------------------------------------- LWMA 整合
def test_lwma_matches_on_buffer_derivation():
    # LWMA ma = [0,0,23.3333,25.8333,33.3333,35.0]。a<2 は ma==0→NaN。
    close = np.array([10.0, 20.0, 30.0, 25.0, 40.0, 35.0])
    out = compute_osi_ma(close, ma_mode=3, ma_period=3)
    assert np.isnan(out[0])
    assert np.isnan(out[1])
    assert out[2] == pytest.approx((20.0 - 23.333333333333332) / 23.333333333333332 * 100)
    assert out[3] == pytest.approx((30.0 - 25.833333333333332) / 25.833333333333332 * 100)
    assert out[4] == pytest.approx((25.0 - 33.333333333333336) / 33.333333333333336 * 100)
    assert out[5] == pytest.approx((40.0 - 35.0) / 35.0 * 100)


# --------------------------------------------------------------------------- 既定値
def test_defaults_used_when_omitted(monkeypatch):
    # ma_mode/ma_period 省略時に既定（EMA, 21）が使われる。
    # 既定 period=21 では N=6 は未確定（EMA は計算されるが SMA 系は ma==0）。
    # ここでは「例外なく既定で実行され配列長を返す」ことのみ固定。
    close = np.arange(1.0, 31.0)  # N=30 >= 21
    out_default = compute_osi_ma(close)
    out_explicit = compute_osi_ma(close, ma_mode=1, ma_period=21)
    assert out_default.shape == close.shape
    np.testing.assert_array_equal(
        np.nan_to_num(out_default, nan=-999.0),
        np.nan_to_num(out_explicit, nan=-999.0),
    )


# --------------------------------------------------------------------------- 異常系
def test_unknown_ma_mode_raises_value_error():
    close = np.array([10.0, 20.0, 30.0, 25.0, 40.0, 35.0])
    with pytest.raises(ValueError):
        compute_osi_ma(close, ma_mode=4, ma_period=3)
    with pytest.raises(ValueError):
        compute_osi_ma(close, ma_mode=-1, ma_period=3)


def test_non_positive_period_raises_value_error():
    close = np.array([10.0, 20.0, 30.0, 25.0, 40.0, 35.0])
    with pytest.raises(ValueError):
        compute_osi_ma(close, ma_mode=0, ma_period=0)
    with pytest.raises(ValueError):
        compute_osi_ma(close, ma_mode=0, ma_period=-3)


def test_empty_close_returns_empty():
    # 空配列 → 空配列（挙動を固定）。
    out = compute_osi_ma(np.array([]), ma_mode=0, ma_period=3)
    assert out.shape == (0,)
