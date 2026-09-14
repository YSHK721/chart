"""tickvol_updown（上昇／下落ティック数の n 区間累積）の検証。

固定する仕様:
  - 累積は **当該バーを含む** 直近 window_n 本の合計（窓が満たない先頭は NaN）。
  - 描くのは 1 本のバー＝上昇 − 下落。正なら上昇優勢・負なら下落優勢。
  - バーの色は符号で切り替える（per-point color）。
  - up / dn 列を持たないデータセットでは KeyError（値を捏造しない）。
"""

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src import (  # noqa: E402
    NET_SERIES,
    cumulative_updown,
    net_updown,
    resolve_updown_columns,
)
from src.lwc_chart import add_tickvol_updown  # noqa: E402


class FakeSeries:
    def __init__(self, name, **kwargs):
        self.name = name
        self.kwargs = kwargs
        self.points = None

    def set(self, df):
        self.points = df


class FakeChart:
    def __init__(self):
        self.histograms = []

    def create_histogram(self, name, **kwargs):
        s = FakeSeries(name, **kwargs)
        self.histograms.append(s)
        return s


def _bars(n=12, *, updown=True, index=True):
    idx = pd.date_range("2026-01-05 00:00:00", periods=n, freq="5min")
    data = {
        "open": np.full(n, 100.0), "high": np.full(n, 101.0),
        "low": np.full(n, 99.0), "close": np.full(n, 100.5),
        "volume": np.arange(n, dtype=float) + 10.0,
    }
    if updown:
        data["up"] = np.arange(n, dtype=float) + 1.0
        data["dn"] = np.full(n, 2.0)
    df = pd.DataFrame(data, index=idx)
    return df if index else df.reset_index(drop=True)


# ---- core -----------------------------------------------------------------


def test_cumulative_is_the_sum_of_the_last_n_bars_including_current():
    df = _bars(12)
    up, dn = cumulative_updown(df, window_n=3)
    # 先頭 2 本は窓が満たない＝NaN（値を作らない）。
    assert np.isnan(up[0]) and np.isnan(up[1])
    # バー 2 の up = 1+2+3、dn = 2*3。
    assert up[2] == 6.0 and dn[2] == 6.0
    assert up[-1] == df["up"].iloc[-3:].sum()


def test_net_is_up_minus_dn():
    df = _bars(12)
    up, dn = cumulative_updown(df, window_n=3)
    net = net_updown(df, window_n=3)
    ok = np.isfinite(net)
    assert np.allclose(net[ok], (up - dn)[ok])


def test_window_n_below_one_raises():
    with pytest.raises(ValueError):
        cumulative_updown(_bars(5), window_n=0)


def test_missing_updown_columns_raise():
    with pytest.raises(KeyError):
        cumulative_updown(_bars(5, updown=False), window_n=3)


def test_resolve_columns_is_case_insensitive():
    df = pd.DataFrame({"UP": [1.0], "Dn": [2.0]})
    assert resolve_updown_columns(df) == ("UP", "Dn")


def test_non_numeric_becomes_nan_not_zero():
    df = _bars(5)
    df["up"] = ["1", "x", "3", "4", "5"]
    up, _dn = cumulative_updown(df, window_n=2)
    assert np.isnan(up[1]) and np.isnan(up[2])   # 非数を含む窓は NaN（0 埋めしない）


# ---- 出力アダプタ ----------------------------------------------------------


def test_add_creates_a_single_histogram_named_for_the_front():
    chart = FakeChart()
    out = add_tickvol_updown(chart, _bars(12), window_n=3)
    assert len(out) == 1
    assert [s.name for s in chart.histograms] == [NET_SERIES]
    # per-point color 列を持つ（バーごとに符号で色を変えるため）。
    assert list(chart.histograms[0].points.columns) == ["time", NET_SERIES, "color"]


def test_sign_decides_the_bar_color():
    df = _bars(12)
    df.loc[df.index[:6], "up"] = 0.0        # 前半は下落優勢にする
    chart = FakeChart()
    add_tickvol_updown(chart, df, window_n=3)
    pts = chart.histograms[0].points
    neg = pts[pts[NET_SERIES] < 0]["color"].unique()
    pos = pts[pts[NET_SERIES] > 0]["color"].unique()
    assert len(neg) == 1 and len(pos) == 1 and neg[0] != pos[0]


def test_warmup_rows_are_dropped_not_zero_filled():
    chart = FakeChart()
    add_tickvol_updown(chart, _bars(12), window_n=3)
    assert len(chart.histograms[0].points) == 12 - 2


def test_values_can_be_negative_when_down_dominates():
    df = _bars(12)
    df["up"] = 0.0
    assert (net_updown(df, window_n=3)[2:] < 0).all()


def test_raises_without_updown_columns():
    with pytest.raises(KeyError):
        add_tickvol_updown(FakeChart(), _bars(6, updown=False), window_n=3)


def test_raises_when_time_unresolvable():
    with pytest.raises(KeyError):
        add_tickvol_updown(FakeChart(), _bars(6, index=False), window_n=3)
