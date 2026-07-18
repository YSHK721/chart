"""CausalCandleRepository の結線テスト（ISSUE-131: dataset 完全委譲＝ライブと同一配信路）。

fake bridge（SimpleNamespace）を注入し、dataset.load_dataframe への委譲・candle 整形・
tail(limit)・tickvol additive（volume 列写し）・未知 ref 拒否を検証する。
自前足生成（リサンプル・独自外れ値補正 _m1_repair）は全廃済み＝本テストは配信路の委譲だけを見る。
"""
from __future__ import annotations

from types import SimpleNamespace

import pandas as pd
import pytest

from simulator.replay_ui.adapter.causal_candle_repository import CausalCandleRepository


def _fake_bridge(df: pd.DataFrame, known=("jp225_tick",)):
    calls: dict = {}

    def load_dataframe(ref, timeframe):
        calls["ref"] = ref
        calls["timeframe"] = timeframe
        return df

    ns = SimpleNamespace(
        dataset=SimpleNamespace(
            is_known=lambda ref: ref in known,
            load_dataframe=load_dataframe,
        )
    )
    return ns, calls


def _df(rows, columns=("open", "high", "low", "close", "volume")):
    idx = pd.to_datetime([r[0] for r in rows])
    return pd.DataFrame([list(r[1:]) for r in rows], index=idx, columns=list(columns))


def test_delegates_to_dataset_and_formats_candles_with_tickvol():
    df = _df([
        ("2020-01-03 00:00:00", 100.0, 105.0, 99.0, 101.0, 3.0),
        ("2020-01-06 00:00:00", 200.0, 210.0, 199.0, 208.0, 2.0),
    ])
    bridge, calls = _fake_bridge(df)
    repo = CausalCandleRepository(bridge_loader=lambda *a: bridge)
    candles = repo.load_candles("jp225_tick", "1D", None)
    assert calls == {"ref": "jp225_tick", "timeframe": "1D"}  # 配信路＝dataset へ完全委譲
    assert [c["time"] for c in candles] == [1578009600, 1578268800]
    assert candles[0] == {
        "time": 1578009600, "open": 100.0, "high": 105.0, "low": 99.0,
        "close": 101.0, "tickvol": 3,
    }
    assert candles[1]["tickvol"] == 2


def test_limit_tail():
    df = _df([
        ("2020-01-01 00:00:00", 1.0, 2.0, 0.5, 1.5, 1.0),
        ("2020-01-02 00:00:00", 110.0, 112.0, 109.0, 111.0, 1.0),
    ])
    bridge, _ = _fake_bridge(df)
    repo = CausalCandleRepository(bridge_loader=lambda *a: bridge)
    candles = repo.load_candles("jp225_tick", "1D", 1)
    assert len(candles) == 1
    assert candles[0]["open"] == 110.0  # 直近 1 本（dataset.load_candles と同一の末尾規則）


def test_unknown_ref_raises_valueerror():
    bridge, _ = _fake_bridge(_df([("2020-01-01", 1.0, 1.0, 1.0, 1.0, 1.0)]))
    repo = CausalCandleRepository(bridge_loader=lambda *a: bridge)
    with pytest.raises(ValueError):
        repo.load_candles("totally_unknown_ref", "1D", None)


def test_tickvol_skips_non_finite_volume_rows():
    """volume が NaN の行は tickvol を載せない（int(NaN)→ValueError で /candles 500 を防ぐ）。"""
    df = _df([
        ("2020-01-01 00:00:00", 100.0, 105.0, 99.0, 101.0, 2.0),
        ("2020-01-01 00:01:00", 101.0, 106.0, 100.0, 102.0, float("nan")),
    ])
    bridge, _ = _fake_bridge(df)
    repo = CausalCandleRepository(bridge_loader=lambda *a: bridge)
    m1 = repo.load_candles("jp225_tick", "1m", None)
    assert m1[0]["tickvol"] == 2
    assert "tickvol" not in m1[1]  # 欠損足は載せない（JS 側が従来モデルへフォールバック）


def test_no_volume_column_yields_no_tickvol():
    """非 tick 源（volume 列なし）は tickvol を付与しない（additive・従来 JSON 不変）。"""
    df = _df([("2020-01-01 00:00:00", 1.0, 2.0, 0.5, 1.5)],
             columns=("open", "high", "low", "close"))
    bridge, _ = _fake_bridge(df, known=("sample",))
    repo = CausalCandleRepository(bridge_loader=lambda *a: bridge)
    candles = repo.load_candles("sample", "1D", None)
    assert "tickvol" not in candles[0]

