"""PRO!fitRMMMACD lightweight-charts 出力アダプタの検証（Fake チャート）。

描画ライブラリ（lightweight_charts）に依存させず、create_histogram /
create_line / horizontal_line を持つ Fake で、本数（ヒスト 1 本・線 2 本・
**水平線 0 本**）・名前・値・name↔値列一致・異常系（必須列欠落・volume 欠落・
時刻欠落）を確認する（PORTING_GUIDE §6/§7）。テストファースト（Red→Green）。

**本指標は σ 水準線を持たない**（元 funIndicatorSet 未呼出）。先例
profit_mfi_macd の test_lwc_chart にあった「水平線 7 本」「draw_levels 抑止」の
検証は削除し、代わりに「水平線が 0 本であること」を固定する discriminating な
テスト（TC-L5）を置く。
"""

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src import HIST_COLUMN, MACD_COLUMN, SIGNAL_COLUMN, build_rmmmacd  # noqa: E402
from src.lwc_chart import (  # noqa: E402
    MACD_LINE_NAME,
    SIGNAL_LINE_NAME,
    add_rmmmacd,
)

_KW = dict(osc_period=6, ma_period=6, fast=4, slow=8, signal=4)


class FakeSeries:
    def __init__(self, kind, name, **kwargs):
        self.kind = kind
        self.name = name
        self.kwargs = kwargs
        self.data = None

    def set(self, data):
        self.data = data


class FakeChart:
    def __init__(self):
        self.histograms = []
        self.lines = []
        self.hlines = []

    def create_histogram(self, name, **kwargs):
        s = FakeSeries("histogram", name, **kwargs)
        self.histograms.append(s)
        return s

    def create_line(self, name, **kwargs):
        s = FakeSeries("line", name, **kwargs)
        self.lines.append(s)
        return s

    def horizontal_line(self, price, **kwargs):
        # 本指標では呼ばれてはならない（σ 水準線なし）。呼ばれたら記録して fail させる。
        line = {"price": price, **kwargs}
        self.hlines.append(line)
        return line


def _df(n=40):
    # 単調増加の合成 OHLCV（volume 列必須）。
    h = np.arange(n, dtype=float) + 10.0
    return pd.DataFrame(
        {
            "time": pd.date_range("2024-01-01", periods=n, freq="h"),
            "high": h,
            "low": h - 1.0,
            "close": h - 0.5,
            "volume": np.arange(n, dtype=float) * 10.0 + 100.0,
        }
    )


# ---------------------------------------------------------------------------
# TC-L1 ヒスト 1 本・線 2 本を生成する（水平線は 0 本）
# ---------------------------------------------------------------------------
def test_creates_one_histogram_two_lines():
    chart = FakeChart()
    created = add_rmmmacd(chart, _df(), **_KW)
    assert len(chart.histograms) == 1
    assert len(chart.lines) == 2
    assert len(created) == 1 + 2


# ---------------------------------------------------------------------------
# TC-L2 ヒストグラム名が値列名（rmmmacd_hist）と一致する
# ---------------------------------------------------------------------------
def test_histogram_name_matches_value_column():
    chart = FakeChart()
    add_rmmmacd(chart, _df(), **_KW)
    hist = chart.histograms[0]
    assert hist.name == HIST_COLUMN
    assert HIST_COLUMN in hist.data.columns
    assert "time" in hist.data.columns


# ---------------------------------------------------------------------------
# TC-L3 ライン名（RMMWMACD / Signal）が値列名と一致する
# ---------------------------------------------------------------------------
def test_line_names_match_value_columns():
    chart = FakeChart()
    add_rmmmacd(chart, _df(), **_KW)
    names = [ln.name for ln in chart.lines]
    assert names == [MACD_LINE_NAME, SIGNAL_LINE_NAME]
    for ln in chart.lines:
        # 値列名はライン name と完全一致（§5）。
        assert ln.name in ln.data.columns
        assert "time" in ln.data.columns


# ---------------------------------------------------------------------------
# TC-L4 系列値が build_rmmmacd の出力（NaN 除外後）と一致する
#   warm-up NaN は描画側で非描画にするため set 前に dropna 除外する（姉妹
#   profit_rmm と整合）。本テストは window=None（全期間版＝NaN 無し）を用いて
#   全行が保持されかつ値が build と一致することを固定する。warm-up NaN 除外の
#   挙動自体は TC-L11 で discriminating に固定する。
# ---------------------------------------------------------------------------
def test_series_values_match_build_output():
    df = _df()
    chart = FakeChart()
    add_rmmmacd(chart, df, **_KW, window=None)  # 全期間版: NaN 無しで全行保持
    built = build_rmmmacd(df, **_KW, window=None)

    # ヒストグラム値（rmmmacd_hist）。
    hist = chart.histograms[0]
    assert len(hist.data) == len(df)
    np.testing.assert_allclose(
        hist.data[HIST_COLUMN].to_numpy(), built[HIST_COLUMN].to_numpy()
    )
    # ライン値（macd→RMMWMACD, signal→Signal にマップ）。
    expected_cols = (MACD_COLUMN, SIGNAL_COLUMN)
    for value_col, line in zip(expected_cols, chart.lines):
        got = line.data[line.name].to_numpy()
        assert len(got) == len(df)
        np.testing.assert_allclose(got, built[value_col].to_numpy())


