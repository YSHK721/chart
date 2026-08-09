"""層名: 出力アダプタテスト（lightweight-charts / Fake チャート）。

責務:
    PRO!fit_HLBand の overlay 出力アダプタ ``add_hl_band`` を、描画ライブラリ
    （``lightweight_charts``）に依存させず Fake チャートで固定する。本指標は
    separate ヒストグラムを持たない overlay 専用指標のため、メイン chart へ
    水平線 8 本（up_067/up_165/up_196/up_258・dn_067/dn_165/dn_196/dn_258）のみを
    追加する。価格系列の描画は呼び出し側前提（バンドのみ追加）。

検証内容:
    * 水平線が 8 本生成される（up 4 / dn 4）。
    * 各水平線の price 値が hl_band_levels（up_*/dn_*）と一致する。
    * 各水平線の text（name）が対応する levels キーと一致する。
    * 多数線のため axis_label_visible=False（実 horizontal_line API 準拠・ISSUE-008）。
    * 異常系: 必須列（high/low/close）欠落 → KeyError、時刻解決不可 → KeyError。

依存: 標準 sys/pathlib / 外部 numpy, pandas, pytest / プロジェクト内 src.lwc_chart
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.lwc_chart import _BAND_KEYS, add_hl_band  # noqa: E402


from indigators.testing.lwc_fakes import FakeChart  # noqa: E402


def _df(n=12):
    rng = np.arange(n, dtype=float)
    high = 10.0 + rng + np.sin(rng)
    low = high - (1.0 + 0.5 * np.cos(rng))
    close = (high + low) / 2.0
    return pd.DataFrame(
        {
            "time": pd.date_range("2024-01-01", periods=n, freq="h"),
            "high": high,
            "low": low,
            "close": close,
        }
    )


# --- overlay（メインチャート: 水平線 8 本） -------------------------------


def test_add_hl_band_creates_eight_horizontal_lines():
    chart = FakeChart()
    created = add_hl_band(chart, _df())
    assert len(chart.hlines) == 8
    assert len(_BAND_KEYS) == 8
    assert len(created) == 8


def test_add_hl_band_keys_are_four_up_and_four_dn():
    up = [k for k in _BAND_KEYS if k.startswith("up_")]
    dn = [k for k in _BAND_KEYS if k.startswith("dn_")]
    assert len(up) == 4
    assert len(dn) == 4
    assert set(_BAND_KEYS) == {
        "up_067", "up_165", "up_196", "up_258",
        "dn_067", "dn_165", "dn_196", "dn_258",
    }


def test_add_hl_band_prices_match_hl_band_levels():
    from src import hl_band_levels

    df = _df()
    chart = FakeChart()
    add_hl_band(chart, df)
    levels = hl_band_levels(df)
    prices = sorted(h["price"] for h in chart.hlines)
    expected = sorted(levels[k] for k in _BAND_KEYS)
    assert np.allclose(prices, expected)


def test_add_hl_band_each_line_text_matches_its_level_key():
    from src import hl_band_levels

    df = _df()
    chart = FakeChart()
    add_hl_band(chart, df)
    levels = hl_band_levels(df)
    by_text = {h["text"]: h["price"] for h in chart.hlines}
    assert set(by_text.keys()) == set(_BAND_KEYS)
    for key in _BAND_KEYS:
        assert by_text[key] == pytest.approx(levels[key], abs=1e-12)


def test_add_hl_band_lines_have_axis_label_off():
    # 実 lightweight_charts の horizontal_line は price_line/price_label を受けない
    # （ISSUE-008）。軸ラベル抑制は axis_label_visible=False で行う。
    chart = FakeChart()
    add_hl_band(chart, _df())
    for h in chart.hlines:
        assert h["axis_label_visible"] is False
        assert "price_line" not in h and "price_label" not in h


def test_add_hl_band_resolves_time_from_datetime_index():
    # 時刻が DatetimeIndex でも解決でき、8 本を生成する。
    df = _df().set_index("time")
    chart = FakeChart()
    created = add_hl_band(chart, df)
    assert len(created) == 8


def test_add_hl_band_missing_close_raises_keyerror():
    df = pd.DataFrame(
        {
            "time": pd.date_range("2024-01-01", periods=5, freq="h"),
            "high": np.arange(5, dtype=float) + 10.0,
            "low": np.arange(5, dtype=float) + 9.0,
        }
    )
    chart = FakeChart()
    with pytest.raises(KeyError):
        add_hl_band(chart, df)


def test_add_hl_band_missing_high_raises_keyerror():
    df = pd.DataFrame(
        {
            "time": pd.date_range("2024-01-01", periods=5, freq="h"),
            "low": np.arange(5, dtype=float) + 9.0,
            "close": np.arange(5, dtype=float) + 9.5,
        }
    )
    chart = FakeChart()
    with pytest.raises(KeyError):
        add_hl_band(chart, df)


def test_add_hl_band_missing_time_raises_keyerror():
    n = 6
    high = np.arange(n, dtype=float) + 10.0
    df = pd.DataFrame(
        {"high": high, "low": high - 1.0, "close": high - 0.5}
    )  # 時刻なし（time/date 列も DatetimeIndex も無い）
    chart = FakeChart()
    with pytest.raises(KeyError):
        add_hl_band(chart, df)


def test_add_hl_band_explicit_time_column_missing_raises_keyerror():
    df = _df()
    chart = FakeChart()
    with pytest.raises(KeyError):
        add_hl_band(chart, df, time_column="nonexistent")
