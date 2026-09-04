"""usecase.dataset_port（ISSUE-092 ①）— Output Boundary の set/get・遅延既定を検証する。

参照実装 market_profile_api.compute.tick_store_port と同じ規律:
    - Protocol 所有は内側（usecase）
    - set_dataset_port / dataset_port() の set/get
    - 未注入時は既定 gateway を遅延合成（自己完結起動の温存）

具象 gateway（adapter.gateway.marketdata_dataset.MarketdataDatasetGateway）は
marketdata.dataset へ等価委譲する（別モジュールへ差し替えても is_known/load 系が同一）。
"""

from __future__ import annotations

import pytest

from usecase.dataset_port import (
    CandleDatasetPort,
    DatasetPort,
    candle_dataset_port,
    dataset_port,
    set_dataset_port,
)


class _FakePort:
    def is_known(self, ref):  # noqa: ANN001
        return ref == "ok"

    def is_known_timeframe(self, tf):  # noqa: ANN001
        return tf == "1D"

    def load_dataframe(self, ref, tf):  # noqa: ANN001
        return ("df", ref, tf)


@pytest.fixture(autouse=True)
def _restore_default():
    """各テスト後に既定へ戻す（グローバル注入の漏れを防ぐ）。"""
    yield
    set_dataset_port(None)


def test_set_and_get_returns_injected_port():
    fake = _FakePort()
    set_dataset_port(fake)
    assert dataset_port() is fake


def test_default_is_lazily_synthesised_when_not_injected():
    set_dataset_port(None)
    port = dataset_port()
    assert isinstance(port, DatasetPort)  # runtime_checkable Protocol


def test_set_none_restores_default_synthesis():
    set_dataset_port(_FakePort())
    set_dataset_port(None)
    port = dataset_port()
    assert not isinstance(port, _FakePort)


def test_default_gateway_delegates_to_marketdata_dataset():
    # 既定 gateway は marketdata.dataset の is_known / is_known_timeframe / load_dataframe に等価委譲。
    from marketdata import dataset as md_dataset

    set_dataset_port(None)
    port = dataset_port()
    assert port.is_known("sample") == md_dataset.is_known("sample")
    assert port.is_known("____nope____") == md_dataset.is_known("____nope____")
    assert port.is_known_timeframe("1D") == md_dataset.is_known_timeframe("1D")
    assert port.is_known_timeframe("9z") == md_dataset.is_known_timeframe("9z")


def test_candle_face_rejects_port_that_lacks_load_candles():
    """配信面を満たさない実装の注入は、その場で RuntimeError（ISSUE-222）。

    `_FakePort` は `DatasetPort`（is_known / is_known_timeframe / load_dataframe）を満たす
    **合法な実装**だが `load_candles` を持たない。注入シームが 1 本しか無いため注入自体は通る。
    無検査キャストのままだと欠落は `/candles` の実行時 AttributeError まで潜伏し、
    `framework.server` の総括 catch で HTTP 500 `internal` へ沈黙劣化していた。
    結線漏れは serving 中ではなく取得時点で落ちること（未注入時と対称）を固定する。
    """
    fake = _FakePort()
    assert isinstance(fake, DatasetPort)          # 前提: DatasetPort としては合法
    assert not isinstance(fake, CandleDatasetPort)  # 配信面は満たさない
    set_dataset_port(fake)

    assert dataset_port() is fake                 # /compute 側は従来どおり通る
    with pytest.raises(RuntimeError, match="CandleSeriesPort"):
        candle_dataset_port()


def test_candle_face_returns_default_gateway_which_implements_both():
    """既定 gateway は両面を実装する＝正常系は素通しする（上のガードが過剰でないこと）。"""
    set_dataset_port(None)
    port = candle_dataset_port()
    assert isinstance(port, CandleDatasetPort)
    assert port is dataset_port()


def test_default_gateway_load_dataframe_sees_monkeypatched_module(monkeypatch):
    # gateway はモジュールオブジェクトへ実行時委譲するため、marketdata.dataset の
    # load_dataframe を差し替えると gateway 経由でも見える（monkeypatch 経路の温存）。
    from marketdata import dataset as md_dataset

    set_dataset_port(None)
    port = dataset_port()
    monkeypatch.setattr(md_dataset, "load_dataframe", lambda ref, tf: ("patched", ref, tf))
    assert port.load_dataframe("sample", "1D") == ("patched", "sample", "1D")
