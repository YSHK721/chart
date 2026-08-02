"""tickvol（ティックボリューム）の core + 出力アダプタ検証。

描画ライブラリ（lightweight_charts）に依存させず、``create_histogram`` を持つ Fake チャートで
ヒストグラム 1 本・名前と値列名の一致・値が volume の素通しであること・時刻解決・
異常系（volume 欠落 / 時刻欠落）・NaN 行の非描画を確認する
（profit_volatility/tests/test_lwc_chart.py を踏襲）。
"""

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src import TICKVOL_COLUMN, build_tickvol, resolve_volume_column  # noqa: E402
from src.lwc_chart import add_tickvol  # noqa: E402


class FakeHistogram:
    def __init__(self, name, **kwargs):
        self.name = name
        self.kwargs = kwargs
        self.points = None

    def set(self, df):
        self.points = df


class FakeChart:
    def __init__(self):
        self.histograms = []
        self.lines = []

    def create_histogram(self, name, **kwargs):
        h = FakeHistogram(name, **kwargs)
        self.histograms.append(h)
        return h

    def create_line(self, name, **kwargs):
        line = FakeHistogram(name, **kwargs)
        self.lines.append(line)
        return line


def _ohlcv(n=10, *, index=True, volume=True):
    """最小妥当な OHLCV（DatetimeIndex・volume は決定論的な整数列）。"""
    idx = pd.date_range("2026-01-05 00:00:00", periods=n, freq="5min")
    data = {
        "open": np.full(n, 100.0),
        "high": np.full(n, 101.0),
        "low": np.full(n, 99.0),
        "close": np.full(n, 100.5),
    }
    if volume:
        data["volume"] = np.arange(n, dtype=float) * 10.0 + 7.0
    df = pd.DataFrame(data, index=idx)
    if not index:
        df = df.reset_index(drop=True)
    return df


# ---- core -----------------------------------------------------------------


def test_build_tickvol_passes_volume_through_unchanged():
    # 点ごとの写像＝加工しない（平滑・正規化を持たない仕様の固定）。
    df = _ohlcv(5)
    got = build_tickvol(df)
    assert got.tolist() == df["volume"].tolist()


def test_resolve_volume_column_is_case_insensitive_and_accepts_vol():
    assert resolve_volume_column(pd.DataFrame({"Volume": [1.0]})) == "Volume"
    assert resolve_volume_column(pd.DataFrame({"VOL": [1.0]})) == "VOL"


def test_resolve_volume_column_raises_without_volume():
    with pytest.raises(KeyError):
        resolve_volume_column(pd.DataFrame({"close": [1.0]}))


def test_build_tickvol_coerces_non_numeric_to_nan():
    df = pd.DataFrame({"volume": ["12", "x", 3]})
    got = build_tickvol(df)
    assert got[0] == 12.0
    assert np.isnan(got[1])
    assert got[2] == 3.0


# ---- 出力アダプタ ----------------------------------------------------------


def test_add_tickvol_creates_single_histogram_named_tickvol():
    chart = FakeChart()
    out = add_tickvol(chart, _ohlcv(6))
    # ヒストグラム 1 + 正常帯 2 + 水準線 3 + トレンド（mean 1 + 帯 2 + off 2 + 読取 3）。
    assert len(out) == 14
    assert len(chart.histograms) == 1
    h = chart.histograms[0]
    # 系列名は front の SeriesDef.seriesName（usecase/catalog.js）と一致させる契約。
    assert h.name == TICKVOL_COLUMN
    # 値列名は系列名と一致（FakeChart._line_points の収集規約）。
    assert list(h.points.columns) == ["time", TICKVOL_COLUMN]


def test_add_tickvol_values_equal_source_volume():
    df = _ohlcv(6)
    chart = FakeChart()
    add_tickvol(chart, df)
    assert chart.histograms[0].points[TICKVOL_COLUMN].tolist() == df["volume"].tolist()


def test_add_tickvol_resolves_time_from_datetime_index_and_time_column():
    df = _ohlcv(4)
    chart = FakeChart()
    add_tickvol(chart, df)
    assert chart.histograms[0].points["time"].tolist() == list(df.index)

    flat = df.reset_index(names="time")
    chart2 = FakeChart()
    add_tickvol(chart2, flat)
    assert chart2.histograms[0].points["time"].tolist() == list(df.index)


def test_add_tickvol_drops_rows_without_volume():
    # リプレイの形成中バー（OHLC のみ）は volume を持たない＝点を立てない（値を捏造しない）。
    df = _ohlcv(5)
    df.loc[df.index[-1], "volume"] = np.nan
    chart = FakeChart()
    add_tickvol(chart, df)
    assert len(chart.histograms[0].points) == 4


def test_add_tickvol_raises_without_volume_column():
    with pytest.raises(KeyError):
        add_tickvol(FakeChart(), _ohlcv(3, volume=False))


def test_add_tickvol_raises_when_time_unresolvable():
    with pytest.raises(KeyError):
        add_tickvol(FakeChart(), _ohlcv(3, index=False))


def test_add_tickvol_accepts_color_override():
    chart = FakeChart()
    add_tickvol(chart, _ohlcv(3), color="rgba(1, 2, 3, 0.5)")
    assert chart.histograms[0].kwargs["color"] == "rgba(1, 2, 3, 0.5)"
