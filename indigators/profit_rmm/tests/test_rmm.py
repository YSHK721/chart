"""PRO!fitRMM 成果物層（pandas）の検証。

core 層の ``compute_rmm`` を pandas DataFrame 入出力でラップする成果物層を固定する。
列名大小不問・元 index 継承・必須列（volume 含む）欠落時 KeyError を固定する。
"""

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # = profit_rmm/

from src import core, rmm  # noqa: E402


def _make_df(index_offset: int = 0) -> pd.DataFrame:
    rng = np.random.default_rng(7)
    n = 30
    high = np.cumsum(rng.random(n)) + 10.0
    low = high - rng.random(n) - 0.5
    close = low + rng.random(n) * (high - low)
    volume = rng.integers(1, 100, n).astype(float)
    idx = pd.RangeIndex(start=index_offset, stop=index_offset + n)
    return pd.DataFrame(
        {"high": high, "low": low, "close": close, "volume": volume}, index=idx
    )


class TestBuildRmm:
    def test_level_count_column_name_constant(self) -> None:
        # Arrange / Act / Assert: 列名定数の固定。
        assert rmm.LEVEL_COUNT_COLUMN == "rmm_lc"

    def test_build_rmm_returns_level_count_matching_core(self) -> None:
        # Arrange
        df = _make_df()
        # Act
        out = rmm.build_rmm(df, osc_period=6, ma_period=6)
        result = core.compute_rmm(
            df["high"].to_numpy(),
            df["low"].to_numpy(),
            df["close"].to_numpy(),
            df["volume"].to_numpy(),
            osc_period=6,
            ma_period=6,
        )
        # Assert: rmm_lc 列が core.level_count と一致。
        np.testing.assert_allclose(
            out[rmm.LEVEL_COUNT_COLUMN].to_numpy(), result.level_count
        )

    def test_build_rmm_preserves_original_index(self) -> None:
        # Arrange: 元 index が非ゼロ始まり。
        df = _make_df(index_offset=1000)
        # Act
        out = rmm.build_rmm(df)
        # Assert: 元 index を継承する。
        pd.testing.assert_index_equal(out.index, df.index)

    def test_build_rmm_column_name_case_insensitive(self) -> None:
        # Arrange: 大文字列名でも抽出できる（列名大小不問）。
        df = _make_df().rename(
            columns={"high": "High", "low": "LOW", "close": "Close", "volume": "Volume"}
        )
        # Act
        out = rmm.build_rmm(df)
        # Assert: 例外なく rmm_lc 列を生成。
        assert rmm.LEVEL_COUNT_COLUMN in out.columns

    def test_build_rmm_missing_volume_raises_key_error(self) -> None:
        # Arrange: volume 欠落（必須列）。
        df = _make_df().drop(columns=["volume"])
        # Act / Assert
        with pytest.raises(KeyError):
            rmm.build_rmm(df)

    def test_build_rmm_missing_high_raises_key_error(self) -> None:
        # Arrange: high 欠落。
        df = _make_df().drop(columns=["high"])
        with pytest.raises(KeyError):
            rmm.build_rmm(df)


class TestRmmLevels:
    def test_rmm_levels_returns_six_levels_matching_core(self) -> None:
        # Arrange
        df = _make_df()
        # Act
        levels = rmm.rmm_levels(df, osc_period=6, ma_period=6)
        result = core.compute_rmm(
            df["high"].to_numpy(),
            df["low"].to_numpy(),
            df["close"].to_numpy(),
            df["volume"].to_numpy(),
            osc_period=6,
            ma_period=6,
        )
        # Assert: 6 水準・core.lc_levels と一致。
        assert set(levels.keys()) == {
            "up_1s", "up_2s", "up_3s", "dn_1s", "dn_2s", "dn_3s",
        }
        for k in levels:
            assert levels[k] == pytest.approx(result.lc_levels[k])

    def test_rmm_levels_missing_volume_raises_key_error(self) -> None:
        df = _make_df().drop(columns=["volume"])
        with pytest.raises(KeyError):
            rmm.rmm_levels(df)
