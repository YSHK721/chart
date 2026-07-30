"""価格スケール上の CVFE バンドの検証（表示仕様・lwc_chart.cvfe_bands）。

正本仕様: indigators/cvfe/CVFE_spec_v1.0.md §4 柱書（因果）・§4.8（σ̂ の合成）
表示仕様: indigators/cvfe/src/lwc_chart.py の docstring（ISSUE-223）
共有参照: common/marod_bands.quantile_bands・common/event_quantiles.outlier_event_quantiles

検証観点:
    - 中心が 1 本前の確定終値（当該バーの値動きを使わない＝因果）
    - 上下が mid · exp(± k σ̂)（対数収益 σ の価格への写像は比率）
    - available=False・非有限は全系列 nan
    - 当該バーの高安終値を変えてもバンドが動かない（非リペイント）
"""

import sys
from pathlib import Path

import numpy as np
import pytest

_PKG_DIR = Path(__file__).resolve().parents[1]
if str(_PKG_DIR) not in sys.path:
    sys.path.insert(0, str(_PKG_DIR))

from src.lwc_chart import (  # noqa: E402
    DEFAULT_SIGMA_INNER,
    DEFAULT_SIGMA_OUTER,
    cvfe_bands,
    outlier_levels_in_price,
    standardized_residuals,
)


def test_default_multipliers():
    """既定は内側 1σ・外側 2σ。"""
    assert DEFAULT_SIGMA_INNER == 1.0
    assert DEFAULT_SIGMA_OUTER == 2.0


def test_center_is_previous_close_not_current():
    """中心は close_{t−1}。当該バーの終値ではない（因果）。"""
    close = np.array([100.0, 110.0, 120.0, 130.0])
    sigma = np.full(4, 0.01)
    avail = np.array([False, True, True, True])
    mid, *_rest = cvfe_bands(close, sigma, avail)
    assert np.isnan(mid[0])                      # 1 本目は直前が無い
    assert mid[1] == pytest.approx(100.0)
    assert mid[2] == pytest.approx(110.0)
    assert mid[3] == pytest.approx(120.0)


def test_band_edges_match_exponential_mapping():
    """上下 = mid · exp(± k σ̂)。加減算ではなく比率で写像する。"""
    close = np.array([100.0, 100.0, 100.0])
    sigma = np.array([np.nan, 0.02, 0.02])
    avail = np.array([False, True, True])
    mid, u1, l1, u2, l2 = cvfe_bands(close, sigma, avail, sigma_inner=1.0, sigma_outer=2.5)

    assert u1[1] == pytest.approx(100.0 * np.exp(0.02))
    assert l1[1] == pytest.approx(100.0 * np.exp(-0.02))
    assert u2[1] == pytest.approx(100.0 * np.exp(2.5 * 0.02))
    assert l2[1] == pytest.approx(100.0 * np.exp(-2.5 * 0.02))
    # 上下は中心について幾何的に対称（対数スケールで等距離）。
    assert (u1[1] / mid[1]) == pytest.approx(mid[1] / l1[1])


def test_outer_band_encloses_inner_band():
    close = np.array([100.0, 100.0])
    sigma = np.array([np.nan, 0.03])
    avail = np.array([False, True])
    _mid, u1, l1, u2, l2 = cvfe_bands(close, sigma, avail)
    assert l2[1] < l1[1] < u1[1] < u2[1]


def test_outlier_levels_use_the_shared_primitive_keys():
    """外れ値水準のキーが共有プリミティブと同一（表示規約の単一情報源に載る）。"""
    from common.event_quantiles import EVQ_LINE_SPECS

    rng = np.random.default_rng(3)
    n = 900
    close = 10_000.0 * np.exp(np.cumsum(rng.standard_normal(n) * 0.01))
    sigma = np.full(n, 0.01)
    avail = np.ones(n, dtype=bool)
    mid = np.concatenate([[np.nan], close[:-1]])

    evq = outlier_levels_in_price(close, sigma, avail, mid, window_n=200, k_events=20)
    for key, _style in EVQ_LINE_SPECS:
        assert key in evq, key
        assert evq[key].shape == (n,)


