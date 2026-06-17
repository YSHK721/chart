"""adapter/indicator/ema_adx_di.py の ADX(8)/+DI/−DI 指標テスト（SPEC §3.5 / §9・PROCESS §1.2）.

#5 PRO!fit_Band が参照する ``iADX(NULL,0,ADX_Period)`` の 3 バッファ
(buf0=ADX 本線 / buf1=+DI / buf2=−DI) を Wilder 式（MetaQuotes iADX 再現）で
算出する ``compute_adx_with_di(high, low, close, period) -> (adx, plus_di, minus_di)``.

トートロジー回避（一次情報照合）:
    ADX 本線は移植元 ``indigators/profit_adx_needle/src/core.py`` の
    ``compute_adx``（Wilder 式 ADX 本線・MetaQuotes 再現）と一致しなければならない。
    本テストは production と独立に compute_adx を直接呼び出して期待値を得る
    （自己一致＝self-fulfilling を排除）。+DI/−DI は同 core の文書化された中間式
    （+DM/−DM の非対称ゼロ化・TR・100*DM/TR・EMA α=2/(period+1)）を独立に再導出し
    照合する。

warmup 方針（SPEC §1.2/§9・既存 EMA 方針と整合）:
    EMA ベースの ADX/+DI/−DI は index0 から再帰定義され warmup NaN を持たない
    （MQL 忠実・既存 moving_averages EMA と同方針）。先頭バー(i=0)は前足が無いため
    +DM/−DM/TR=0 で開始する（compute_adx と同一）。

TDD AAA 構造。F.I.R.S.T。
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest


def _profit_adx_compute_adx(high, low, close, period):
    """一次情報照合用: 移植元 profit_adx_needle.compute_adx を直接呼ぶ独立オラクル。

    production（compute_adx_with_di）とは別経路で ADX 本線の期待値を得る。
    これにより self-fulfilling（production と同じ式を再実装した自己一致）を排除する。
    """
    src = str(Path(__file__).resolve().parents[3] / "indigators" / "profit_adx_needle" / "src")
    if src not in sys.path:
        sys.path.insert(0, src)
    from core import compute_adx  # noqa: E402

    return compute_adx(
        np.asarray(high, dtype=float),
        np.asarray(low, dtype=float),
        np.asarray(close, dtype=float),
        period,
    )


def _independent_di(high, low, close, period):
    """独立再導出オラクル: +DI/−DI を SPEC §9 / core.py:139-171 の中間式で算出する.

    production に依存せず、文書化された Wilder/MetaQuotes 中間式を直書きで再現:
        +DM = max(H[i]-H[i-1], 0), −DM = max(L[i-1]-L[i], 0)  （非対称ゼロ化）
        TR  = max(|H-L|, |H-prevC|, |L-prevC|)
        +SDI = 100*(+DM)/TR, −SDI = 100*(−DM)/TR  （TR=0 は 0）
        +DI = EMA(+SDI), −DI = EMA(−SDI)  （α=2/(period+1), seed=index0）
    """
    h = np.asarray(high, dtype=float)
    lo = np.asarray(low, dtype=float)
    c = np.asarray(close, dtype=float)
    n = h.size
    pdm = np.zeros(n)
    mdm = np.zeros(n)
    tr = np.zeros(n)
    up = h[1:] - h[:-1]
    dn = lo[:-1] - lo[1:]
    p = np.where(up < 0.0, 0.0, up)
    m = np.where(dn < 0.0, 0.0, dn)
    eq = p == m
    p_lt = p < m
    pdm[1:] = np.where(eq, 0.0, np.where(p_lt, 0.0, p))
    mdm[1:] = np.where(eq, 0.0, np.where(p_lt, m, 0.0))
    hl = np.abs(h[1:] - lo[1:])
    hc = np.abs(h[1:] - c[:-1])
    lc = np.abs(lo[1:] - c[:-1])
    tr[1:] = np.maximum(np.maximum(hl, hc), lc)
    with np.errstate(divide="ignore", invalid="ignore"):
        sdi_p = np.where(tr > 0.0, 100.0 * pdm / tr, 0.0)
        sdi_m = np.where(tr > 0.0, 100.0 * mdm / tr, 0.0)

    def _ema(v, per):
        out = np.empty(v.size)
        alpha = 2.0 / (per + 1.0)
        out[0] = v[0]
        for k in range(1, v.size):
            out[k] = out[k - 1] + alpha * (v[k] - out[k - 1])
        return out

    return _ema(sdi_p, period), _ema(sdi_m, period)


# 既知の小さな OHLC 系列（昇順・8 本）。
_HIGH = [10.0, 11.0, 12.0, 11.5, 13.0, 12.0, 14.0, 13.5]
_LOW = [9.0, 9.5, 10.0, 10.5, 11.0, 11.0, 12.0, 12.5]
_CLOSE = [9.5, 10.5, 11.5, 11.0, 12.5, 11.5, 13.5, 13.0]


def _series():
    return (pd.Series(_HIGH), pd.Series(_LOW), pd.Series(_CLOSE))


# --- TD.1 正常系: ADX 本線が一次情報 compute_adx と一致（トートロジー回避）-------

def test_adx_main_line_matches_profit_adx_needle_compute_adx():
    # Arrange: 期待値は移植元 profit_adx_needle.compute_adx の独立呼び出し
    from backtest.adapter.indicator.ema_adx_di import compute_adx_with_di

    high, low, close = _series()
    expected_adx = _profit_adx_compute_adx(_HIGH, _LOW, _CLOSE, period=4)

    # Act
    adx, _plus_di, _minus_di = compute_adx_with_di(high, low, close, period=4)

    # Assert: ADX 本線が一次情報と完全一致（self-fulfilling でない外部オラクル照合）
    np.testing.assert_allclose(np.asarray(adx, dtype=float), expected_adx, rtol=1e-12)


# --- TD.1 正常系: +DI/−DI が独立再導出した Wilder 中間式と一致 -----------------

def test_plus_minus_di_match_independent_wilder_derivation():
    # Arrange: production 非依存の独立再導出（SPEC §9 / core.py:139-171）
    from backtest.adapter.indicator.ema_adx_di import compute_adx_with_di

    high, low, close = _series()
    exp_pdi, exp_mdi = _independent_di(_HIGH, _LOW, _CLOSE, period=4)

    # Act
    _adx, plus_di, minus_di = compute_adx_with_di(high, low, close, period=4)

    # Assert
    np.testing.assert_allclose(np.asarray(plus_di, dtype=float), exp_pdi, rtol=1e-12)
    np.testing.assert_allclose(np.asarray(minus_di, dtype=float), exp_mdi, rtol=1e-12)


# --- TD.1 正常系: 戻り値は pandas.Series ×3 で入力 index に整合（registry 登録形）-

def test_returns_three_series_aligned_to_input_index():
    # Arrange
    from backtest.adapter.indicator.ema_adx_di import compute_adx_with_di

    high, low, close = _series()

    # Act
    adx, plus_di, minus_di = compute_adx_with_di(high, low, close, period=4)

    # Assert: 3 本とも pandas.Series・入力 index と同一・同長（PandasIndicatorRegistry 登録形）
    for s in (adx, plus_di, minus_di):
        assert isinstance(s, pd.Series)
        assert list(s.index) == list(high.index)
        assert len(s) == len(high)


# --- TD.2 境界値: warmup（先頭バー）の扱い（MQL 忠実・index0 から定義）---------

def test_warmup_first_bar_is_zero_not_nan_mql_faithful():
    # Arrange: SPEC §9 / 既存 EMA 方針＝EMA ベースは index0 から再帰定義（warmup NaN なし）。
    # 先頭バー(i=0)は前足が無いため +DM/−DM/TR=0 → +DI/−DI/ADX は 0 始点（compute_adx と同一）。
    from backtest.adapter.indicator.ema_adx_di import compute_adx_with_di

    high, low, close = _series()

    # Act
    adx, plus_di, minus_di = compute_adx_with_di(high, low, close, period=4)

    # Assert: index0 は warmup として 0.0（NaN ではない・MQL 忠実 index0 定義）
    assert adx.iloc[0] == 0.0
    assert plus_di.iloc[0] == 0.0
    assert minus_di.iloc[0] == 0.0
    # warmup より後に NaN を持たない（全数 finite・registry が NaN 破損検出しない）
    assert np.isfinite(np.asarray(adx, dtype=float)).all()
    assert np.isfinite(np.asarray(plus_di, dtype=float)).all()
    assert np.isfinite(np.asarray(minus_di, dtype=float)).all()


# --- TD.4 異常系: period 下限（period<=0）------------------------------------

def test_period_le_0_raises_value_error():
    # Arrange: compute_adx は period<=0 で ValueError（core.py:136-137）。adapter も踏襲。
    from backtest.adapter.indicator.ema_adx_di import compute_adx_with_di

    high, low, close = _series()

    # Act / Assert
    with pytest.raises(ValueError):
        compute_adx_with_di(high, low, close, period=0)


# --- TD.4 異常系: 長さ不一致 -------------------------------------------------

def test_length_mismatch_raises_value_error():
    # Arrange: HLC の長さ不一致は ValueError（compute_adx core.py:131-132 と整合）
    from backtest.adapter.indicator.ema_adx_di import compute_adx_with_di

    high = pd.Series([10.0, 11.0, 12.0])
    low = pd.Series([9.0, 9.5])
    close = pd.Series([9.5, 10.5, 11.5])

    # Act / Assert
    with pytest.raises(ValueError):
        compute_adx_with_di(high, low, close, period=2)


# --- TD.1 不変条件（production 非依存）: +DI/−DI の方向性 ---------------------
# レビュー 🟡-1: _independent_di オラクルは production と行単位同一でトートロジー。
# ADX 式は +DI/−DI の対称入替・等倍で不変なため個別 DI 値の方向性を保証しない。
# ここでは「単調上昇系列なら +DI が優勢・単調下降系列なら −DI が優勢」という、
# 実装の式と独立に成立すべき概念的性質を固定し、+DI/−DI の取り違え・符号誤りを禁止する。
# 現行実装は既にこの性質を満たすため特性化（characterization）として追加する。

def test_monotone_uptrend_makes_plus_di_dominate_minus_di():
    # Arrange: 各バーで高値・安値が単調上昇（+DM のみ発生・−DM=0）の決定論系列
    from backtest.adapter.indicator.ema_adx_di import compute_adx_with_di

    n = 30
    high = pd.Series([10.0 + i for i in range(n)])
    low = pd.Series([9.0 + i for i in range(n)])
    close = pd.Series([9.5 + i for i in range(n)])

    # Act
    _adx, plus_di, minus_di = compute_adx_with_di(high, low, close, period=8)

    # Assert: 上昇トレンドでは +DI が −DI を上回る（方向性・実装の式と独立な性質）
    assert plus_di.iloc[-1] > minus_di.iloc[-1]


def test_monotone_downtrend_makes_minus_di_dominate_plus_di():
    # Arrange: 各バーで高値・安値が単調下降（−DM のみ発生・+DM=0）の決定論系列
    from backtest.adapter.indicator.ema_adx_di import compute_adx_with_di

    n = 30
    high = pd.Series([10.0 + (n - i) for i in range(n)])
    low = pd.Series([9.0 + (n - i) for i in range(n)])
    close = pd.Series([9.5 + (n - i) for i in range(n)])

    # Act
    _adx, plus_di, minus_di = compute_adx_with_di(high, low, close, period=8)

    # Assert: 下降トレンドでは −DI が +DI を上回る（方向性・実装の式と独立な性質）
    assert minus_di.iloc[-1] > plus_di.iloc[-1]


# --- TD.2 境界値（production 非依存）: +DM/−DM 非対称ゼロ化の同値境界 ----------
# +DM=max(H[i]-H[i-1],0) と −DM=max(L[i-1]-L[i],0) が等しいバーは「方向なし」と扱い
# 両 DM を 0 化する（非対称ゼロ化の同値境界）。完全な対称運動（H 上昇=L 下降が等量）の
# 系列では +DI と −DI が一致する（方向性ゼロ）ことを実装の式と独立に固定する。

def test_symmetric_equal_dm_yields_equal_plus_and_minus_di():
    # Arrange: 各バー H が +1・L が −1 で対称に動く（+DM=1, −DM=1 で同値 → 両 0 化）
    n = 12
    high = pd.Series([10.0 + i for i in range(n)])
    low = pd.Series([9.0 - i for i in range(n)])
    # close は TR>0 を保つだけ（DI 方向性には DM が支配的）
    close = pd.Series([9.5 for _ in range(n)])
    from backtest.adapter.indicator.ema_adx_di import compute_adx_with_di

    # Act
    _adx, plus_di, minus_di = compute_adx_with_di(high, low, close, period=8)

    # Assert: 同値 DM は両 0 化されるため +DI と −DI は終始一致（方向性ゼロ）
    np.testing.assert_allclose(
        np.asarray(plus_di, dtype=float), np.asarray(minus_di, dtype=float), atol=1e-12
    )
