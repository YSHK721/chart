"""層名: core 層テスト（因果窓 W + 比率正規化）。

責務:
    PRO!fit_HLBand の core 層に追加する「因果窓（直近 W 本）+ 比率正規化
    （価格水準依存・look-ahead 是正）」の振る舞いを固定する。忠実移植は放棄済みだが
    後方互換モード（window=None, normalize=False）は旧 compute_hl_band と bit 一致で
    固定点として残す。

    本ファイルの各テストは discriminating input を用い、「比率正規化が per-bar の
    |X-C|/C を平均する（絶対距離の集約を close_ref で割るのではない）」「窓が末尾 W 本の
    スライスである（全長統計でも per-bar でもない）」「close_ref=close[-2] が窓に
    依らず維持される」を区別する。

依存: 標準 sys/pathlib / 外部 numpy, pytest / プロジェクト内 src.core
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.core import (  # noqa: E402
    HL_BAND_DEVS,
    band_upper,
    compute_distances,
    compute_hl_band,
)

# === discriminating dataset（価格水準が bar 間で変動。per-bar 正規化と
#     集約後 close_ref 除算を区別する） ===
# close[-2]=12.0（窓に依らず close_ref として維持されるか区別）。
_HIGH = np.array([10.0, 20.0, 11.0, 13.0, 15.0])
_LOW = np.array([8.0, 9.0, 9.5, 10.0, 11.0])
_CLOSE = np.array([9.0, 18.0, 10.5, 12.0, 14.0])
_CLOSE_REF = 12.0  # close[-2]

_SUFFIX = ("067", "165", "196", "258")


def _expected_levels_full_ratio(high, low, close):
    """window=None, normalize=True の手計算 levels（per-bar 比率・全長）。"""
    high = np.asarray(high, float)
    low = np.asarray(low, float)
    close = np.asarray(close, float)
    cref = float(close[-2])
    r_high = np.abs(high - close) / close
    r_low = np.abs(low - close) / close
    lv = {}
    for dev, suf in zip(HL_BAND_DEVS, _SUFFIX):
        lv[f"up_{suf}"] = cref * (1.0 + band_upper(r_high, dev))
        lv[f"dn_{suf}"] = cref * (1.0 - band_upper(r_low, dev))
    return lv


# --- TC-1: 比率正規化は per-bar（集約後 close_ref 除算ではない） ---
def test_ratio_mode_normalizes_per_bar_not_aggregate_over_close_ref():
    # Arrange（価格水準が bar 間で変動 → per-bar 比率 != 集約距離/close_ref）
    cref = _CLOSE_REF
    # per-bar 比率の band_upper（正しい比率モードの相対オフセット）
    r_high = np.abs(_HIGH - _CLOSE) / _CLOSE
    expected_rel = band_upper(r_high, 1.65)
    # 絶対距離を集約して close_ref で割った相対オフセット（誤実装で出る値）
    wrong_rel = band_upper(np.abs(_HIGH - _CLOSE), 1.65) / cref
    # Act
    result = compute_hl_band(_HIGH, _LOW, _CLOSE, window=None, normalize=True)
    actual_rel = (result.levels["up_165"] - cref) / cref
    # Assert（per-bar 正規化を採用していること。誤実装値とは一致しない）
    assert actual_rel == pytest.approx(expected_rel, abs=1e-12)
    assert actual_rel != pytest.approx(wrong_rel, abs=1e-12)


def test_ratio_mode_relative_offset_is_scale_invariant():
    # Arrange（全価格 k 倍。比率モードの相対オフセットは不変）
    k = 2.7
    base = compute_hl_band(_HIGH, _LOW, _CLOSE, window=None, normalize=True)
    scaled = compute_hl_band(k * _HIGH, k * _LOW, k * _CLOSE, window=None, normalize=True)
    # Act
    base_rel = (base.levels["up_165"] - base.close_ref) / base.close_ref
    scaled_rel = (scaled.levels["up_165"] - scaled.close_ref) / scaled.close_ref
    # Assert
    assert scaled_rel == pytest.approx(base_rel, abs=1e-12)


# --- TC-2: 履歴長非依存（window=120・末尾 W 本同一なら levels 不変） ---
def test_window_levels_depend_only_on_tail_w_bars():
    # Arrange（tail 120 本同一、先頭に古バーを 20 本前置）
    rng = np.random.default_rng(0)
    n = 120
    close = 100.0 + np.cumsum(rng.normal(0.0, 1.0, n))
    high = close + np.abs(rng.normal(2.0, 0.5, n))
    low = close - np.abs(rng.normal(2.0, 0.5, n))
    old = 20
    o_close = 50.0 + np.cumsum(rng.normal(0.0, 3.0, old))
    o_high = o_close + np.abs(rng.normal(5.0, 1.0, old))
    o_low = o_close - np.abs(rng.normal(5.0, 1.0, old))
    ext_high = np.concatenate([o_high, high])
    ext_low = np.concatenate([o_low, low])
    ext_close = np.concatenate([o_close, close])
    # Act（tail 120 のみ vs 古バー前置 140 本。どちらも window=120）
    base = compute_hl_band(high, low, close, window=120, normalize=True)
    ext = compute_hl_band(ext_high, ext_low, ext_close, window=120, normalize=True)
    # Assert（close[-2] 同一かつ tail 120 同一 → levels 不変。全長統計なら fail）
    assert ext.close_ref == pytest.approx(base.close_ref, abs=1e-12)
    for suf in _SUFFIX:
        assert ext.levels[f"up_{suf}"] == pytest.approx(base.levels[f"up_{suf}"], abs=1e-9)
        assert ext.levels[f"dn_{suf}"] == pytest.approx(base.levels[f"dn_{suf}"], abs=1e-9)


# --- TC-3: 後方互換（window=None, normalize=False で旧挙動と bit 一致） ---
def test_backward_compat_window_none_normalize_false_matches_legacy():
    # Arrange（旧 test_core の固定期待値: close_ref=12.0, dn_165=9.08,
    #          band_upper(dist_low,1.65)=2.92, dist=|X-C| 絶対距離）
    legacy_high = np.array([10.0, 12.0, 11.0, 13.0, 15.0])
    legacy_low = np.array([8.0, 9.0, 9.5, 10.0, 11.0])
    legacy_close = np.array([9.0, 10.0, 10.5, 12.0, 14.0])
    exp_dist_high = np.array([1.0, 2.0, 0.5, 1.0, 1.0])
    exp_dist_low = np.array([1.0, 1.0, 1.0, 2.0, 3.0])
    # Act
    result = compute_hl_band(
        legacy_high, legacy_low, legacy_close, window=None, normalize=False
    )
    # Assert（絶対距離投影・加減算。旧 core.py:146-147 と bit 一致）
    assert result.close_ref == pytest.approx(12.0, abs=1e-12)
    assert result.levels["dn_165"] == pytest.approx(9.08, abs=1e-12)
    assert result.levels["up_165"] == pytest.approx(
        12.0 + band_upper(exp_dist_high, 1.65), abs=1e-12
    )
    for suf, dev in zip(_SUFFIX, HL_BAND_DEVS):
        assert result.levels[f"up_{suf}"] == pytest.approx(
            12.0 + band_upper(exp_dist_high, dev), abs=1e-12
        )
        assert result.levels[f"dn_{suf}"] == pytest.approx(
            12.0 - band_upper(exp_dist_low, dev), abs=1e-12
        )
    assert result.available is True


# --- TC-4: データ不足（有効本数 < 2 → available=False・levels NaN） ---
def test_insufficient_effective_bars_sets_available_false_and_nan_levels():
    # Arrange（N=5 だが window=1 → 有効本数 1 < 2）
    # Act
    result = compute_hl_band(_HIGH, _LOW, _CLOSE, window=1, normalize=True)
    # Assert
    assert result.available is False
    for suf in _SUFFIX:
        assert np.isnan(result.levels[f"up_{suf}"])
        assert np.isnan(result.levels[f"dn_{suf}"])


# --- TC-5: 0 除算ガード（比率モードで close に 0 → ValueError） ---
def test_ratio_mode_raises_value_error_when_close_contains_zero():
    # Arrange（close に 0 を含む）
    high = np.array([10.0, 20.0, 11.0, 13.0, 15.0])
    low = np.array([8.0, 9.0, 9.5, 10.0, 11.0])
    close = np.array([9.0, 0.0, 10.5, 12.0, 14.0])
    # Act / Assert
    with pytest.raises(ValueError):
        compute_hl_band(high, low, close, window=None, normalize=True)


def test_ratio_mode_raises_value_error_when_close_is_negative():
    # Arrange（close<=0 ガード: 負値も対象）
    high = np.array([10.0, 20.0, 11.0, 13.0, 15.0])
    low = np.array([8.0, 9.0, 9.5, 10.0, 11.0])
    close = np.array([9.0, -5.0, 10.5, 12.0, 14.0])
    # Act / Assert
    with pytest.raises(ValueError):
        compute_hl_band(high, low, close, window=None, normalize=True)


# --- TC-6: 窓スライス（window=3・末尾 3 本以外を変えても levels 不変） ---
def test_window_slice_ignores_bars_outside_tail_w():
    # Arrange（window=3。tail 3 = index 2,3,4。index 0 を変えても levels 不変）
    base = compute_hl_band(_HIGH, _LOW, _CLOSE, window=3, normalize=True)
    high2 = _HIGH.copy()
    high2[0] = 999.0
    low2 = _LOW.copy()
    low2[0] = -50.0
    # Act（tail 3 本以外を変更）
    mod = compute_hl_band(high2, low2, _CLOSE, window=3, normalize=True)
    # Assert（末尾 3 本のみ参照 → levels 不変。全長 per-bar 誤実装なら fail）
    assert mod.close_ref == pytest.approx(base.close_ref, abs=1e-12)
    for suf in _SUFFIX:
        assert mod.levels[f"up_{suf}"] == pytest.approx(base.levels[f"up_{suf}"], abs=1e-12)
        assert mod.levels[f"dn_{suf}"] == pytest.approx(base.levels[f"dn_{suf}"], abs=1e-12)


# --- compute_ratios 直接（per-bar 比率・0 除算ガード） ---
def test_compute_ratios_returns_per_bar_abs_ratio():
    from src.core import compute_ratios

    high = np.array([10.0, 20.0])
    low = np.array([8.0, 9.0])
    close = np.array([9.0, 18.0])
    # Act
    r_high, r_low = compute_ratios(high, low, close)
    # Assert（|H-C|/C, |L-C|/C を per-bar）
    np.testing.assert_allclose(r_high, [abs(10 - 9) / 9, abs(20 - 18) / 18])
    np.testing.assert_allclose(r_low, [abs(8 - 9) / 9, abs(9 - 18) / 18])


def test_compute_ratios_raises_when_close_contains_zero():
    from src.core import compute_ratios

    high = np.array([10.0, 11.0])
    low = np.array([8.0, 9.0])
    close = np.array([9.0, 0.0])
    # Act / Assert
    with pytest.raises(ValueError):
        compute_ratios(high, low, close)


# --- DEFAULT_WINDOW 既定値 ---
def test_default_window_is_120():
    from src.core import DEFAULT_WINDOW

    assert DEFAULT_WINDOW == 120


def test_compute_hl_band_default_window_is_120_and_normalize_true():
    # Arrange（既定引数 = window=120, normalize=True。N=5<120 → 全長 tail だが
    #          eff=min(120,5)=5>=2 → available True かつ比率モード）
    # Act
    result = compute_hl_band(_HIGH, _LOW, _CLOSE)
    expected = _expected_levels_full_ratio(_HIGH, _LOW, _CLOSE)
    # Assert（既定が比率モード・全長 tail）
    assert result.available is True
    for suf in _SUFFIX:
        assert result.levels[f"up_{suf}"] == pytest.approx(expected[f"up_{suf}"], abs=1e-12)
        assert result.levels[f"dn_{suf}"] == pytest.approx(expected[f"dn_{suf}"], abs=1e-12)


# === window 境界（🟡-1 規約 / 🟡-3 境界テスト欠落補完） ===
# 規約: window は「直近 W 本」の窓長であり window>=1 のみ有効。window<1（0・負）は
# 窓として無意味（_tail(s,0)=s[-0:]=全長、_tail(s,-3)=s[3:] と実スライス長が乖離し
# available の真実源が崩れる）ため ValueError とする。available は実際に統計に使う
# スライス長 len(slice) を単一の真実源として判定する。


# --- TC-7a: window=0 → ValueError（窓長 0 は無意味・実スライス乖離の根本遮断） ---
def test_window_zero_raises_value_error():
    # Arrange / Act / Assert（window=0 は不正な窓長 → ValueError）
    with pytest.raises(ValueError):
        compute_hl_band(_HIGH, _LOW, _CLOSE, window=0, normalize=True)


# --- TC-7b: window=-1（負）→ ValueError ---
def test_window_negative_raises_value_error():
    # Arrange / Act / Assert（負の窓長 → ValueError。normalize に依らず入口で拒否）
    with pytest.raises(ValueError):
        compute_hl_band(_HIGH, _LOW, _CLOSE, window=-1, normalize=True)
    with pytest.raises(ValueError):
        compute_hl_band(_HIGH, _LOW, _CLOSE, window=-1, normalize=False)


# --- TC-7c: window=None & n<2 → ValueError（close[-2] 不在の入口ガード） ---
def test_window_none_with_n_below_two_raises_value_error():
    # Arrange（N=1 → close[-2] 不在）
    single_high = np.array([10.0])
    single_low = np.array([8.0])
    single_close = np.array([9.0])
    # Act / Assert
    with pytest.raises(ValueError):
        compute_hl_band(single_high, single_low, single_close, window=None, normalize=True)


# --- TC-7d: window=1 → available=False・NaN levels（有効本数 1<2。退化 false-green でない） ---
def test_window_one_is_valid_but_available_false_with_nan_levels():
    # Arrange（window=1 は有効な窓長だが eff=1<MIN_EFFECTIVE_BARS → 帯潰れ）
    # Act
    result = compute_hl_band(_HIGH, _LOW, _CLOSE, window=1, normalize=True)
    # Assert（ValueError ではなく available=False で NaN を返す。window>=1 は正常系）
    assert result.available is False
    for suf in _SUFFIX:
        assert np.isnan(result.levels[f"up_{suf}"])
        assert np.isnan(result.levels[f"dn_{suf}"])


# --- TC-7e: available の真実源は実スライス長 len(slice)（min(window,n) ではない） ---
def test_available_truth_source_is_actual_slice_length():
    # Arrange（window=2・n=5。tail 2 本 = index 3,4。available は実スライス長 2>=2 で True。
    #          かつ levels は tail 2 本のみで算出される＝index 0..2 を変えても不変）
    base = compute_hl_band(_HIGH, _LOW, _CLOSE, window=2, normalize=True)
    high2 = _HIGH.copy()
    high2[0] = 999.0
    high2[1] = -777.0
    high2[2] = 555.0
    low2 = _LOW.copy()
    low2[0] = -50.0
    low2[1] = 333.0
    low2[2] = -99.0
    # Act（tail 2 本以外を破壊的に変更）
    mod = compute_hl_band(high2, low2, _CLOSE, window=2, normalize=True)
    # Assert（available=True かつ tail 2 本のみ参照 → levels 不変）
    assert base.available is True
    assert mod.available is True
    assert mod.close_ref == pytest.approx(base.close_ref, abs=1e-12)
    for suf in _SUFFIX:
        assert mod.levels[f"up_{suf}"] == pytest.approx(base.levels[f"up_{suf}"], abs=1e-12)
        assert mod.levels[f"dn_{suf}"] == pytest.approx(base.levels[f"dn_{suf}"], abs=1e-12)