# ---------------------------------------------------------------------------
# TC-L5 水平線が 0 本であること（σ 水準線なし・本指標固有の discriminating）
# ---------------------------------------------------------------------------
def test_no_horizontal_lines_drawn():
    chart = FakeChart()
    add_rmmmacd(chart, _df(), **_KW)
    # horizontal_line は一切呼ばれない（元 funIndicatorSet 未呼出）。
    assert len(chart.hlines) == 0
    # add_rmmmacd は draw_levels パラメータを持たない（構造で水準線の不在を担保）。
    import inspect

    sig = inspect.signature(add_rmmmacd)
    assert "draw_levels" not in sig.parameters


# ---------------------------------------------------------------------------
# TC-L6 多数系列のため price_line / price_label は False（§6）
# ---------------------------------------------------------------------------
def test_series_price_flags_off():
    chart = FakeChart()
    add_rmmmacd(chart, _df(), **_KW)
    for s in (*chart.histograms, *chart.lines):
        assert s.kwargs["price_line"] is False
        assert s.kwargs["price_label"] is False


# ---------------------------------------------------------------------------
# TC-L7 DatetimeIndex から時刻を解決できる
# ---------------------------------------------------------------------------
def test_time_resolution_from_datetime_index():
    df = _df(30).set_index("time")
    chart = FakeChart()
    add_rmmmacd(chart, df, **_KW)
    assert "time" in chart.histograms[0].data.columns
    assert "time" in chart.lines[0].data.columns


# ---------------------------------------------------------------------------
# TC-L8 時刻列が解決できないと KeyError
# ---------------------------------------------------------------------------
def test_missing_time_raises():
    n = 30
    h = np.arange(n, dtype=float) + 10.0
    df = pd.DataFrame(
        {
            "high": h,
            "low": h - 1.0,
            "close": h - 0.5,
            "volume": np.full(n, 100.0),
        }
    )  # 時刻なし
    chart = FakeChart()
    with pytest.raises(KeyError):
        add_rmmmacd(chart, df, **_KW)


# ---------------------------------------------------------------------------
# TC-L9 HLC 列欠落で KeyError
# ---------------------------------------------------------------------------
def test_missing_hlc_raises():
    n = 30
    df = pd.DataFrame(
        {
            "time": pd.date_range("2024-01-01", periods=n, freq="h"),
            "volume": np.full(n, 100.0),
        }
    )  # HLC なし
    chart = FakeChart()
    with pytest.raises(KeyError):
        add_rmmmacd(chart, df, **_KW)


# ---------------------------------------------------------------------------
# TC-L10 volume 列欠落で KeyError（RMMMACD 固有の必須列）
# ---------------------------------------------------------------------------
def test_missing_volume_raises():
    n = 30
    h = np.arange(n, dtype=float) + 10.0
    df = pd.DataFrame(
        {
            "time": pd.date_range("2024-01-01", periods=n, freq="h"),
            "high": h,
            "low": h - 1.0,
            "close": h - 0.5,
        }
    )  # volume なし
    chart = FakeChart()
    with pytest.raises(KeyError):
        add_rmmmacd(chart, df, **_KW)


# ---------------------------------------------------------------------------
# TC-L11 warm-up NaN 行を histogram 系列から除外する（非描画保証・姉妹整合）
#   因果版（window=120）は先頭 window-1 が NaN（warm-up・非描画）。lightweight-charts
#   へ NaN を渡さず dropna 除外して描画系へ有限値のみ渡す（姉妹 profit_rmm と整合）。
# ---------------------------------------------------------------------------
def test_histogram_drops_warmup_nan_rows():
    # Arrange: n>window で warm-up NaN（window-1 本）と有限行が共存する。
    W = 120
    n = 150
    df = _df(n)
    built = build_rmmmacd(df, **_KW, window=W)
    n_nan = int(built[HIST_COLUMN].isna().sum())
    assert n_nan > 0, "前提: warm-up NaN が存在する n を選ぶこと"

    # Act
    chart = FakeChart()
    add_rmmmacd(chart, df, **_KW, window=W)

    # Assert: histogram 系列に NaN 行が 1 つも無く、行数 = 有限行数（= n - n_nan）。
    hist = chart.histograms[0]
    assert not hist.data[HIST_COLUMN].isna().any(), "warm-up NaN が描画系へ漏れている"
    assert len(hist.data) == n - n_nan
    # 生き残った値が build の有限値と完全一致する（dropna が値を歪めない）。
    expected_finite = built[HIST_COLUMN].to_numpy()[
        ~np.isnan(built[HIST_COLUMN].to_numpy())
    ]
    np.testing.assert_allclose(
        hist.data[HIST_COLUMN].to_numpy(), expected_finite, rtol=0, atol=0
    )
