"""層名: 成果物層テスト（pandas）。

責務:
    PRO!fitHLBand 成果物層（build_hlband / hlband_levels / hlband_price_bands）を
    元 MQL ``PRO!fitHLBand.mq4`` と 1:1 再現で固定する。列名大小不問・元 index 継承・
    high/low 欠落で KeyError・母σ÷N・8 本符号・sub_max=b196*2 を固定する。

元 MQL 対応:
    L61 ExtVOLBuffer（range バッファ）       → build_hlband（RANGE_COLUMN 1 列）
    L97-100 StcLCStdDevArray / L100 平均      → hlband_levels（avg/b165/b196/b258）
    L102-103 INDICATOR_MINIMUM/MAXIMUM        → hlband_levels（sub_min=0.0 / sub_max=b196*2）
    L67-74 iHigh(0)-... / iLow(0)+...        → hlband_price_bands（overlay 8 本）

依存: 標準 sys/pathlib / 外部 numpy, pandas, pytest / プロジェクト内 src
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src import (  # noqa: E402
    RANGE_COLUMN,
    build_hlband,
    hlband_levels,
    hlband_price_bands,
)

# discriminating input（母σ÷N と標本σ ddof=1 で値が異なる／high[0]!=high[-1]）。
_HIGH = [10.0, 12.0, 14.0, 13.0]
_LOW = [8.0, 9.0, 11.0, 10.0]
_INDEX = [100, 101, 102, 103]

_AVG = 2.75
_SIGMA_POP = float(np.sqrt(0.1875))
_B165 = _AVG + 1.65 * _SIGMA_POP
_B196 = _AVG + 1.96 * _SIGMA_POP
_B258 = _AVG + 2.58 * _SIGMA_POP
_H_LAST = 13.0
_L_LAST = 10.0


def _df(high_col: str = "high", low_col: str = "low") -> pd.DataFrame:
    return pd.DataFrame({high_col: _HIGH, low_col: _LOW}, index=_INDEX)


# --- build_hlband ----------------------------------------------------------


def test_build_hlband_returns_range_column_with_high_minus_low() -> None:
    # Arrange
    df = _df()
    # Act
    out = build_hlband(df)
    # Assert
    np.testing.assert_array_equal(
        out[RANGE_COLUMN].to_numpy(), np.array([2.0, 3.0, 3.0, 3.0])
    )


def test_build_hlband_inherits_source_index() -> None:
    # Arrange
    df = _df()
    # Act
    out = build_hlband(df)
    # Assert: 元 index 継承
    assert list(out.index) == _INDEX


def test_build_hlband_accepts_uppercase_columns() -> None:
    # Arrange: 列名大小不問
    df = _df(high_col="HIGH", low_col="Low")
    # Act
    out = build_hlband(df)
    # Assert
    np.testing.assert_array_equal(
        out[RANGE_COLUMN].to_numpy(), np.array([2.0, 3.0, 3.0, 3.0])
    )


def test_build_hlband_raises_keyerror_when_high_missing() -> None:
    # Arrange: high 列欠落
    df = pd.DataFrame({"low": _LOW}, index=_INDEX)
    # Act / Assert
    with pytest.raises(KeyError):
        build_hlband(df)


# --- hlband_levels （separate レベル） -------------------------------------


def test_hlband_levels_avg_is_full_series_mean() -> None:
    # Arrange / Act
    levels = hlband_levels(_df())
    # Assert
    assert levels["avg"] == pytest.approx(_AVG)


def test_hlband_levels_three_bands_use_population_sigma() -> None:
    # Arrange: 母σ÷N。標本σ(ddof=1)=0.5 実装なら fail する discriminating input。
    levels = hlband_levels(_df())
    # Act / Assert
    assert levels["b165"] == pytest.approx(_B165)
    assert levels["b196"] == pytest.approx(_B196)
    assert levels["b258"] == pytest.approx(_B258)
    assert levels["b196"] != pytest.approx(_AVG + 1.96 * 0.5)  # 標本σ では fail


def test_hlband_levels_sub_min_is_zero_and_sub_max_is_b196_times_two() -> None:
    # Arrange / Act
    levels = hlband_levels(_df())
    # Assert: sub_min=0.0 / sub_max=b196*2（b258*2 等の誤りなら fail）
    assert levels["sub_min"] == 0.0
    assert levels["sub_max"] == pytest.approx(_B196 * 2)
    assert levels["sub_max"] != pytest.approx(_B258 * 2)


def test_hlband_levels_raises_keyerror_when_low_missing() -> None:
    # Arrange: low 列欠落
    df = pd.DataFrame({"high": _HIGH}, index=_INDEX)
    # Act / Assert
    with pytest.raises(KeyError):
        hlband_levels(df)


# --- hlband_price_bands （overlay 8 本・符号） -----------------------------


def test_hlband_price_bands_high_side_subtracts_from_latest_high() -> None:
    # Arrange: H_last=high[-1]=13.0。high[0]=10.0 なら fail する discriminating input。
    bands = hlband_price_bands(_df())
    # Act / Assert: High 側 = 減算
    assert bands["high_avg"] == pytest.approx(_H_LAST - _AVG)
    assert bands["high_b165"] == pytest.approx(_H_LAST - _B165)
    assert bands["high_b196"] == pytest.approx(_H_LAST - _B196)
    assert bands["high_b258"] == pytest.approx(_H_LAST - _B258)


def test_hlband_price_bands_low_side_adds_to_latest_low() -> None:
    # Arrange: L_last=low[-1]=10.0。low[0]=8.0 なら fail する discriminating input。
    bands = hlband_price_bands(_df())
    # Act / Assert: Low 側 = 加算
    assert bands["low_avg"] == pytest.approx(_L_LAST + _AVG)
    assert bands["low_b165"] == pytest.approx(_L_LAST + _B165)
    assert bands["low_b196"] == pytest.approx(_L_LAST + _B196)
    assert bands["low_b258"] == pytest.approx(_L_LAST + _B258)


def test_hlband_price_bands_uses_last_not_first_element() -> None:
    # Arrange: high[0]=10!=high[-1]=13。high[0] を使う実装なら fail する。
    bands = hlband_price_bands(_df())
    # Act / Assert
    assert bands["high_avg"] == pytest.approx(13.0 - 2.75)  # 10.25
    assert bands["high_avg"] != pytest.approx(10.0 - 2.75)  # 7.25（先頭ではない）
    assert bands["low_avg"] == pytest.approx(10.0 + 2.75)  # 12.75
    assert bands["low_avg"] != pytest.approx(8.0 + 2.75)  # 10.75（先頭ではない）


def test_hlband_price_bands_has_exactly_eight_keys() -> None:
    # Arrange / Act
    bands = hlband_price_bands(_df())
    # Assert: overlay 8 本
    assert set(bands.keys()) == {
        "high_avg",
        "high_b165",
        "high_b196",
        "high_b258",
        "low_avg",
        "low_b165",
        "low_b196",
        "low_b258",
    }


# --- 空入力ガード（成果物層・core 非変更） --------------------------------


def _empty_df() -> pd.DataFrame:
    # high/low 列は存在するが 0 行（core では high[-1] が IndexError / mean が nan）。
    return pd.DataFrame({"high": [], "low": []})


def test_build_hlband_raises_valueerror_on_empty_dataframe() -> None:
    # Arrange: 空 DataFrame
    df = _empty_df()
    # Act / Assert: 明示的 ValueError（IndexError/nan ではなく）
    with pytest.raises(ValueError):
        build_hlband(df)


def test_hlband_levels_raises_valueerror_on_empty_dataframe() -> None:
    # Arrange / Act / Assert
    with pytest.raises(ValueError):
        hlband_levels(_empty_df())


def test_hlband_price_bands_raises_valueerror_on_empty_dataframe() -> None:
    # Arrange / Act / Assert
    with pytest.raises(ValueError):
        hlband_price_bands(_empty_df())