def test_outlier_levels_lie_outside_the_one_sigma_band():
    """外れ値水準は 1σ バンドの外側に出る（「外れた」ときの水準なので当然）。"""
    rng = np.random.default_rng(5)
    n = 1200
    close = 10_000.0 * np.exp(np.cumsum(rng.standard_normal(n) * 0.01))
    sigma = np.full(n, 0.01)
    avail = np.ones(n, dtype=bool)
    mid, u1, l1, _u2, _l2 = cvfe_bands(close, sigma, avail)

    evq = outlier_levels_in_price(close, sigma, avail, mid, window_n=200, k_events=20)
    ok = np.isfinite(evq["med_hi"]) & np.isfinite(u1)
    assert ok.sum() > 50, "有効なバーが少なすぎて検証にならない"
    assert np.all(evq["med_hi"][ok] > u1[ok]), "上側の典型深度が 1σ の内側にある"
    ok_lo = np.isfinite(evq["med_lo"]) & np.isfinite(l1)
    assert np.all(evq["med_lo"][ok_lo] < l1[ok_lo]), "下側の典型深度が 1σ の内側にある"


def test_extreme_level_is_beyond_typical_level():
    """極端深度は典型深度よりさらに外側。"""
    rng = np.random.default_rng(7)
    n = 1500
    close = 10_000.0 * np.exp(np.cumsum(rng.standard_normal(n) * 0.01))
    sigma = np.full(n, 0.01)
    avail = np.ones(n, dtype=bool)
    mid = np.concatenate([[np.nan], close[:-1]])
    evq = outlier_levels_in_price(close, sigma, avail, mid, window_n=200, k_events=20)
    ok = np.isfinite(evq["ext_hi"]) & np.isfinite(evq["med_hi"])
    assert ok.sum() > 50
    assert np.all(evq["ext_hi"][ok] >= evq["med_hi"][ok])


def test_standardized_residuals_are_normalized_by_sigma_hat():
    """z_t = ln(close_t/close_{t−1}) / σ̂_t（外れ値水準の入力）。"""
    close = np.array([100.0, 102.0, 101.0])
    sigma = np.array([0.01, 0.02, 0.01])
    avail = np.array([True, True, True])
    z = standardized_residuals(close, sigma, avail)
    assert np.isnan(z[0])
    assert z[1] == pytest.approx(np.log(102.0 / 100.0) / 0.02)
    assert z[2] == pytest.approx(np.log(101.0 / 102.0) / 0.01)


def test_unavailable_and_nonfinite_bars_are_nan():
    """available=False・σ̂ が非有限/非正のバーは全系列 nan。"""
    close = np.array([100.0, 100.0, 100.0, 100.0])
    sigma = np.array([0.01, 0.01, np.nan, 0.0])
    avail = np.array([True, False, True, True])
    for arr in cvfe_bands(close, sigma, avail):
        assert np.isnan(arr[0])      # 直前終値が無い
        assert np.isnan(arr[1])      # available=False
        assert np.isnan(arr[2])      # σ̂ が nan
        assert np.isnan(arr[3])      # σ̂ = 0


def test_band_does_not_move_with_current_bar():
    """当該バーの終値を変えてもそのバーのバンドは不変（非リペイント）。

    バンドは close_{t−1} と σ̂_t のみで決まり、σ̂_t も t−1 までの情報で確定している。
    """
    close = np.array([100.0, 101.0, 102.0, 103.0])
    sigma = np.full(4, 0.01)
    avail = np.array([False, True, True, True])
    base = cvfe_bands(close, sigma, avail)

    moved = close.copy()
    moved[2] = 150.0                              # バー 2 の終値を大きく動かす
    after = cvfe_bands(moved, sigma, avail)

    names = ("mid", "u1", "l1", "u2", "l2")
    moved_any = False
    for name, b, a in zip(names, base, after):
        assert b[2] == pytest.approx(a[2]), f"{name}: 当該バーの終値でバンドが動いている"
        if not np.isclose(b[3], a[3]):
            moved_any = True
    assert moved_any, "次バーに一切反映されていない＝検定が空虚"


# --------------------------------------------------------------------------------------
# 表示形式（display_mode）
# --------------------------------------------------------------------------------------

class _FakeChart:
    """`create_line` / `create_level_dash` を記録するダミー（duck typing）。

    実 runtime の FakeChart（api/adapter/compute/fake_chart.py）と同じ 2 メソッドを持つ。
    """

    def __init__(self):
        self.lines = []
        self.dashes = []
        self.rows = {}

    def _series(self, bucket, name):
        rows = self.rows

        class _S:
            def set(self, df):
                rows[name] = df

        bucket.append(name)
        return _S()

    def create_line(self, name, **kwargs):
        return self._series(self.lines, name)

    def create_level_dash(self, name, **kwargs):
        return self._series(self.dashes, name)


