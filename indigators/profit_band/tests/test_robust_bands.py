"""robust_bands（正規化＋因果窓）の検証。

評価で実証した 2 欠陥が是正されていることを確認する:
  - 因果性: expanding で各足の値が未来に依存しない（接頭辞一致）。
  - スケール不変: return 正規化で帯幅率が一定・負価格が生じない。
  - 形状/分類/符号の整合、ATR・rolling の動作、異常系。
"""

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src import PROBABILITIES, build_robust_bands  # noqa: E402
from src.robust_bands import DEFAULT_BUCKETS  # noqa: E402


def _ohlc_df(n: int = 400, scale_trend: bool = False, seed: int = 0) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    base = 100 + np.cumsum(rng.normal(0, 1, n))
    if scale_trend:  # 価格水準を大きく変える（1→数百）
        base = 1.0 + np.exp(np.linspace(0, 6, n)) + rng.normal(0, 1, n)
        base = np.clip(base, 1.0, None)
    open_ = base
    close = open_ + rng.normal(0, base * 0.01)
    high = np.maximum(open_, close) + np.abs(rng.normal(0, base * 0.01))
    low = np.minimum(open_, close) - np.abs(rng.normal(0, base * 0.01))
    times = pd.date_range("2024-01-01", periods=n, freq="h")
    return pd.DataFrame({"date": times, "open": open_, "high": high,
                         "low": low, "close": close})


def test_shape_and_columns():
    df = _ohlc_df()
    b = build_robust_bands(df)
    assert list(b.columns) == [f"{bk}_{int(p*100)}"
                               for bk in DEFAULT_BUCKETS for p in PROBABILITIES]
    assert len(b) == len(df)
    assert b.index.equals(df.index)


def test_early_bars_nan_then_finite():
    df = _ohlc_df(n=300)
    b = build_robust_bands(df, min_obs=30)
    col = "pOL_95"
    # 初期は標本不足で NaN、後半は確定
    assert b[col].iloc[:5].isna().all()
    assert b[col].iloc[-1] == b[col].iloc[-1]  # not NaN


def test_causality_expanding_prefix_invariant():
    """expanding は未来非依存：df[:k] で計算した接頭辞が全件計算と一致する。"""
    df = _ohlc_df(n=300)
    full = build_robust_bands(df, window="expanding")
    k = 200
    prefix = build_robust_bands(df.iloc[:k], window="expanding")
    a = full["pOL_95"].to_numpy()[:k]
    c = prefix["pOL_95"].to_numpy()
    both = np.isfinite(a) & np.isfinite(c)
    assert both.any()
    assert np.allclose(a[both], c[both])


def test_return_scale_invariant_no_negative_price():
    """価格水準が激変するデータでも、return 正規化は負価格を生まない。"""
    df = _ohlc_df(n=400, scale_trend=True)
    b = build_robust_bands(df, normalize="return")
    finite = b.to_numpy()[np.isfinite(b.to_numpy())]
    assert (finite > 0).all()  # 不可能価格（負）が無い


def test_return_band_width_ratio_constant():
    """return 正規化では上側帯幅率 (pOL-open)/open が（確定区間で）ほぼ一定。"""
    df = _ohlc_df(n=400, scale_trend=True)
    b = build_robust_bands(df, normalize="return", window="expanding")
    o = df["open"].to_numpy()
    w = (b["pOL_95"].to_numpy() - o) / o
    w = w[np.isfinite(w)]
    # expanding で標本が増えると分位点は収束。後半の帯幅率の変動係数は小さい。
    tail = w[len(w) // 2:]
    assert np.std(tail) / np.mean(tail) < 0.25


def test_sign_direction():
    df = _ohlc_df(n=300)
    b = build_robust_bands(df)
    o = df["open"].to_numpy()
    fin = np.isfinite(b["pOL_95"].to_numpy())
    # pOL/pOH は始値の上、nOH/nOL は始値の下
    assert (b["pOL_95"].to_numpy()[fin] >= o[fin]).all()
    assert (b["pOH_95"].to_numpy()[fin] >= o[fin]).all()
    fin2 = np.isfinite(b["nOH_95"].to_numpy())
    assert (b["nOH_95"].to_numpy()[fin2] <= o[fin2]).all()
    assert (b["nOL_95"].to_numpy()[fin2] <= o[fin2]).all()


def test_atr_mode_runs():
    df = _ohlc_df(n=300)
    b = build_robust_bands(df, normalize="atr", atr_period=14)
    assert np.isfinite(b["pOL_95"].to_numpy()).any()


def test_rolling_window_runs():
    df = _ohlc_df(n=400)
    b = build_robust_bands(df, window=120, min_obs=30)
    assert np.isfinite(b["pOL_95"].to_numpy()).any()


def test_invalid_normalize_raises():
    with pytest.raises(ValueError):
        build_robust_bands(_ohlc_df(), normalize="zscore")


def test_unknown_bucket_raises():
    with pytest.raises(KeyError):
        build_robust_bands(_ohlc_df(), buckets=("XYZ",))


def test_missing_column_raises():
    df = _ohlc_df().drop(columns="close")
    with pytest.raises(KeyError):
        build_robust_bands(df)
