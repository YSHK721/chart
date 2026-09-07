"""PRO!fitRMMMACD 成果物層（pandas）の検証。

DataFrame から high/low/close/volume を小文字正規化抽出し、core を呼んで
histogram/macd/signal の 3 列のみを付与した DataFrame（元 index 継承）を返す薄い
変換層を固定する。**σ 水準が無いため levels 関数は持たない**。

固定する discriminating 観点::

    - build_rmmmacd が histogram/macd/signal の 3 列のみを付与し core と一致
    - 元 index を継承する
    - 列名大小不問（High/LOW/Close/VOLUME でも抽出可）
    - volume を含む必須列欠落 → KeyError
    - σ 水準が無い（rmmmacd_levels 関数を持たない）を構造で担保
"""

import numpy as np
import pandas as pd
import pytest

# import 解決は台帳（tools/dev_paths.txt）由来の pythonpath が担う。テスト側で sys.path を
# 改変しない（改変するとプロダクトとモジュール同一性が食い違う）。``indigators/`` は
# pyproject.toml ``[tool.pytest.ini_options] pythonpath`` と venv の
# ``jp225_chart_paths.pth`` の双方に登録済みで、本パッケージは名前空間パッケージ
# （PEP 420）として ``profit_rmm_macd`` の名前で解決する。
from profit_rmm_macd.src import core, rmmmacd


def _sample_df(index=None) -> pd.DataFrame:
    high = np.array(
        [10, 12, 11, 13, 9, 14, 15, 13, 16, 12,
         17, 18, 16, 19, 15, 20, 21, 19, 22, 18],
        dtype=np.float64,
    )
    low = np.array(
        [8, 9, 7, 10, 6, 11, 12, 10, 13, 9,
         14, 15, 13, 16, 12, 17, 18, 16, 19, 15],
        dtype=np.float64,
    )
    close = np.array(
        [9, 11, 10, 12, 8, 13, 14, 11, 15, 10,
         16, 17, 14, 18, 13, 19, 20, 17, 21, 16],
        dtype=np.float64,
    )
    volume = np.array(
        [100, 120, 110, 130, 90, 140, 150, 130, 160, 120,
         170, 180, 160, 190, 150, 200, 210, 190, 220, 180],
        dtype=np.float64,
    )
    return pd.DataFrame(
        {"high": high, "low": low, "close": close, "volume": volume},
        index=index,
    )


# ===========================================================================
# 列名定数
# ===========================================================================
class TestColumnNames:
    def test_column_name_constants(self) -> None:
        assert rmmmacd.HIST_COLUMN == "rmmmacd_hist"
        assert rmmmacd.MACD_COLUMN == "rmmmacd_macd"
        assert rmmmacd.SIGNAL_COLUMN == "rmmmacd_signal"


# ===========================================================================
# build_rmmmacd: 3 列のみ・core と一致・index 継承
# ===========================================================================
class TestBuildRmmMacd:
    def test_builds_three_columns_matching_core(self) -> None:
        # Arrange
        df = _sample_df()
        expected = core.compute_rmmmacd(
            df["high"].to_numpy(),
            df["low"].to_numpy(),
            df["close"].to_numpy(),
            df["volume"].to_numpy(),
        )
        # Act
        out = rmmmacd.build_rmmmacd(df)
        # Assert: 3 列が付与され core と一致。
        np.testing.assert_allclose(
            out[rmmmacd.HIST_COLUMN].to_numpy(), expected.histogram
        )
        np.testing.assert_allclose(
            out[rmmmacd.MACD_COLUMN].to_numpy(), expected.macd
        )
        np.testing.assert_allclose(
            out[rmmmacd.SIGNAL_COLUMN].to_numpy(), expected.signal
        )

    def test_inherits_original_index(self) -> None:
        # Arrange: 非自明な index。
        idx = pd.RangeIndex(start=100, stop=120)
        df = _sample_df(index=idx)
        # Act
        out = rmmmacd.build_rmmmacd(df)
        # Assert
        pd.testing.assert_index_equal(out.index, idx)

    def test_accepts_uppercase_columns(self) -> None:
        # Arrange: 大文字混在列名。
        df = _sample_df().rename(
            columns={
                "high": "High",
                "low": "LOW",
                "close": "Close",
                "volume": "VOLUME",
            }
        )
        # Act
        out = rmmmacd.build_rmmmacd(df)
        # Assert: 抽出に成功し 3 列付与。
        assert rmmmacd.HIST_COLUMN in out.columns
        assert rmmmacd.MACD_COLUMN in out.columns
        assert rmmmacd.SIGNAL_COLUMN in out.columns


# ===========================================================================
# 例外: volume 含む必須列欠落 → KeyError
# ===========================================================================
class TestMissingColumns:
    def test_missing_volume_raises_key_error(self) -> None:
        df = _sample_df().drop(columns=["volume"])
        with pytest.raises(KeyError):
            rmmmacd.build_rmmmacd(df)

    def test_missing_close_raises_key_error(self) -> None:
        df = _sample_df().drop(columns=["close"])
        with pytest.raises(KeyError):
            rmmmacd.build_rmmmacd(df)


# ===========================================================================
# σ 水準が無い（構造で担保）
# ===========================================================================
class TestNoLevelsFunction:
    def test_artifact_module_has_no_levels_function(self) -> None:
        assert not hasattr(rmmmacd, "rmmmacd_levels")
