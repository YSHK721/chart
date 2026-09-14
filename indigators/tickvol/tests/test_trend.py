"""tickvol の回帰トレンド（btlm_trail 仕様の参照拡張）の検証。

固定する仕様（indigators/tickvol/src/trend.py の docstring と 1:1）:
  - 計算は btlm_trail の公開 API へ委譲する（計算式を写していない＝同じ入力で同じ値）。
  - 既定のバンド方式は経験分位（btlm_trail 本体の ols とは実測により変える）。
  - 因果・非リペイント: 後ろへ延ばしても既存バーの値は変わらない。
  - q_out 無効時は外れ値分位線を出さない（None）。with_metrics=False は β/σ/実績率が None。
  - 分位ペア・band_method の不正は ValueError（btlm_trail と同一規約）。
"""

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from btlm_trail.src.trail import build_btlm_trail, rolling_coverage  # noqa: E402
from src.trend import (  # noqa: E402
    BAND_METHODS,
    DEFAULT_BAND_METHOD,
    DEFAULT_MAXBARS,
    TREND_KEYS,
    tickvol_trend,
)

_KW = {"maxbars": 50, "q_low": 0.10, "q_high": 0.90, "q_out": 0.99,
       "empirical_n": 200, "n_cov": 100}


def _spiky(n=900, seed=5):
    rng = np.random.default_rng(seed)
    base = rng.gamma(shape=2.0, scale=100.0, size=n) + 10.0
    base[::17] *= 3.0
    return np.round(base)


def test_defaults_follow_btlm_trail_except_the_band_method():
    # 回帰窓は btlm_trail 本体の既定をそのまま使う（単一情報源）。
    assert DEFAULT_MAXBARS == 100
    # バンド方式だけ実測により変える（tick 数の乖離率は右に歪み ols の正規仮定が成立しない）。
    assert DEFAULT_BAND_METHOD == "empirical"
    assert BAND_METHODS == ("ols", "empirical")


def test_keys_cover_the_trend_result():
    out = tickvol_trend(_spiky(), **_KW)
    assert set(out) == set(TREND_KEYS)


@pytest.mark.parametrize("method", ["ols", "empirical"])
def test_values_match_the_reference_implementation(method):
    # 計算式を写していないこと＝btlm_trail に同じ系列を渡した結果と完全一致する。
    v = _spiky()
    got = tickvol_trend(v, **{**_KW, "band_method": method})
    ref = build_btlm_trail(
        pd.DataFrame({"open": v, "high": v, "low": v, "close": v}), source="close",
        maxbars=_KW["maxbars"], q_low=_KW["q_low"], q_high=_KW["q_high"],
        band_method=method, empirical_n=_KW["empirical_n"], q_out=_KW["q_out"],
    )
    for key, want in (("mean", ref.mean), ("band_low", ref.band_low),
                      ("band_high", ref.band_high), ("beta", ref.beta),
                      ("sigma", ref.sigma), ("off_low", ref.off_low),
                      ("off_high", ref.off_high)):
        np.testing.assert_array_equal(got[key], want, err_msg=key)
    np.testing.assert_array_equal(
        got["band_hit_rate"],
        rolling_coverage(v, ref.band_low, ref.band_high, _KW["n_cov"]),
    )


def test_trend_is_causal_and_non_repainting():
    v = _spiky(900)
    short = tickvol_trend(v[:600], **_KW)
    long = tickvol_trend(v, **_KW)
    for key in TREND_KEYS:
        a, b = short[key], long[key][:600]
        both = np.isfinite(a) & np.isfinite(b)
        assert np.array_equal(np.isfinite(a), np.isfinite(b)), key
        np.testing.assert_array_equal(a[both], b[both], err_msg=key)


def test_band_encloses_the_trend_line():
    out = tickvol_trend(_spiky(), **_KW)
    ok = np.isfinite(out["band_low"]) & np.isfinite(out["band_high"]) & np.isfinite(out["mean"])
    assert ok.sum() > 100
    assert np.all(out["band_low"][ok] <= out["band_high"][ok])


def test_empirical_band_stays_positive_on_a_count_series():
    # 実測根拠（trend.py ③）: 名目 ols は帯下端が tick 数として成立しない値へ落ちるが
    #   経験分位はほぼ落ちない。計数量に対する既定選択の裏付けをテストでも固定する。
    v = _spiky(2000)
    emp = tickvol_trend(v, **{**_KW, "band_method": "empirical"})
    ols = tickvol_trend(v, **{**_KW, "band_method": "ols"})
    frac = lambda a: float(np.mean(a[np.isfinite(a)] < 1.0))  # noqa: E731
    assert frac(emp["band_low"]) < frac(ols["band_low"])


@pytest.mark.parametrize("bad", [None, 0.5, 0.90, 1.5])
def test_invalid_q_out_disables_the_offset_lines(bad):
    out = tickvol_trend(_spiky(), **{**_KW, "q_out": bad})
    assert out["off_low"] is None and out["off_high"] is None
    assert np.isfinite(out["mean"]).any()          # 本体は残る


def test_with_metrics_false_skips_the_readout_series():
    out = tickvol_trend(_spiky(), **{**_KW, "with_metrics": False})
    for key in ("beta", "sigma", "band_hit_rate"):
        assert out[key] is None, key
    assert np.isfinite(out["mean"]).any()


@pytest.mark.parametrize("kw", [
    {"q_low": 0.9, "q_high": 0.1},      # 分位ペア逆転
    {"band_method": "unknown"},          # 未知の方式
])
def test_invalid_parameters_raise(kw):
    with pytest.raises(ValueError):
        tickvol_trend(_spiky(200), **{**_KW, **kw})
