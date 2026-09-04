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


# ---- WindowedCandlePort / AvailableDaysPort（リプレイバーのカレンダー） ---- #


def test_load_candles_from_starts_at_the_selected_day_with_pre_bars():
    """``start`` 以降の最初の足の ``pre`` 本手前から ``limit`` 本を返す。"""
    df = _df([
        ("2020-01-01 00:00:00", 1.0, 1.0, 1.0, 1.0, 1.0),
        ("2020-01-02 00:00:00", 2.0, 2.0, 2.0, 2.0, 1.0),
        ("2020-01-03 00:00:00", 3.0, 3.0, 3.0, 3.0, 1.0),
        ("2020-01-06 00:00:00", 4.0, 4.0, 4.0, 4.0, 1.0),
        ("2020-01-07 00:00:00", 5.0, 5.0, 5.0, 5.0, 1.0),
    ])
    bridge, _ = _fake_bridge(df)
    repo = CausalCandleRepository(bridge_loader=lambda *a: bridge)
    start = 1578268800  # 2020-01-06 00:00:00 UTC
    candles = repo.load_candles_from("jp225_tick", "1D", start, 1, 3)
    assert [c["open"] for c in candles] == [3.0, 4.0, 5.0]  # 1 本前置き（01-03）から 3 本


def test_load_candles_from_clamps_pre_at_the_head_and_end_of_data():
    df = _df([
        ("2020-01-01 00:00:00", 1.0, 1.0, 1.0, 1.0, 1.0),
        ("2020-01-02 00:00:00", 2.0, 2.0, 2.0, 2.0, 1.0),
    ])
    bridge, _ = _fake_bridge(df)
    repo = CausalCandleRepository(bridge_loader=lambda *a: bridge)
    head = repo.load_candles_from("jp225_tick", "1D", 1577836800, 999, 10)
    assert [c["open"] for c in head] == [1.0, 2.0]  # 前置きは先頭で頭打ち・末尾は素材で打ち切り


def test_load_candles_from_matches_load_candles_shape():
    """窓の取り方だけが違い、足の形（tickvol 込み）は load_candles と同一。"""
    df = _df([("2020-01-03 00:00:00", 100.0, 105.0, 99.0, 101.0, 3.0)])
    bridge, _ = _fake_bridge(df)
    repo = CausalCandleRepository(bridge_loader=lambda *a: bridge)
    assert repo.load_candles_from("jp225_tick", "1D", 0, 0, None) == repo.load_candles(
        "jp225_tick", "1D", None
    )


def test_load_days_lists_distinct_utc_days_ascending():
    df = _df([
        ("2020-01-03 09:00:00", 1.0, 1.0, 1.0, 1.0, 1.0),
        ("2020-01-03 23:59:00", 1.0, 1.0, 1.0, 1.0, 1.0),
        ("2020-01-06 00:00:00", 1.0, 1.0, 1.0, 1.0, 1.0),
    ])
    bridge, _ = _fake_bridge(df)
    repo = CausalCandleRepository(bridge_loader=lambda *a: bridge)
    assert repo.load_days("jp225_tick", "1D") == ["2020-01-03", "2020-01-06"]


def test_load_days_rejects_unknown_ref():
    bridge, _ = _fake_bridge(_df([("2020-01-01", 1.0, 1.0, 1.0, 1.0, 1.0)]))
    repo = CausalCandleRepository(bridge_loader=lambda *a: bridge)
    with pytest.raises(ValueError):
        repo.load_days("totally_unknown_ref", "1D")
