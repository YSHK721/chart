"""ma_marod core の参照実装バインディングのテスト（ISSUE-176）。

検証対象:
    1. 並列ロック: 動的ロードが共有ローダ ``common.module_loader``（ロック付き）を経由すること。
       core 層が自前の importlib 機構（ロック無し）を保持しないこと。
    2. 注入形: ``MovingAverageReference`` Protocol を受け取る注入点が存在し、未注入時は従来の
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

_MA_TYPES = ("sma", "ema", "smma", "lwma")
_BUFFER_FNS = (
    "simple_ma_on_buffer",
    "exponential_ma_on_buffer",
    "smoothed_ma_on_buffer",
    "linear_weighted_ma_on_buffer",
)


def _ohlc(n: int = 200, seed: int = 3) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    prices = np.cumsum(rng.normal(0, 1, n)) + 100.0
    return pd.DataFrame({
        "open": prices, "high": prices + 1.0, "low": prices - 1.0, "close": prices,
    })


@pytest.fixture(autouse=True)
def _reset_injection():
    yield
    core.set_moving_average_reference(None)


# --- 1. 並列ロック（ISSUE-176 影響 2） ---------------------------------------

def test_core_does_not_own_importlib_machinery():
    assert not hasattr(core, "importlib")


def test_load_moving_averages_delegates_to_shared_locked_loader(monkeypatch):
    calls = []
    real = module_loader.load_module

    def spy(name, file_path):
        calls.append((name, Path(file_path)))
        return real(name, file_path)

    monkeypatch.setattr(module_loader, "load_module", spy)
    module = core._load_moving_averages()
    assert calls == [(core._MOVING_AVERAGES_MODNAME, core._MOVING_AVERAGES_CORE)]
    for fn in _BUFFER_FNS:
        assert callable(getattr(module, fn))


# --- 2. 注入形（ISSUE-176 影響 1: DIP / SRP） --------------------------------

def test_moving_average_reference_protocol_is_runtime_checkable():
    assert isinstance(core._load_moving_averages(), core.MovingAverageReference)


def test_default_reference_falls_back_to_dynamic_load():
    assert core.moving_average_reference() is core._load_moving_averages()


@pytest.mark.parametrize("ma_type", _MA_TYPES)
def test_injected_reference_is_used_by_ma_marod_series(ma_type):
    calls = []
    real = core._load_moving_averages()

    class _Ref:
        def __init__(self):
            for fn in _BUFFER_FNS:
                setattr(self, fn, self._wrap(fn))

        def _wrap(self, fn):
            target = getattr(real, fn)

            def inner(*args, **kwargs):
                calls.append(fn)
                return target(*args, **kwargs)
            return inner

    df = _ohlc()
    expected = core.ma_marod_series(df, ma_type=ma_type, length=50)
    core.set_moving_average_reference(_Ref())
    got = core.ma_marod_series(df, ma_type=ma_type, length=50)
    assert calls, "注入した参照実装が使われていない"
    np.testing.assert_array_equal(got, expected)  # bit-for-bit


def test_set_moving_average_reference_rejects_non_conforming_object():
    with pytest.raises(TypeError):
        core.set_moving_average_reference(object())


def test_set_moving_average_reference_none_restores_dynamic_load():
    core.set_moving_average_reference(None)
    assert core.moving_average_reference() is core._load_moving_averages()
