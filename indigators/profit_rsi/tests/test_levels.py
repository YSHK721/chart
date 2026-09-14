"""profit_rsi levels 層（正常帯・POT/GPD 外れ値水準）の検証。

水準の定義そのものを固定する。手続き（因果ローリング分位 → 超過エピソード → 経験的分位 /
GPD 外挿）は共有プリミティブへ委譲しているため、ここで固定するのは **本指標に固有の 3 点**:

  1. **余地割合スケール**: 超過を ``(RSI − u)/(100 − u)``（下側は ``(u − RSI)/u``）で測る。
     RSI が [0,100] の有界量であることに由来し、水準が境界を出ないことを保証する。
  2. **因果性（非リペイント）**: 水準は当該バーを含まない情報だけで決まる。未来のバーを
     足しても既に描いた水準は変わらない。
  3. **上下対称**: 上側・下側とも同じ手続き・同じ q_out（観測が「超過の大きさ」だから）。

共有側の性質（GPD 当てはめ・分位・イベント畳み込み）は ``common/tests`` が固定済みで、
ここでは再検証しない。
"""

import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.levels import (  # noqa: E402
    BAND_KEYS,
    LEVEL_KEYS,
    RSI_MAX,
    causal_bands,
    excess_fraction,
    headroom,
    levels_at,
    levels_latest,
    rsi_levels,
)


#: GPD 側の水準が出るには直近窓に MIN_GPD_EVENTS(=30) 件以上の観測が要る（共有規約）。
#: そのため k_events は 30 以上で試験する（30 未満で NaN になることは TC-45 が固定する）。
_K_EVENTS = 40


def _rsi_series(n=1500, seed=3):
    """[0,100] に収まる合成 RSI 系列（両側に十分な数の超過エピソードが出る振幅・周期）。"""
    rng = np.random.default_rng(seed)
    x = 50.0 + 28.0 * np.sin(np.arange(n) * 0.35) + rng.normal(0, 6.0, n)
    return np.clip(x, 0.0, 100.0)


# ---------------------------------------------------------------------------
# TC-40 余地割合: 閾値ちょうどで 0、境界（100 / 0）ちょうどで 1
# ---------------------------------------------------------------------------
def test_excess_fraction_is_zero_at_threshold_and_one_at_bound():
    values = np.array([80.0, 90.0, 100.0])
    u = np.array([80.0, 80.0, 80.0])

    frac = excess_fraction(values, u, upper=True)

    assert frac[0] == pytest.approx(0.0)
    assert frac[1] == pytest.approx(0.5)      # 余地 20 のうち 10
    assert frac[2] == pytest.approx(1.0)      # 境界ちょうど＝余地を使い切る


def test_excess_fraction_lower_side_is_symmetric():
    values = np.array([20.0, 10.0, 0.0])
    u = np.array([20.0, 20.0, 20.0])

    frac = excess_fraction(values, u, upper=False)

    assert frac.tolist() == pytest.approx([0.0, 0.5, 1.0])


def test_headroom_is_nan_when_threshold_touches_the_bound():
    # 余地 0（u=100 の上側 / u=0 の下側）は割り算が定義できない＝イベント判定外。
    assert np.isnan(headroom(np.array([RSI_MAX]), upper=True)[0])
    assert np.isnan(headroom(np.array([0.0]), upper=False)[0])


# ---------------------------------------------------------------------------
# TC-41 水準は [0,100] を出ない（有界量への適用の核心・生スケールでは 26〜35% 出た）
# ---------------------------------------------------------------------------
def test_levels_never_leave_rsi_bounds():
    out = rsi_levels(_rsi_series(), window_n=50, k_events=_K_EVENTS)

    for key in (*BAND_KEYS, *LEVEL_KEYS):
        v = out[key]
        finite = v[np.isfinite(v)]
        assert finite.size > 0, key
        assert finite.min() >= 0.0, key
        assert finite.max() <= RSI_MAX, key


# ---------------------------------------------------------------------------
# TC-42 水準の順序: 外れ値(下) <= 正常帯(下) < 正常帯(上) <= 外れ値(上)
# ---------------------------------------------------------------------------
def test_levels_are_ordered_outward_from_the_bands():
    out = rsi_levels(_rsi_series(), window_n=50, k_events=_K_EVENTS)

    def _both(a, b):
        m = np.isfinite(out[a]) & np.isfinite(out[b])
        return out[a][m], out[b][m]

    lo_ext, band_lo = _both("ext_lo", "band_low")
    assert np.all(lo_ext <= band_lo + 1e-9)
    gpd_lo, band_lo2 = _both("gpd_lo", "band_low")
    assert np.all(gpd_lo <= band_lo2 + 1e-9)
    band_hi, hi_ext = _both("band_high", "ext_hi")
    assert np.all(band_hi <= hi_ext + 1e-9)
    band_hi2, gpd_hi = _both("band_high", "gpd_hi")
    assert np.all(band_hi2 <= gpd_hi + 1e-9)