def _ohlc_frame(n=700, seed=11):
    import pandas as pd
    rng = np.random.default_rng(seed)
    c = 10_000.0 * np.exp(np.cumsum(rng.standard_normal(n) * 0.004))
    hi = c * (1 + np.abs(rng.standard_normal(n)) * 0.002)
    lo = c * (1 - np.abs(rng.standard_normal(n)) * 0.002)
    op = np.concatenate([[c[0]], c[:-1]])
    t = pd.date_range("2024-01-01", periods=n, freq="1h")
    return pd.DataFrame({"date": t, "open": op, "high": hi, "low": lo, "close": c})


def test_dashes_mode_emits_level_dash_series_only():
    """既定（dashes）は level_dash 系列のみを作り、line 系列を作らない。"""
    from src.lwc_chart import add_cvfe

    chart = _FakeChart()
    add_cvfe(chart, _ohlc_frame(), n_har=500, time_column="date")
    assert chart.lines == [], f"dashes なのに line 系列が出ている: {chart.lines}"
    assert "cvfe_u1" in chart.dashes and "cvfe_l1" in chart.dashes
    assert "cvfe_evq_med_hi" in chart.dashes


def test_dashes_are_per_bar_not_a_single_level():
    """ダッシュは全バー分の水準を持つ（最新 1 点だけの水平線ではない）。"""
    from src.lwc_chart import add_cvfe

    chart = _FakeChart()
    add_cvfe(chart, _ohlc_frame(n=800), n_har=500, time_column="date")
    rows = chart.rows["cvfe_u1"]
    assert len(rows) > 100, f"バー毎になっていない（{len(rows)} 行）"
    # 各行が別の水準を持つ（同一値の水平線を全バーへ引いたのではない）。
    assert rows["cvfe_u1"].nunique() > 50


def test_dashes_levels_are_ordered_outward_per_bar():
    """同一バー上で 1σ < 2σ < 外れ値 の順に外側へ並ぶ。"""
    from src.lwc_chart import add_cvfe

    chart = _FakeChart()
    add_cvfe(chart, _ohlc_frame(n=900, seed=23), n_har=500, time_column="date")
    import pandas as pd
    u1 = chart.rows["cvfe_u1"].set_index("time")["cvfe_u1"]
    u2 = chart.rows["cvfe_u2"].set_index("time")["cvfe_u2"]
    common = u1.index.intersection(u2.index)
    assert len(common) > 50
    assert (u2.loc[common] > u1.loc[common]).all(), "2σ が 1σ の内側にある"


def test_bands_mode_emits_line_series():
    """display_mode='bands' は line 系列を引き、level_dash を作らない。"""
    from src.lwc_chart import add_cvfe

    chart = _FakeChart()
    add_cvfe(chart, _ohlc_frame(), n_har=500, time_column="date", display_mode="bands")
    assert chart.dashes == []
    assert "cvfe_u1" in chart.lines and "cvfe_l1" in chart.lines
    assert "cvfe_evq_med_hi" in chart.lines


def test_toggles_limit_emitted_series():
    """外れ値・外側をオフにすれば 1σ の 2 本だけになる。"""
    from src.lwc_chart import add_cvfe

    chart = _FakeChart()
    add_cvfe(chart, _ohlc_frame(), n_har=500, time_column="date",
             show_outliers=False, show_outer=False)
    assert sorted(chart.dashes) == ["cvfe_l1", "cvfe_u1"], chart.dashes


def test_falls_back_to_create_line_when_level_dash_is_unsupported():
    """create_level_dash を持たない chart では create_line へ落ちる（後方互換）。"""
    from src.lwc_chart import add_cvfe

    class _OldChart:
        """create_line しか持たない旧 duck type。"""

        def __init__(self):
            self.lines = []

        def create_line(self, name, **kwargs):
            self.lines.append(name)
            return type("S", (), {"set": lambda self, df: None})()

    chart = _OldChart()
    add_cvfe(chart, _ohlc_frame(), n_har=500, time_column="date")
    assert "cvfe_u1" in chart.lines and "cvfe_l1" in chart.lines
