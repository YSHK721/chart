"""btlm_trail_marod core の参照実装バインディングのテスト（ISSUE-176）。

検証対象:
    1. 並列ロック: 動的ロードが共有ローダ ``common.module_loader``（ロック付き）を経由すること。
       core 層が自前の importlib 機構（ロック無し）を保持しないこと。
    2. 注入形: ``TrendLineReference`` Protocol を受け取る注入点が存在し、未注入時は従来の
       動的ロードへフォールバックすること（既存呼出元の挙動が完全不変であること）。
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

_PKG_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_PKG_DIR))
sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from src import core  # noqa: E402
from common import module_loader  # noqa: E402


def _ohlc(n: int = 200, seed: int = 3) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    prices = np.cumsum(rng.normal(0, 1, n)) + 100.0
    return pd.DataFrame({
        "open": prices, "high": prices + 1.0, "low": prices - 1.0, "close": prices,
    })


@pytest.fixture(autouse=True)
def _reset_injection():
    yield
    core.set_trend_line_reference(None)


# --- 1. 並列ロック（ISSUE-176 影響 2） ---------------------------------------

def test_core_does_not_own_importlib_machinery():
    # core（最内層）が動的ロード機構そのものを保持しない（ロック無し複製の排除）。
    assert not hasattr(core, "importlib")


def test_load_btlm_trail_delegates_to_shared_locked_loader(monkeypatch):
    calls = []
    real = module_loader.load_package

    def spy(name, pkg_dir):
        calls.append((name, Path(pkg_dir)))
        return real(name, pkg_dir)

    monkeypatch.setattr(module_loader, "load_package", spy)
    module = core._load_btlm_trail()
    assert calls == [(core._BTLM_TRAIL_MODNAME, core._BTLM_TRAIL_SRC)]
    assert callable(module.resolve_source)
    assert callable(module.rolling_ols_window_end)


# --- 2. 注入形（ISSUE-176 影響 1: DIP / SRP） --------------------------------

def test_trend_line_reference_protocol_is_runtime_checkable():
    assert isinstance(core._load_btlm_trail(), core.TrendLineReference)


def test_default_reference_falls_back_to_dynamic_load():
    # 未注入時は従来どおり btlm_trail src を動的ロードした結果を返す（挙動不変）。
    assert core.trend_line_reference() is core._load_btlm_trail()


def test_injected_reference_is_used_by_marod_series():
    calls = []
    real = core._load_btlm_trail()

    class _Ref:
        def resolve_source(self, df, source):
            calls.append(("resolve_source", source))
            return real.resolve_source(df, source)

        def rolling_ols_window_end(self, prices, maxbars):
            calls.append(("rolling_ols_window_end", maxbars))
            return real.rolling_ols_window_end(prices, maxbars)

    df = _ohlc()
    expected = core.marod_series(df, source="close", maxbars=100)
    core.set_trend_line_reference(_Ref())
    got = core.marod_series(df, source="close", maxbars=100)
    assert calls == [("resolve_source", "close"), ("rolling_ols_window_end", 100)]
    np.testing.assert_array_equal(got, expected)  # bit-for-bit


def test_set_trend_line_reference_rejects_non_conforming_object():
    with pytest.raises(TypeError):
        core.set_trend_line_reference(object())


def test_set_trend_line_reference_none_restores_dynamic_load():
    core.set_trend_line_reference(None)
    assert core.trend_line_reference() is core._load_btlm_trail()
