"""lwc_chart アダプタの振る舞いを検証するテスト。

lightweight_charts に依存せず、create_line を備えたダックタイプの Fake チャートで
検証する（生成本数・系統別スタイル・値が build_bands と一致すること）。
"""

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src import PROBABILITIES, build_bands  # noqa: E402
from src.lwc_chart import DEFAULT_BUCKETS, add_profit_band  # noqa: E402


class _FakeLine:
    """create_line が返す Line のスタブ。set されたデータを保持する。"""

    def __init__(self, meta: dict):
        self.meta = meta
        self.data: pd.DataFrame | None = None

    def set(self, df: pd.DataFrame) -> None:
        self.data = df


class _FakeChart:
    """create_line / legend のみを備えたダックタイプのチャート。"""

    def __init__(self):
        self.lines: list[_FakeLine] = []
        self.legend_visible = None

    def create_line(self, name="", color="", style="solid", width=2,
                    price_line=True, price_label=True, price_scale_id=None):
        line = _FakeLine(dict(name=name, color=color, style=style, width=width,
                              price_line=price_line, price_label=price_label))
        self.lines.append(line)
        return line

    def legend(self, visible=False):
        self.legend_visible = visible


def _ohlc_df(n: int = 30) -> pd.DataFrame:
    rng = np.random.default_rng(0)
    open_ = 100 + np.cumsum(rng.normal(0, 1, n))
    close = open_ + rng.normal(0, 1, n)
    high = np.maximum(open_, close) + rng.uniform(0, 1, n)
    low = np.minimum(open_, close) - rng.uniform(0, 1, n)
    times = pd.date_range("2024-01-01", periods=n, freq="h")
    return pd.DataFrame({"date": times, "open": open_, "high": high,
                         "low": low, "close": close})


def test_creates_all_lines_by_default():
    chart = _FakeChart()
    lines = add_profit_band(chart, _ohlc_df())
    # 4 系統 × 7 水準 = 28 本
    assert len(chart.lines) == len(DEFAULT_BUCKETS) * len(PROBABILITIES) == 28
    assert len(lines) == 28


def test_style_mapping_solid_and_dotted():
    chart = _FakeChart()
    add_profit_band(chart, _ohlc_df())
    style_by_name = {ln.meta["name"]: ln.meta["style"] for ln in chart.lines}
    # 塗りバンド端は実線
    assert style_by_name["nOH 95%"] == "solid"
    assert style_by_name["pOL 95%"] == "solid"
    # 外側は点線
    assert style_by_name["pOH 95%"] == "dotted"
    assert style_by_name["nOL 95%"] == "dotted"


def test_values_match_build_bands():
    df = _ohlc_df()
    bands = build_bands(df)
    chart = _FakeChart()
    add_profit_band(chart, df)
    by_name = {ln.meta["name"]: ln for ln in chart.lines}
    for col in ("nOH_95", "pOL_99", "pOH_51", "nOL_80"):
        bucket, tag = col.split("_")
        line = by_name[f"{bucket} {tag}%"]
        assert np.allclose(line.data[f"{bucket} {tag}%"].to_numpy(),
                           bands[col].to_numpy())
        # 時刻列が付与されている
        assert "time" in line.data.columns


def test_subset_buckets_and_probabilities():
    chart = _FakeChart()
    add_profit_band(chart, _ohlc_df(), buckets=("nOH", "pOL"), probabilities=(0.95,))
    assert len(chart.lines) == 2
    assert {ln.meta["name"] for ln in chart.lines} == {"nOH 95%", "pOL 95%"}


def test_legend_toggle():
    chart = _FakeChart()
    add_profit_band(chart, _ohlc_df(), legend=True)
    assert chart.legend_visible is True


def test_price_line_label_disabled():
    chart = _FakeChart()
    add_profit_band(chart, _ohlc_df())
    # 28 本の価格ラベルで軸が埋まらないよう無効化されている
    assert all(ln.meta["price_line"] is False for ln in chart.lines)
    assert all(ln.meta["price_label"] is False for ln in chart.lines)


def test_time_resolution_via_index():
    df = _ohlc_df().set_index("date")  # DatetimeIndex 経由
    chart = _FakeChart()
    add_profit_band(chart, df)
    assert len(chart.lines) == 28


def test_missing_time_raises():
    df = _ohlc_df().drop(columns="date").reset_index(drop=True)
    chart = _FakeChart()
    with pytest.raises(KeyError):
        add_profit_band(chart, df)


def test_unknown_bucket_raises():
    chart = _FakeChart()
    with pytest.raises(KeyError):
        add_profit_band(chart, _ohlc_df(), buckets=("XYZ",))
