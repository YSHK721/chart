"""adapter/indicator/registry.py の PandasIndicatorRegistry テスト（IndicatorPort）。

責務（CLEAN_ARCH §6）:
    - IndicatorPort を実装（get / update）。
    - get(name): 登録済み指標系列を返す。未登録参照は IndicatorBufferError。
    - get で参照位置に NaN がある場合は IndicatorNaNError（NaN 検出翻訳）。
    - update(bar_index): 事前計算系列では no-op（IF 充足のため呼べること）。
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from simulator.domain.exceptions import IndicatorBufferError, IndicatorNaNError
from simulator.usecase.ports import IndicatorPort


def test_registry_implements_indicator_port():
    # Arrange / Act
    from simulator.adapter.indicator.registry import PandasIndicatorRegistry

    reg = PandasIndicatorRegistry({"madiff": pd.Series([1.0, 2.0])})

    # Assert: LSP — Port のサブクラスで抽象解決済み（インスタンス化可）
    assert isinstance(reg, IndicatorPort)


def test_get_returns_registered_series():
    # Arrange
    from simulator.adapter.indicator.registry import PandasIndicatorRegistry

    series = pd.Series([0.5, -0.5, 1.5])
    reg = PandasIndicatorRegistry({"madiff": series})

    # Act
    got = reg.get("madiff")

    # Assert
    assert list(got) == [0.5, -0.5, 1.5]


def test_get_unregistered_raises_indicator_buffer_error():
    # Arrange
    from simulator.adapter.indicator.registry import PandasIndicatorRegistry

    reg = PandasIndicatorRegistry({"madiff": pd.Series([1.0])})

    # Act / Assert: 未登録参照は内側ドメイン例外へ翻訳（CLEAN_ARCH §6）
    with pytest.raises(IndicatorBufferError):
        reg.get("does_not_exist")


def test_get_series_with_internal_nan_raises_indicator_nan_error():
    # Arrange: 有効区間（先頭 warmup より後）の NaN はデータ破損 → IndicatorNaNError。
    # 先頭が数値で中間に NaN がある＝warmup では説明できない異常 NaN。
    from simulator.adapter.indicator.registry import PandasIndicatorRegistry

    reg = PandasIndicatorRegistry({"madiff": pd.Series([1.0, np.nan, 2.0])})

    # Act / Assert
    with pytest.raises(IndicatorNaNError):
        reg.get("madiff")


def test_get_series_with_leading_warmup_nan_does_not_raise():
    # Arrange: 先頭の連続 NaN は warmup（指標の正しい未定義区間・SPEC §1.2）。
    # warmup 由来の NaN で IndicatorNaNError を投げてはならない（誤検出禁止）。
    from simulator.adapter.indicator.registry import PandasIndicatorRegistry

    reg = PandasIndicatorRegistry({"madiff": pd.Series([np.nan, np.nan, 1.0, 2.0])})

    # Act
    got = reg.get("madiff")

    # Assert: 例外を投げず系列を返す。先頭 warmup NaN は許容。
    assert np.isnan(got.iloc[0]) and np.isnan(got.iloc[1])
    assert list(got.iloc[2:]) == [1.0, 2.0]


def test_get_all_nan_series_is_treated_as_all_warmup_and_does_not_raise():
    # Arrange: 全数 NaN は post-warmup 区間を持たない（全 warmup）。上流規約は
    # 「warmup より後の NaN のみ IndicatorNaNError」なので本検査では投げない。
    # （最小バー不足の検証は別責務・本タスク範囲外）
    from simulator.adapter.indicator.registry import PandasIndicatorRegistry

    reg = PandasIndicatorRegistry({"madiff": pd.Series([np.nan, np.nan])})

    # Act
    got = reg.get("madiff")

    # Assert: 例外を投げず系列を返す
    assert bool(got.isna().all())


def test_get_series_with_trailing_nan_raises_indicator_nan_error():
    # Arrange: 末尾の NaN は warmup では説明できない（warmup は先頭のみ）→ 破損検出。
    from simulator.adapter.indicator.registry import PandasIndicatorRegistry

    reg = PandasIndicatorRegistry({"madiff": pd.Series([1.0, 2.0, np.nan])})

    # Act / Assert
    with pytest.raises(IndicatorNaNError):
        reg.get("madiff")


def test_update_is_noop_for_precomputed_series():
    # Arrange: 事前計算系列では update は系列を変えない（IF 充足のため呼べる）
    from simulator.adapter.indicator.registry import PandasIndicatorRegistry

    reg = PandasIndicatorRegistry({"madiff": pd.Series([1.0, 2.0, 3.0])})

    # Act
    reg.update(1)

    # Assert
    assert list(reg.get("madiff")) == [1.0, 2.0, 3.0]
