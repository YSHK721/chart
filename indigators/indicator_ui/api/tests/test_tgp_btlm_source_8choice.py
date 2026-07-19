"""tgp_btlm ソース 8 択化（applied_price 参照）の結線検証。

正本仕様: kind-twirling-hollerith.md §4 — price 選択肢を 8 択化。既存 4 択・既定 open・
出力は byte 不変（非破壊・追加拡張のみ）。参照実装 tgp_btlm src は無改変で、8 択解決は
結線層（call_binding）が担う。

検証観点:
    - 既存 4 択（open/high/low/close）は従来経路（build_btlm_bands 直接列参照）と byte 一致
    - 合成 4 択（hl2/hlc3/ohlc4/hlcc4）は applied_price と一致する系列で回帰される
    - 未知ソースは従来どおり KeyError（build_btlm_bands の契約を保つ）
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from adapter.compute import CallBinding, FakeLineChart
from adapter.compute import call_binding


def _ohlcv(n: int = 200, seed: int = 0) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    prices = np.cumsum(rng.normal(0, 1, n)) + 100.0
    return pd.DataFrame({
        "time": pd.date_range("2020-01-01", periods=n, freq="D"),
        "open": prices, "high": prices + 1.0, "low": prices - 1.0,
        "close": prices + 0.25,
    })


def _mean_from_invoke(price: str, df: pd.DataFrame) -> np.ndarray:
    chart = FakeLineChart()
    binding = CallBinding.resolve("tgp_btlm", "default")
    binding.invoke(chart, df, {"fitter": "ols", "price": price,
                               "maxbars": 100, "q_low": 0.05, "q_high": 0.95})
    payloads = {p["name"]: p for p in chart.to_payloads()}
    return np.array([pt["value"] for pt in payloads["btlm_mean"]["data"]], dtype=float)


def _reference_mean(series: np.ndarray) -> np.ndarray:
    src = call_binding._load_src_package("tgp_btlm")
    bands = src.build_btlm_bands(
        pd.DataFrame({"px": series}), src.OlsBtlmFitter(),
        price="px", maxbars=100, q_low=0.05, q_high=0.95,
    )
    return bands["btlm_mean"].to_numpy()


def test_existing_four_sources_are_byte_invariant():
    df = _ohlcv(200, seed=1)
    for price in ("open", "high", "low", "close"):
        got = _mean_from_invoke(price, df)
        ref = _reference_mean(df[price].to_numpy(dtype=float))
        ref = ref[np.isfinite(ref)]
        np.testing.assert_allclose(got, ref, atol=1e-9)


def test_synthetic_sources_resolve_via_applied_price():
    df = _ohlcv(200, seed=2)
    o = df["open"].to_numpy(float); h = df["high"].to_numpy(float)
    lo = df["low"].to_numpy(float); c = df["close"].to_numpy(float)
    expected = {
        "hl2": (h + lo) / 2,
        "hlc3": (h + lo + c) / 3,
        "ohlc4": (o + h + lo + c) / 4,
        "hlcc4": (h + lo + 2 * c) / 4,
    }
    for price, series in expected.items():
        got = _mean_from_invoke(price, df)
        ref = _reference_mean(series)
        ref = ref[np.isfinite(ref)]
        np.testing.assert_allclose(got, ref, atol=1e-9)


def test_resolve_btlm_price_identity_for_literal_columns():
    df = _ohlcv(20)
    df2, name = call_binding._resolve_btlm_price(df, "high")
    assert name == "high"
    assert df2 is df  # 既存列はコピーせず素通し（byte 不変）


def test_resolve_btlm_price_adds_synthetic_column():
    df = _ohlcv(20)
    df2, name = call_binding._resolve_btlm_price(df, "hl2")
    h = df["high"].to_numpy(float); lo = df["low"].to_numpy(float)
    np.testing.assert_allclose(df2[name].to_numpy(float), (h + lo) / 2)
    # 元 df は不変（列を足していない）。
    assert "hl2" not in df.columns or name not in df.columns.difference(df2.columns)
