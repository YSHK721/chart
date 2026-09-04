"""ma_marod core（ma_marod_series）の単体テスト（TDD）。

MA_MAROD = (price - ma) / ma * 100。ma は moving_averages core（4 種バッファ関数）、
ソース写像は moving_averages lwc_chart の _SOURCE_TO_APPLIED、バンドは btlm_trail_marod
core を、それぞれ参照実装としてそのまま再利用する（importlib 動的ロード・無改変）。
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

_PKG_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_PKG_DIR))

from src import (  # noqa: E402
    DEFAULT_EVENT_AGG,
    DEFAULT_K_EVENTS,
    DEFAULT_LENGTH,
    DEFAULT_MA_TYPE,
    DEFAULT_Q_OUT,
    SIGMA_MULT,
    ma_marod_outlier_event_quantiles,
    ma_marod_quantile_bands,
    ma_marod_series,
    ma_marod_sigma_band,
)
from src.core import (  # noqa: E402
    _SOURCE_TO_APPLIED,
    _load_moving_averages,
)

# 参照実装を「テスト側でも独立に」動的ロードし、期待値を組む（期待値算出は完全独立）。
_INDIGATORS = Path(__file__).resolve().parents[2]

_MA_TYPES = ["sma", "ema", "smma", "lwma"]


def _ref_pkg(name: str, modname: str):
    """indigators/<name>/src をパッケージとして独立ロードする（read-only）。"""
    src = _INDIGATORS / name / "src"
    spec = importlib.util.spec_from_file_location(
        modname, src / "__init__.py", submodule_search_locations=[str(src)],
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules[modname] = module
    spec.loader.exec_module(module)
    return module


def _ref_moving_averages_lwc():
    """moving_averages の lwc_chart（_main_ma / _SOURCE_TO_APPLIED）を独立ロードする。"""
    modname = "_moving_averages_ref_expected"
    _ref_pkg("moving_averages", modname)  # 親パッケージ登録（相対 import の解決に必要）
    sub = f"{modname}.lwc_chart"
    if sub not in sys.modules:
        spec = importlib.util.spec_from_file_location(
            sub, _INDIGATORS / "moving_averages" / "src" / "lwc_chart.py",
        )
        module = importlib.util.module_from_spec(spec)
        sys.modules[sub] = module
        spec.loader.exec_module(module)
    return sys.modules[sub]


def _ohlc(n: int = 200, seed: int = 0) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    prices = np.cumsum(rng.normal(0, 1, n)) + 100.0
    return pd.DataFrame({
        "time": pd.date_range("2020-01-01", periods=n, freq="D"),
        "open": prices, "high": prices + 1.0, "low": prices - 1.0, "close": prices + 0.25,
    })


def _expected(df, source="close", ma_type=DEFAULT_MA_TYPE, length=DEFAULT_LENGTH):
    lwc = _ref_moving_averages_lwc()
    kind = lwc._SOURCE_TO_APPLIED[source]
    from common.applied_price import applied_price
    price = applied_price(
        kind,
        df["open"].to_numpy(np.float64), df["high"].to_numpy(np.float64),
        df["low"].to_numpy(np.float64), df["close"].to_numpy(np.float64),
    )
    ma = lwc._main_ma(price, ma_type, length)
    with np.errstate(divide="ignore", invalid="ignore"):
        marod = (price - ma) / ma * 100.0
    return np.where(np.isfinite(marod), marod, np.nan)


def test_load_references_expose_functions():
    # 参照機構（importlib 動的ロード）が MA 参照実装を無改変参照できることの実証。
    #   バンド系は共有プリミティブ common.marod_bands へ委譲（SOLID 是正 🟡-10）。
    mv = _load_moving_averages()
    for fn in ("simple_ma_on_buffer", "exponential_ma_on_buffer",
               "smoothed_ma_on_buffer", "linear_weighted_ma_on_buffer"):
        assert callable(getattr(mv, fn))
    from common import marod_bands as _bands
    assert callable(_bands.quantile_bands)
    assert callable(_bands.sigma_band)


@pytest.mark.parametrize("ma_type", _MA_TYPES)
def test_matches_moving_averages_main_ma_reference(ma_type):
    # 全 4 種別で、参照実装 _main_ma 経路の期待値と定義上一致する。
    df = _ohlc(200)
    got = ma_marod_series(df, source="close", ma_type=ma_type, length=20)
    exp = _expected(df, "close", ma_type, 20)
    np.testing.assert_allclose(got, exp, rtol=1e-12, atol=1e-12, equal_nan=True)


def test_source_mapping_identical_to_moving_averages():
    # 計算の原子（価格ソース写像）が moving_averages と同一（§2.1 の同期を恒久固定）。
    lwc = _ref_moving_averages_lwc()
    assert _SOURCE_TO_APPLIED == lwc._SOURCE_TO_APPLIED


def test_synthetic_source_uses_single_resolved_path():
    # 合成ソース（hl2）でも分子と MA 入力が同一の解決済み配列（単一経路）である。
    df = _ohlc(150, seed=3)
    got = ma_marod_series(df, source="hl2", ma_type="sma", length=10)
    exp = _expected(df, "hl2", "sma", 10)
    np.testing.assert_allclose(got, exp, rtol=1e-12, atol=1e-12, equal_nan=True)


def test_warmup_nan_follows_reference_convention():
    # sma/smma/lwma は先頭 length-1 本 NaN、ema は先頭から有効（_FROM_ZERO 規約）。
    df = _ohlc(120, seed=1)
    for ma_type in ("sma", "smma", "lwma"):
        v = ma_marod_series(df, ma_type=ma_type, length=30)
        assert np.all(np.isnan(v[:29])), ma_type
        assert np.all(np.isfinite(v[29:])), ma_type
    v = ma_marod_series(df, ma_type="ema", length=30)
    assert np.all(np.isfinite(v))  # ema[0]=price[0] → 乖離 0%（有限）


@pytest.mark.parametrize("ma_type", _MA_TYPES)
def test_causal_append_invariance(ma_type):
    # 因果性: 末尾へのデータ追加で既存バーの確定値が変わらない（非リペイント）。
    df = _ohlc(200, seed=7)
    full = ma_marod_series(df, ma_type=ma_type, length=25)
    part = ma_marod_series(df.iloc[:150], ma_type=ma_type, length=25)
    np.testing.assert_allclose(part, full[:150], rtol=1e-12, atol=1e-12, equal_nan=True)


def test_zero_mean_yields_nan_not_inf():
    # 0 除算（ma=0）は inf を残さず NaN に落ちる。
    n = 50
    zeros = np.zeros(n)
    df = pd.DataFrame({
        "time": pd.date_range("2020-01-01", periods=n, freq="D"),
        "open": zeros, "high": zeros, "low": zeros, "close": zeros,
    })
    v = ma_marod_series(df, ma_type="sma", length=5)
    assert not np.isinf(v).any()
    assert np.isnan(v).all()


def test_invalid_inputs_raise_value_error():
    df = _ohlc(50)
    with pytest.raises(ValueError):
        ma_marod_series(df, length=1)          # length < 2
    with pytest.raises(ValueError):
        ma_marod_series(df, ma_type="wma9")    # 未知 ma_type
    with pytest.raises(ValueError):
        ma_marod_series(df, source="median")   # 未知 source


def test_bands_delegate_to_btlm_trail_marod_core():
    # バンドは btlm_trail_marod core への無改変委譲（独立ロードの参照実装と数値一致）。
    ref = _ref_pkg("btlm_trail_marod", "_btlm_trail_marod_ref_expected")
    df = _ohlc(400, seed=11)
    v = ma_marod_series(df, ma_type="sma", length=20)
    got_lo, got_hi = ma_marod_quantile_bands(v, window_n=150, q_low=0.05, q_high=0.95)
    exp_lo, exp_hi = ref.marod_quantile_bands(v, window_n=150, q_low=0.05, q_high=0.95)
    np.testing.assert_allclose(got_lo, exp_lo, rtol=1e-12, atol=1e-12, equal_nan=True)
    np.testing.assert_allclose(got_hi, exp_hi, rtol=1e-12, atol=1e-12, equal_nan=True)
    got = ma_marod_sigma_band(v, window_n=150)
    exp = ref.marod_sigma_band(v, window_n=150)
    for g, e in zip(got, exp):
        np.testing.assert_allclose(g, e, rtol=1e-12, atol=1e-12, equal_nan=True)
    # σ 倍率既定も参照実装と同値（対称性の固定）。
    assert SIGMA_MULT == ref.SIGMA_MULT


# --- 外れ値イベント分位（正常バンド超イベントの因果分位・ユーザー裁定 2026-07-21）-----

def _naive_event_quantiles(v, *, window_n, q_low, q_high, q_out, k, agg="bar"):
    """素朴実装（イベント検出→各バーで過去観測のみから分位）。期待値算出用の独立実装。

    agg="episode" は連続超過を走査し終えたバーで極値 1 点を確定する（runs declustering）。
    """
    lo, hi = ma_marod_quantile_bands(v, window_n=window_n, q_low=q_low, q_high=q_high)
    n = v.size
    keys = ("med_hi", "ext_hi", "med_lo", "ext_lo",
            "med_hi_all", "ext_hi_all", "med_lo_all", "ext_lo_all")
    out = {key: np.full(n, np.nan) for key in keys}
    up, dn, run_up, run_dn = [], [], [], []
    for t in range(n):
        for vals, sfx in ((up[-k:], ""), (up, "_all")):
            if len(vals) >= 5:
                out["med_hi" + sfx][t] = np.median(vals)
                if q_out is not None:
                    out["ext_hi" + sfx][t] = np.quantile(vals, q_out)
        for vals, sfx in ((dn[-k:], ""), (dn, "_all")):
            if len(vals) >= 5:
                out["med_lo" + sfx][t] = np.median(vals)
                if q_out is not None:
                    out["ext_lo" + sfx][t] = np.quantile(vals, 1.0 - q_out)
        is_up = np.isfinite(v[t]) and np.isfinite(hi[t]) and v[t] > hi[t]
        is_dn = (not is_up) and np.isfinite(v[t]) and np.isfinite(lo[t]) and v[t] < lo[t]
        if agg == "bar":
            if is_up:
                up.append(float(v[t]))
            elif is_dn:
                dn.append(float(v[t]))
        else:
            if not is_up and run_up:
                up.append(max(run_up)); run_up = []
            if not is_dn and run_dn:
                dn.append(min(run_dn)); run_dn = []
            if is_up:
                run_up.append(float(v[t]))
            elif is_dn:
                run_dn.append(float(v[t]))
    return out


@pytest.mark.parametrize("agg", ["bar", "episode"])
def test_event_quantiles_match_naive_reference(agg):
    # 実装は素朴独立実装と全キー数値一致（両集計単位・イベント定義・因果境界・K・全履歴）。
    df = _ohlc(500, seed=11)
    v = ma_marod_series(df, ma_type="sma", length=20)
    got = ma_marod_outlier_event_quantiles(
        v, window_n=100, q_low=0.05, q_high=0.95, q_out=0.97, k_events=10, event_agg=agg,
    )
    exp = _naive_event_quantiles(
        v, window_n=100, q_low=0.05, q_high=0.95, q_out=0.97, k=10, agg=agg,
    )
    assert set(got) == set(exp)
    for key in exp:
        np.testing.assert_allclose(got[key], exp[key], rtol=1e-12, atol=1e-12, equal_nan=True)


def test_event_quantiles_episode_fewer_observations_than_bar():
    # エピソード集計は観測数がバー集計以下（declustering の帰結＝独立標本への圧縮）。
    #   有限水準の出現本数（med_hi_all が非 NaN になる時点）はエピソードの方が遅いか同じ。
    df = _ohlc(500, seed=11)
    v = ma_marod_series(df, ma_type="sma", length=20)
    bar = ma_marod_outlier_event_quantiles(v, window_n=100, k_events=10, event_agg="bar")
    ep = ma_marod_outlier_event_quantiles(v, window_n=100, k_events=10, event_agg="episode")
    first_bar = np.argmax(np.isfinite(bar["med_hi_all"]))
    first_ep = np.argmax(np.isfinite(ep["med_hi_all"]))
    assert np.isfinite(ep["med_hi_all"]).any()
    assert first_ep >= first_bar


@pytest.mark.parametrize("agg", ["bar", "episode"])
def test_event_quantiles_causal_append_invariance(agg):
    # 因果性: データ末尾に 1 本追加しても既存バーの水準は不変（非リペイント・両集計単位）。
    #   episode は進行中エピソードを未確定として破棄するため、末尾追加（エピソード確定の
    #   前倒し）でも過去バーの水準は変わらない。
    df = _ohlc(400, seed=7)
    v_full = ma_marod_series(df, ma_type="sma", length=20)
    a = ma_marod_outlier_event_quantiles(v_full[:-1], window_n=100, k_events=10, event_agg=agg)
    b = ma_marod_outlier_event_quantiles(v_full, window_n=100, k_events=10, event_agg=agg)
    for key in a:
        np.testing.assert_allclose(a[key], b[key][:-1], rtol=1e-12, atol=1e-12, equal_nan=True)


def test_event_quantiles_ext_nan_when_q_out_invalid():
    # q_out 無効（None / q_out<=q_high / q_out<=0.5）は極端線（ext_*）のみ全 NaN・中央値は生きる。
    df = _ohlc(500, seed=3)
    v = ma_marod_series(df, ma_type="sma", length=20)
    for q_out in (None, 0.90, 0.4):
        r = ma_marod_outlier_event_quantiles(v, window_n=100, q_high=0.95, q_out=q_out, k_events=10)
        for key in ("ext_hi", "ext_lo", "ext_hi_all", "ext_lo_all"):
            assert np.isnan(r[key]).all(), f"{key} は q_out={q_out} で全 NaN のはず"
        assert np.isfinite(r["med_hi_all"]).any()  # 中央値は無効化されない


def test_event_quantiles_defaults_and_validation():
    # 既定（ユーザー裁定 2026-07-21）: q_out=0.99・k_events=50・event_agg=episode
    #   （bar は旧方式として切替可能に保持）。k_events<1・event_agg 不正は ValueError。
    assert DEFAULT_Q_OUT == 0.99
    assert DEFAULT_K_EVENTS == 50
    assert DEFAULT_EVENT_AGG == "episode"
    df = _ohlc(100, seed=1)
    v = ma_marod_series(df, ma_type="sma", length=20)
    with pytest.raises(ValueError):
        ma_marod_outlier_event_quantiles(v, k_events=0)
    with pytest.raises(ValueError):
        ma_marod_outlier_event_quantiles(v, event_agg="run")
