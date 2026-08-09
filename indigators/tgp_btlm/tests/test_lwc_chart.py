"""lightweight-charts 出力アダプタの検証（Fake チャート）。

描画ライブラリに依存させず、create_line を持つ Fake で本数・名前・スタイル・値・
異常系を確認する（ガイド §7）。
"""

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src import BtlmResult, mean_column, quantile_column  # noqa: E402
from src.lwc_chart import add_btlm  # noqa: E402


from indigators.testing.lwc_fakes import FakeChart, FakeLine  # noqa: E402


class _LinearFitter:
    def fit_predict(self, x, z, *, q_low=0.05, q_high=0.95):
        mean = np.asarray(z, dtype=float)
        return BtlmResult(mean=mean, q_low=mean - 1.0, q_high=mean + 1.0)


def _df(n=120):
    return pd.DataFrame(
        {
            "time": pd.date_range("2024-01-01", periods=n, freq="h"),
            "open": np.arange(n, dtype=float) + 10.0,
        }
    )


def test_creates_three_lines():
    chart = FakeChart()
    lines = add_btlm(chart, _df(), _LinearFitter(), maxbars=100)
    assert len(lines) == 3
    assert len(chart.lines) == 3


def test_line_names_match_value_columns():
    chart = FakeChart()
    add_btlm(chart, _df(), _LinearFitter(), maxbars=100, q_low=0.05, q_high=0.95)
    names = [l.name for l in chart.lines]
    assert names == [mean_column(), quantile_column(0.05), quantile_column(0.95)]
    # 各ラインの値列名はライン名と一致（ガイド §5）。
    for line in chart.lines:
        assert line.name in line.data.columns
        assert "time" in line.data.columns


def test_styles_solid_then_dotted():
    chart = FakeChart()
    add_btlm(chart, _df(), _LinearFitter(), maxbars=100)
    assert chart.lines[0].kwargs["style"] == "solid"
    assert chart.lines[1].kwargs["style"] == "dotted"
    assert chart.lines[2].kwargs["style"] == "dotted"
    for line in chart.lines:
        assert line.kwargs["price_line"] is False
        assert line.kwargs["price_label"] is False


def test_nan_rows_dropped():
    chart = FakeChart()
    add_btlm(chart, _df(120), _LinearFitter(), maxbars=100)
    # 窓=100、窓外 20 本は NaN → dropna で 100 行に。
    for line in chart.lines:
        assert len(line.data) == 100


def test_time_resolution_from_datetime_index():
    df = _df(50).set_index("time")  # time を index 化
    chart = FakeChart()
    add_btlm(chart, df, _LinearFitter(), maxbars=50)
    assert len(chart.lines) == 3
    assert "time" in chart.lines[0].data.columns


def test_missing_time_raises():
    df = pd.DataFrame({"open": np.arange(50.0)})  # time/date/DatetimeIndex なし
    chart = FakeChart()
    with pytest.raises(KeyError):
        add_btlm(chart, df, _LinearFitter(), maxbars=50)
