"""profit_band の振る舞いを検証するテスト。

元 MQL5 ロジック（分類規則・分位点方式・バンド符号）との整合を確認する。
"""

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src import (  # noqa: E402
    PROBABILITIES,
    EmptyBucketError,
    build_bands,
    collect_distance_samples,
    compute_quantiles,
)


def _sample_df() -> pd.DataFrame:
    # 3 本: 陽線 / 陰線 / 同値 を 1 本ずつ含む。
    return pd.DataFrame(
        {
            "open": [100.0, 100.0, 100.0],
            "high": [110.0, 105.0, 108.0],
            "low": [95.0, 90.0, 96.0],
            "close": [108.0, 92.0, 100.0],  # bull / bear / even
        }
    )


def test_classification_rules():
    df = _sample_df()
    s = collect_distance_samples(
        df["open"].to_numpy(),
        df["high"].to_numpy(),
        df["low"].to_numpy(),
        df["close"].to_numpy(),
    )
    # 陽線(bar0): pOH=|100-110|=10, pOL=|100-95|=5, pHL=|110-95|=15
    # 同値(bar2): pOH=|100-108|=8, pHL=|108-96|=12, nOL=|100-96|=4, nHL=12
    # 陰線(bar1): nOH=|100-105|=5, nOL=|100-90|=10, nHL=|105-90|=15
    assert sorted(s.pOH.tolist()) == [8.0, 10.0]      # bull + even
    assert s.pOL.tolist() == [5.0]                    # bull only
    assert sorted(s.pHL.tolist()) == [12.0, 15.0]     # bull + even
    assert s.nOH.tolist() == [5.0]                    # bear only
    assert sorted(s.nOL.tolist()) == [4.0, 10.0]      # bear + even
    assert sorted(s.nHL.tolist()) == [12.0, 15.0]     # bear + even


def test_quantile_matches_numpy_linear():
    df = _sample_df()
    s = collect_distance_samples(
        df["open"].to_numpy(),
        df["high"].to_numpy(),
        df["low"].to_numpy(),
        df["close"].to_numpy(),
    )
    q = compute_quantiles(s)
    expected = np.quantile([8.0, 10.0], PROBABILITIES, method="linear")
    np.testing.assert_allclose(q["pOH"], expected)


def test_build_bands_sign_and_shape():
    df = _sample_df()
    bands = build_bands(df)
    # 4 系統 × 7 パーセンタイル = 28 列、行数は入力と一致。
    assert bands.shape == (3, 28)
    # pOL/pOH は始値の上側(+)、nOH/nOL は下側(-)。
    assert (bands["pOL_99"] >= df["open"].to_numpy()).all()
    assert (bands["pOH_99"] >= df["open"].to_numpy()).all()
    assert (bands["nOH_99"] <= df["open"].to_numpy()).all()
    assert (bands["nOL_99"] <= df["open"].to_numpy()).all()


def test_band_value_exact():
    df = _sample_df()
    bands = build_bands(df)
    # nOH は陰線のみ=[5.0] なので全分位点 5.0 -> 始値100 - 5 = 95。
    np.testing.assert_allclose(bands["nOH_99"].to_numpy(), [95.0, 95.0, 95.0])
    # pOL は陽線のみ=[5.0] -> 100 + 5 = 105。
    np.testing.assert_allclose(bands["pOL_51"].to_numpy(), [105.0, 105.0, 105.0])


def test_no_decimal_truncation():
    # int() 切り捨て廃止の確認: 小数価格でも 0 にならない。
    df = pd.DataFrame(
        {
            "open": [1.10000, 1.10000],
            "high": [1.10050, 1.10030],
            "low": [1.09980, 1.09950],
            "close": [1.10040, 1.09960],  # bull / bear
        }
    )
    bands = build_bands(df)
    assert not np.isclose(bands["pOL_99"].iloc[0], df["open"].iloc[0])
    assert not np.isclose(bands["nOH_99"].iloc[1], df["open"].iloc[1])


def test_missing_column_raises():
    df = pd.DataFrame({"open": [1.0], "high": [2.0], "low": [0.5]})
    with pytest.raises(KeyError):
        build_bands(df)


def test_empty_required_bucket_raises():
    # 陰線が無い -> nOH/nOL が空 -> require_full で ValueError。
    df = pd.DataFrame(
        {"open": [100.0], "high": [110.0], "low": [95.0], "close": [108.0]}
    )
    with pytest.raises(ValueError):
        build_bands(df)
    # require_full=False なら NaN 列で返る。
    bands = build_bands(df, require_full=False)
    assert np.isnan(bands["nOH_99"]).all()


def test_empty_required_bucket_raises_empty_bucket_error_subclass_of_value_error():
    # LSP 是正 LSP-3: 必須バケット空は専用型 EmptyBucketError で送出される（型で識別可能）。
    # 後方互換: EmptyBucketError は ValueError サブクラスゆえ既存 except ValueError も捕捉する。
    df = pd.DataFrame(
        {"open": [100.0], "high": [110.0], "low": [95.0], "close": [108.0]}
    )
    assert issubclass(EmptyBucketError, ValueError)
    with pytest.raises(EmptyBucketError) as exc:
        build_bands(df)
    # メッセージは従来と同一（挙動保存）。
    assert "バンド生成に必要なバケットが空です" in str(exc.value)