# ---------------------------------------------------------------------------
# TC-43 因果性: 後続バーを足しても既存バーの水準は 1 点も変わらない（非リペイント）
# ---------------------------------------------------------------------------
def test_levels_do_not_repaint_when_future_bars_arrive():
    values = _rsi_series()
    cut = 1000

    partial = rsi_levels(values[:cut], window_n=50, k_events=_K_EVENTS)
    full = rsi_levels(values, window_n=50, k_events=_K_EVENTS)

    for key in (*BAND_KEYS, *LEVEL_KEYS):
        np.testing.assert_allclose(
            partial[key], full[key][:cut], rtol=0, atol=0, err_msg=key
        )


# ---------------------------------------------------------------------------
# TC-44 levels_latest（増分入口）は rsi_levels の次バー値と一致する
# ---------------------------------------------------------------------------
def test_levels_latest_matches_batch_next_bar():
    values = _rsi_series()
    k, q_out, window_n = _K_EVENTS, 0.99, 50
    batch = rsi_levels(values, window_n=window_n, k_events=k, q_out=q_out)

    # 全バー走査で確定した観測列を作り、次バーへ与える水準を求める（増分の入口）。
    from src.levels import excess_fraction as _frac
    from src.levels import step_excess_event as _step

    events = {"hi": [], "lo": []}
    runs = {"hi": [], "lo": []}
    for side, upper, band in (("hi", True, "band_high"), ("lo", False, "band_low")):
        frac = _frac(values, batch[band], upper=upper)
        for x in frac:
            _step(x, events[side], runs[side])

    latest = levels_latest(events["hi"], events["lo"], q_out=q_out, k_events=k)

    # 余地割合スケールの水準を最終バーの閾値・余地で RSI スケールへ戻すと、
    # バッチ最終バーの値と一致する（最終バーの水準＝その直前までの観測で決まるため）。
    head_hi = RSI_MAX - batch["band_high"][-1]
    got = batch["band_high"][-1] + latest["ext_hi"] * head_hi
    assert got == pytest.approx(batch["ext_hi"][-1], rel=1e-12) or np.isnan(got)


# ---------------------------------------------------------------------------
# TC-45 観測不足では水準を出さない（GPD は 30 件・経験的は共有規約の下限）
# ---------------------------------------------------------------------------
def test_levels_are_nan_until_enough_events():
    lv = levels_at([0.2, 0.5, 0.7], 3, k_events=50, q_out=0.99)
    assert np.isnan(lv["gpd"])          # GPD は MIN_GPD_EVENTS(30) 未満で NaN

    many = list(np.linspace(0.05, 0.95, 40))
    lv2 = levels_at(many, len(many), k_events=50, q_out=0.99)
    assert np.isfinite(lv2["gpd"])
    assert lv2["gpd"] <= 1.0            # 余地割合の台を超えない


# ---------------------------------------------------------------------------
# TC-46 q_out 無効（共有規約 q_out_valid）で外れ値水準はオフ・正常帯は残る
# ---------------------------------------------------------------------------
def test_invalid_q_out_disables_outlier_levels_only():
    out = rsi_levels(_rsi_series(), window_n=50, k_events=_K_EVENTS, q_out=0.5)  # q_high 以下＝無効

    for key in LEVEL_KEYS:
        assert np.all(np.isnan(out[key])), key
    for key in BAND_KEYS:
        assert np.isfinite(out[key]).any(), key


# ---------------------------------------------------------------------------
# TC-47 正常帯は当該バー除外の因果ローリング分位（共有実装と同一）
# ---------------------------------------------------------------------------
def test_bands_delegate_to_shared_causal_quantile():
    values = _rsi_series(400)
    low, high = causal_bands(values, window_n=30, q_low=0.1, q_high=0.9)
    out = rsi_levels(values, window_n=30, q_low=0.1, q_high=0.9, k_events=_K_EVENTS)

    np.testing.assert_array_equal(out["band_low"], low)
    np.testing.assert_array_equal(out["band_high"], high)


# ---------------------------------------------------------------------------
# TC-48 パラメータ不正は ValueError（共有 validate_window_qpair と同規約）
# ---------------------------------------------------------------------------
def test_invalid_parameters_raise():
    values = _rsi_series(100)
    with pytest.raises(ValueError):
        rsi_levels(values, k_events=0)
    with pytest.raises(ValueError):
        rsi_levels(values, window_n=1)
    with pytest.raises(ValueError):
        rsi_levels(values, q_low=0.9, q_high=0.1)


# ---------------------------------------------------------------------------
# TC-49 空入力でも落ちない（長さ 0 の配列を返す）
# ---------------------------------------------------------------------------
def test_empty_input_returns_empty_arrays():
    out = rsi_levels(np.array([]), window_n=50, k_events=_K_EVENTS)
    for key in (*BAND_KEYS, *LEVEL_KEYS):
        assert out[key].size == 0, key
