"""PRO!fit_Oscillator 成果物層（DataFrame アダプタ）の検証。

OHLCV DataFrame からクランプ済みレベルカウント列（``oscillator_lc``）と σ12 水準辞書を
返すこと、列名大小不問・volume を含む必須列欠落時に KeyError、core 層との数値一致、
元 index 継承を固定する。
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src import core  # noqa: E402
from src import oscillator  # noqa: E402


def _df(n: int = 40, *, columns=("Open", "High", "Low", "Close", "Volume"), index=None):
    rng = np.random.default_rng(3)
    base = np.cumsum(rng.normal(0, 1, n)) + 100.0
    o = base + rng.normal(0, 0.1, n)
    c = base + rng.normal(0, 0.1, n)
    h = np.maximum(o, c) + np.abs(rng.normal(0, 0.2, n))
    l = np.minimum(o, c) - np.abs(rng.normal(0, 0.2, n))
    v = np.abs(rng.normal(1000, 100, n))
    data = dict(zip(columns, [o, h, l, c, v]))
    return pd.DataFrame(data, index=index)


class TestBuildOscillator:
    def test_returns_level_count_column(self):
        df = _df()
        out = oscillator.build_oscillator(df)
        assert oscillator.LEVEL_COUNT_COLUMN == "oscillator_lc"
        assert oscillator.LEVEL_COUNT_COLUMN in out.columns

    def test_column_matches_core_level_count_clamped(self):
        df = _df()
        out = oscillator.build_oscillator(df)
        res = core.compute_oscillator_full(
            df["Open"].to_numpy(float),
            df["High"].to_numpy(float),
            df["Low"].to_numpy(float),
            df["Close"].to_numpy(float),
            df["Volume"].to_numpy(float),
        )
        np.testing.assert_allclose(
            out[oscillator.LEVEL_COUNT_COLUMN].to_numpy(), res.level_count_clamped
        )

    def test_case_insensitive_columns(self):
        df = _df(columns=("open", "high", "low", "close", "volume"))
        out = oscillator.build_oscillator(df)
        assert oscillator.LEVEL_COUNT_COLUMN in out.columns

    def test_index_inherited(self):
        idx = pd.date_range("2024-01-01", periods=40, freq="D")
        df = _df(index=idx)
        out = oscillator.build_oscillator(df)
        assert out.index.equals(df.index)

    def test_missing_volume_raises_keyerror(self):
        df = _df(columns=("Open", "High", "Low", "Close", "Vol"))  # volume 欠落
        with pytest.raises(KeyError):
            oscillator.build_oscillator(df)

    def test_missing_close_raises_keyerror(self):
        df = _df(columns=("Open", "High", "Low", "Cls", "Volume"))  # close 欠落
        with pytest.raises(KeyError):
            oscillator.build_oscillator(df)


class TestOscillatorLevels:
    def test_levels_match_core_sigma_levels(self):
        df = _df()
        levels = oscillator.oscillator_levels(df)
        res = core.compute_oscillator_full(
            df["Open"].to_numpy(float),
            df["High"].to_numpy(float),
            df["Low"].to_numpy(float),
            df["Close"].to_numpy(float),
            df["Volume"].to_numpy(float),
        )
        assert set(levels.keys()) == set(res.levels.keys())
        for k in res.levels:
            assert levels[k] == pytest.approx(res.levels[k])

    def test_levels_has_sigma12_keys(self):
        df = _df()
        levels = oscillator.oscillator_levels(df)
        for key in ("up_067", "up_329", "dn_067", "dn_329"):
            assert key in levels
        assert len(levels) == 12  # σ12（上方 6・下方 6）

    def test_missing_volume_raises_keyerror(self):
        df = _df(columns=("Open", "High", "Low", "Close", "Vol"))
        with pytest.raises(KeyError):
            oscillator.oscillator_levels(df)
