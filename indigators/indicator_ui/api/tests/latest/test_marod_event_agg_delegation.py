"""MAROD 増分器の集計単位（event_agg）検証が共有プリミティブへ委譲されることを固定する。

ISSUE-479 Wave2 追随 B。増分器 ``prepare`` は既知集計単位の集合をリテラルで書き写していた
（``event_agg not in ("episode", "bar")``）。集計単位が 1 つ増えた日にここだけ取り残され、
新しい集計単位が「未知値」として黙って full 経路へ落ちる（値は正しいまま性能だけ落ちるので
状態検証では落ちない）。検証の実装は ``common.event_quantiles.normalize_event_agg`` 1 本にする。

固定するもの:
  1. 既知集計単位は大文字小文字を問わず受理し、正規化済み（小文字）で _Request に載ること
  2. 未知集計単位は従来どおり None（＝従来の full 経路へ落とす）であること — 挙動不変
  3. 正規化の発行が prepare 1 回につき 1 件で、バー数を増やしても増えないこと（計算量）
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from common import event_quantiles as evq

from adapter.compute.incremental import marod as marod_mod


def _ohlcv(n: int = 400, seed: int = 3) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    close = 39000.0 + np.cumsum(rng.normal(0.0, 25.0, n))
    open_ = np.concatenate([[close[0]], close[:-1]])
    high = np.maximum(open_, close) + np.abs(rng.normal(0.0, 8.0, n))
    low = np.minimum(open_, close) - np.abs(rng.normal(0.0, 8.0, n))
    return pd.DataFrame(
        {"open": open_, "high": high, "low": low, "close": close,
         "volume": np.full(n, 1000.0)},
        index=pd.date_range("2024-01-01", periods=n, freq="h", name="time"),
    )


def _params(**overrides) -> dict:
    base = {
        "source": "close", "ma_type": "ema", "length": 50,
        "q_low": 0.05, "q_high": 0.95, "q_out": 0.99,
        "k_events": 50, "event_agg": "episode", "window_n": 200,
    }
    base.update(overrides)
    return base


def _incrementer():
    return marod_mod.MarodIncrementer(marod_mod._MovingAverageBaseline(), "ma_marod")


@pytest.mark.parametrize(
    "given,expected", [("episode", "episode"), ("bar", "bar"),
                       ("EPISODE", "episode"), ("Bar", "bar")]
)
def test_known_aggs_are_accepted_and_normalised(given: str, expected: str) -> None:
    # Arrange
    incrementer = _incrementer()

    # Act
    req = incrementer.prepare(_ohlcv(), _params(event_agg=given))

    # Assert
    assert req is not None
    assert req.event_agg == expected


def test_unknown_agg_falls_back_to_the_previous_full_path() -> None:
    """未知値は None（従来経路へ落ちる）。増分器は例外を外へ出さない — 従来挙動の保存。"""
    # Arrange
    incrementer = _incrementer()

    # Act
    req = incrementer.prepare(_ohlcv(), _params(event_agg="epsiode"))

    # Assert
    assert req is None


def test_the_validation_is_delegated_to_the_shared_primitive(monkeypatch) -> None:
    """検証の実装は共有プリミティブ 1 本（差し替えると増分器の判定も変わる）。"""
    # Arrange: 共有側だけを差し替える。書き写しが残っていれば判定は変わらない＝赤。
    monkeypatch.setattr(
        marod_mod, "normalize_event_agg",
        lambda agg: (_ for _ in ()).throw(ValueError("stub: 全部未知")),
    )
    incrementer = _incrementer()

    # Act
    req = incrementer.prepare(_ohlcv(), _params(event_agg="episode"))

    # Assert
    assert req is None


class _NormalizeSpy:
    def __init__(self, original):
        self._original = original
        self.issued: list[str] = []

    def __call__(self, event_agg):
        self.issued.append(str(event_agg))
        return self._original(event_agg)


@pytest.mark.parametrize("bars", [300, 1200])
def test_normalisation_is_issued_once_per_prepare_regardless_of_bar_count(
    monkeypatch, bars: int
) -> None:
    """計算量テスト: 発行した正規化 − 出力(_Request)に使った正規化 = 0。

    1 回の prepare が使う集計単位は 1 つだけ。正規化をバー走査の内側へ落とすと発行が
    バー数に比例して赤になる。回数を焼き込まず**無駄の不在**を固定し、バー数 300/1200 の
    2 点で発行が変わらないこと（発行が入力量に比例しない＝オーダーの表明）も固定する。
    """
    # Arrange
    spy = _NormalizeSpy(evq.normalize_event_agg)
    monkeypatch.setattr(marod_mod, "normalize_event_agg", spy)
    incrementer = _incrementer()

    # Act
    req = incrementer.prepare(_ohlcv(bars), _params())

    # Assert
    used = 1 if req is not None else 0
    assert used == 1
    assert len(spy.issued) - used == 0, f"正規化を取り直している: {spy.issued}"
