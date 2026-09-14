"""ISSUE-158（リプレイ再生高速化 ①）: DataFrame→plain bars 変換のベクトル化の等価性検証。

旧実装（行ループ: df.iloc / df.iterrows）を参照実装としてテスト内に凍結し、
ベクトル化後の実装が **完全同一の出力**（キー・順序・型・値）を返すことを固定する。
挙動同一が本改修の絶対条件（ユーザー確認 2026-07-23「振る舞いに変更はないな？」→「ありません」）。
"""
from __future__ import annotations

import math

import pandas as pd

from simulator.replay_ui.adapter.causal_candle_repository import CausalCandleRepository
from simulator.replay_ui.adapter.causal_compute_gateway import CausalComputeGateway


def _make_df(n: int = 500, with_volume: bool = True) -> pd.DataFrame:
    index = pd.to_datetime([1_700_000_000 + 60 * i for i in range(n)], unit="s")
    base = [30_000.0 + (i % 97) * 3.25 for i in range(n)]
    data = {
        "Open": base,
        "High": [b + 5.5 for b in base],
        "Low": [b - 4.25 for b in base],
        "Close": [b + 1.125 for b in base],
    }
    if with_volume:
        data["Volume"] = [float(i % 11) for i in range(n)]
    return pd.DataFrame(data, index=index)


# ---- 参照実装（改修前の実装を逐語で凍結・回帰の壁） ----

def _df_to_bars_reference(df: pd.DataFrame) -> "list[dict]":
    secs = df.index.values.astype("datetime64[s]").astype("int64")
    cols = list(df.columns)
    bars: "list[dict]" = []
    for pos in range(len(df)):
        row = df.iloc[pos]
        bar: dict = {"time": int(secs[pos])}
        for c in cols:
            bar[str(c).lower()] = float(row[c])
        bars.append(bar)
    return bars


def _candles_reference(df: pd.DataFrame) -> "list[dict]":
    lower = {str(c).lower(): c for c in df.columns}
    secs = df.index.values.astype("datetime64[s]").astype("int64")
    col_o, col_h, col_l, col_c = (lower["open"], lower["high"], lower["low"], lower["close"])
    col_v = lower.get("volume")
    out: "list[dict]" = []
    for i, (_, row) in enumerate(df.iterrows()):
        d = {
            "time": int(secs[i]),
            "open": float(row[col_o]),
            "high": float(row[col_h]),
            "low": float(row[col_l]),
            "close": float(row[col_c]),
        }
        if col_v is not None:
            v = float(row[col_v])
            if math.isfinite(v):
                d["tickvol"] = int(v)
        out.append(d)
    return out


# ---- compute gateway: _df_to_bars ----

def test_df_to_bars_identical_to_reference():
    df = _make_df(500)
    assert CausalComputeGateway._df_to_bars(df) == _df_to_bars_reference(df)


def test_df_to_bars_identical_without_volume():
    df = _make_df(50, with_volume=False)
    assert CausalComputeGateway._df_to_bars(df) == _df_to_bars_reference(df)


def test_df_to_bars_key_order_and_types():
    df = _make_df(3)
    bars = CausalComputeGateway._df_to_bars(df)
    ref = _df_to_bars_reference(df)
    for b, r in zip(bars, ref):
        assert list(b.keys()) == list(r.keys())          # キー順（time が先頭・列順維持）
        assert isinstance(b["time"], int)
        assert all(isinstance(b[k], float) for k in b if k != "time")


def test_df_to_bars_nan_positions_match():
    df = _make_df(10)
    df.iloc[3, df.columns.get_loc("Close")] = float("nan")
    bars = CausalComputeGateway._df_to_bars(df)
    ref = _df_to_bars_reference(df)
    for b, r in zip(bars, ref):
        assert set(b.keys()) == set(r.keys())
        for k in b:
            if isinstance(b[k], float) and math.isnan(b[k]):
                assert math.isnan(r[k])
            else:
                assert b[k] == r[k]


def test_df_to_bars_empty():
    df = _make_df(0)
    assert CausalComputeGateway._df_to_bars(df) == []


def test_df_to_bars_roundtrip_via_bars_to_df():
    # 既存の完全逆変換不変条件が保たれる（_bars_to_df は無改修・組合せの回帰）。
    df = _make_df(20)
    bars = CausalComputeGateway._df_to_bars(df)
    df2 = CausalComputeGateway._bars_to_df(bars)
    assert list(df2.columns) == [str(c).lower() for c in df.columns]
    assert (df2.index.values.astype("datetime64[s]").astype("int64")
            == df.index.values.astype("datetime64[s]").astype("int64")).all()


# ---- candle repository: 変換部（tickvol の isfinite 規則込み） ----

def _convert_candles(df: pd.DataFrame) -> "list[dict]":
    # load_candles の変換部だけを dataset 非依存で通す（fake loader で df を注入）。
    class _FakeDataset:
        def is_known(self, ref):
            return True
        def is_known_timeframe(self, tf):
            return True
        def load_dataframe(self, ref, timeframe):
            return df
    class _FakeBridge:
        dataset = _FakeDataset()
    repo = CausalCandleRepository(bridge_loader=lambda *_a: _FakeBridge())
    return repo.load_candles("jp225_tick", "1m", None)


def test_candles_identical_to_reference():
    df = _make_df(300)
    assert _convert_candles(df) == _candles_reference(df)


def test_candles_nan_volume_row_drops_tickvol_only():
    df = _make_df(10)
    df.iloc[4, df.columns.get_loc("Volume")] = float("nan")
    got = _convert_candles(df)
    ref = _candles_reference(df)
    assert got == ref
    assert "tickvol" not in got[4] and "tickvol" in got[3]


def test_candles_without_volume_column():
    df = _make_df(10, with_volume=False)
    got = _convert_candles(df)
    assert got == _candles_reference(df)
    assert all("tickvol" not in d for d in got)
