"""PRO!fitMFI lightweight-charts 出力アダプタの検証（Fake チャート）。

描画ライブラリ（lightweight_charts）に依存させず、create_line /
horizontal_line を持つ Fake で本数（線 2 本・水平線 7 本）・名前・値・
name 一致・異常系（必須列欠落・volume 欠落・時刻欠落）を確認する
（PORTING_GUIDE §6/§7）。テストファースト（Red→Green）。
"""

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src import MA_COLUMN, MFI_COLUMN  # noqa: E402
from src.lwc_chart import _LEVEL_KEYS, add_mfi  # noqa: E402


from indigators.testing.lwc_fakes import FakeChart, FakeLine  # noqa: E402


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
# TC-21 線 2 本（MFI / MA）＋ 水準線 7 本（±1/2/3σ, mid50）を生成する
# ---------------------------------------------------------------------------
def test_creates_two_lines_and_seven_level_lines():
    chart = FakeChart()
    created = add_mfi(chart, _df(), mfi_period=14, ma_period=5)
    assert len(chart.lines) == 2
    assert len(chart.hlines) == 7
    assert len(_LEVEL_KEYS) == 7
    assert len(created) == 2 + 7


# ---------------------------------------------------------------------------
# TC-22 ライン名が値列名（mfi / mfi_ma）と一致する
# ---------------------------------------------------------------------------
def test_line_names_match_value_columns():
    chart = FakeChart()
    add_mfi(chart, _df(), mfi_period=14, ma_period=5)
    names = [ln.name for ln in chart.lines]
    assert names == [MFI_COLUMN, MA_COLUMN]
    for ln in chart.lines:
        assert ln.name in ln.data.columns
        assert "time" in ln.data.columns


# ---------------------------------------------------------------------------
# TC-23 ライン値が build_mfi の出力と一致する
# ---------------------------------------------------------------------------
def test_line_values_match_build_output():
    from src import build_mfi

    df = _df()
    chart = FakeChart()
    add_mfi(chart, df, mfi_period=14, ma_period=5)
    built = build_mfi(df, mfi_period=14, ma_period=5)
    for col, line in zip((MFI_COLUMN, MA_COLUMN), chart.lines):
        expected = built[col].to_numpy()
        got = line.data[col].to_numpy()
        assert len(got) == len(df)
        assert np.allclose(got, expected)


# ---------------------------------------------------------------------------
# TC-24 水準線の価格が mfi_levels の 7 水準と一致する
# ---------------------------------------------------------------------------
def test_level_prices_match_mfi_levels():
    from src import mfi_levels

    df = _df()
    chart = FakeChart()
    add_mfi(chart, df, mfi_period=14, ma_period=5)
    levels = mfi_levels(df, mfi_period=14, ma_period=5)
    prices = sorted(h["price"] for h in chart.hlines)
    expected = sorted(levels[k] for k in _LEVEL_KEYS)
    assert np.allclose(prices, expected)
    assert any(np.isclose(p, 50.0) for p in prices)  # mid50 が含まれる


# ---------------------------------------------------------------------------
# TC-25 draw_levels=False で水準線を抑止できる
# ---------------------------------------------------------------------------
def test_levels_can_be_disabled():
    chart = FakeChart()
    add_mfi(chart, _df(), mfi_period=14, ma_period=5, draw_levels=False)
    assert len(chart.hlines) == 0
    assert len(chart.lines) == 2


# ---------------------------------------------------------------------------
# TC-26 多数線のため price_line / price_label は False
# ---------------------------------------------------------------------------
def test_line_price_flags_off():
    chart = FakeChart()
    add_mfi(chart, _df(), mfi_period=14, ma_period=5)
    for ln in chart.lines:
        assert ln.kwargs["price_line"] is False
        assert ln.kwargs["price_label"] is False


# ---------------------------------------------------------------------------
# TC-27 DatetimeIndex から時刻を解決できる
# ---------------------------------------------------------------------------
def test_time_resolution_from_datetime_index():
    df = _df(30).set_index("time")
    chart = FakeChart()
    add_mfi(chart, df, mfi_period=14, ma_period=5)
    assert "time" in chart.lines[0].data.columns


# ---------------------------------------------------------------------------
# TC-28 時刻列が解決できないと KeyError
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
        add_mfi(chart, df, mfi_period=14, ma_period=5)


# ---------------------------------------------------------------------------
# TC-29 HLC 列欠落で KeyError
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
        add_mfi(chart, df, mfi_period=14, ma_period=5)


# ---------------------------------------------------------------------------
# TC-30 volume 列欠落で KeyError（MFI 固有の必須列）
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
        add_mfi(chart, df, mfi_period=14, ma_period=5)
