"""SOLID W2 回帰ガード（ISSUE-097 🟡-6/🟡-7・ISSUE-098 🟡-5）。

OCP/LSP 是正の宣言一元化が従来挙動と byte 一致で成立することを固定する回帰ガード:
  🟡-6: latest_meta の per-indicator if 連鎖を _BindingSpec の archetype 宣言へ移設。
  🟡-7: call_binding.invoke の price_range_power 特別扱いを _BindingSpec の preprocess へ昇格。
  🟡-5: indicator_compute_adapter の profit_band 例外翻訳を専用境界1箇所へ隔離。

すべて「移設・隔離後も従来と同一結果」を検証する（新規挙動は追加しない）。
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from adapter.compute import call_binding
from adapter.compute import indicator_compute_adapter as ica
from adapter.compute.call_binding import _TABLE
from adapter.compute.latest_meta import latest_meta


# =========================================================================== #
# 🟡-6: latest_meta の archetype 宣言が _BindingSpec へ移設され従来と同一結果
# =========================================================================== #

# 移設前（if 連鎖）の golden。params={} 既定での (archetype, min_window, trailing_k)。
_LATEST_GOLDEN_EMPTY_PARAMS = {
    # ISSUE-233: moving_averages は増分計算へ移行（archetype=incremental）。min_window/K は不変。
    ("moving_averages", "default"): ("incremental", None, 1),
    # ISSUE-233 S2/S3/S4/S5: btlm_trail・MAROD 系も増分計算へ移行（min_window/K は不変）。
    ("btlm_trail", "default"): ("incremental", None, 1),
    ("btlm_trail_marod", "default"): ("incremental", None, 1),
    ("ma_marod", "default"): ("incremental", None, 1),
    ("price_range_power", "default"): ("axis_distribution", None, None),
}


@pytest.mark.parametrize("key", list(_TABLE.keys()))
def test_latest_meta_matches_golden_for_all_registered_indicators(key):
    # 全登録指標で latest_meta(params={}) が移設前 golden と byte 一致。
    compute_id, variant = key
    expected = _LATEST_GOLDEN_EMPTY_PARAMS.get(key, ("recurrence", None, 1))
    meta = latest_meta(compute_id, variant, {})
    assert (meta.archetype, meta.min_window, meta.trailing_k) == expected


def test_latest_meta_archetype_declared_in_binding_spec():
    # 移設の実証: archetype 解決子は _BindingSpec 側に宣言される（if 連鎖の撤去）。
    assert _TABLE[("price_range_power", "default")].get("latest_meta") is not None
    assert _TABLE[("moving_averages", "default")].get("latest_meta") is not None
    assert _TABLE[("btlm_trail", "default")].get("latest_meta") is not None
    # 未宣言指標は field を持たない（安全既定 recurrence/full/K=1 へ落ちる）。
    assert _TABLE[("profit_band", "global")].get("latest_meta") is None
    assert _TABLE[("tgp_btlm", "default")].get("latest_meta") is None


def test_latest_meta_moving_averages_declares_incrementer_for_all_ma_types():
    # ISSUE-233: 全 ma_type（未知含む）で増分計算を宣言する。適用可否の判定は増分器 prepare が
    # 持ち（未知 ma_type は扱えない＝従来経路へ落ちる）、宣言側は params で分岐しない。
    for ma in ("sma", "lwma", "ema", "smma", "unknown"):
        meta = latest_meta("moving_averages", "default", {"ma_type": ma})
        assert (meta.archetype, meta.min_window, meta.trailing_k) == ("incremental", None, 1)
        assert meta.incremental == "moving_averages"


def test_latest_meta_unregistered_falls_to_safe_default():
    meta = latest_meta("does_not_exist", "default", {})
    assert (meta.archetype, meta.min_window, meta.trailing_k) == ("recurrence", None, 1)


# =========================================================================== #
# 🟡-7: price_range_power の interval 前処理が _BindingSpec の preprocess へ昇格
# =========================================================================== #
def test_prp_preprocess_hook_declared_and_others_absent():
    # preprocess フックは price_range_power のみに宣言され、invoke は compute_id 直判定を持たない。
    assert _TABLE[("price_range_power", "default")].get("preprocess") is not None
    for key, spec in _TABLE.items():
        if key != ("price_range_power", "default"):
            assert spec.get("preprocess") is None, key


def _high_price_df(n: int = 80) -> pd.DataFrame:
    base = 40000.0 + np.sin(np.linspace(0.0, 6.0 * np.pi, n)) * 3000.0
    return pd.DataFrame(
        {
            "open": base,
            "high": base + 1000.0,
            "low": base - 1000.0,
            "close": base + 500.0,
            "volume": np.full(n, 1000.0),
            "time": pd.date_range("2024-01-01", periods=n, freq="h"),
        }
    )


def test_prp_preprocess_adapts_interval_on_band_explosion():
    # 高価格帯で既定 interval だとバンド爆発。preprocess フックが粗刻みへ適応（従来同一）。
    df = _high_price_df()
    spec = _TABLE[("price_range_power", "default")]
    kw = {"interval": 0.1, "top_n": 3}
    out = spec["preprocess"](df, dict(kw))
    assert out["interval"] > 0.1
    # 適応値は従来の _adapt_prp_interval と byte 一致（ロジック保存）。
    assert out["interval"] == call_binding._adapt_prp_interval(df, dict(kw))


def test_prp_preprocess_untouched_when_no_interval_key():
    # interval 無しは触らない（従来の "interval" in kw ガード保存）。
    df = _high_price_df()
    spec = _TABLE[("price_range_power", "default")]
    out = spec["preprocess"](df, {"top_n": 3})
    assert "interval" not in out


def test_prp_invoke_still_adapts_interval_end_to_end():
    # invoke 経由でも従来どおり interval 適応が効く（compute_id 直判定撤去後も同一挙動）。
    from adapter.compute import FakeHorizontalChart

    df = _high_price_df()
    binding = call_binding.CallBinding.resolve("price_range_power", "default")
    chart = FakeHorizontalChart()
    # 爆発する interval を渡しても完走する（適応が効いた＝ハングしない）。
    binding.invoke(chart, df, {"interval": 0.1, "top_n": 3})
    assert binding.output_kind == "horizontal_line"


# =========================================================================== #
# 🟡-5 / LSP 是正 LSP-3: profit_band 例外翻訳境界の隔離（型 EmptyBucketError で識別・1箇所へ）
# =========================================================================== #
def test_profit_band_value_error_translator_registered():
    # profit_band 専用翻訳境界が registry に1箇所登録される。
    assert "profit_band" in ica._VALUE_ERROR_TRANSLATORS


def test_empty_series_indicator_set_removed_from_generic_scope():
    # 移設の実証: profit_band 固有の empty_series 判定は専用境界へ移り、汎用スコープの
    # _EMPTY_SERIES_INDICATORS は撤去される（汎用経路は指標名集合に依存しない）。
    assert not hasattr(ica, "_EMPTY_SERIES_INDICATORS")


def test_profit_band_bucket_empty_translates_to_empty_series():
    # LSP 是正 LSP-3: 型 EmptyBucketError で empty_series へ翻訳する（日本語メッセージ片照合ではない）。
    #   実 profit_band src が送出する型（bands.py の EmptyBucketError）を用いる＝実挙動と等価。
    empty_bucket_cls = call_binding.profit_band_empty_bucket_error()
    err = ica._translate_value_error("profit_band", empty_bucket_cls("必須バケットが空です"))
    assert err.error_type == "empty_series"


def test_profit_band_plain_value_error_translates_to_validation_even_with_marker():
    # 型が EmptyBucketError でなければ message に "バケット" を含んでも validation（メッセージ非依存の実証）。
    err = ica._translate_value_error("profit_band", ValueError("バケットという語を含む素の ValueError"))
    assert err.error_type == "validation"


def test_profit_band_other_value_error_translates_to_validation():
    err = ica._translate_value_error("profit_band", ValueError("normalize が不正です"))
    assert err.error_type == "validation"


def test_non_profit_band_value_error_stays_validation_even_with_marker():
    # 他指標は message に "バケット" を含んでも empty_series にならない（従来ガード保存）。
    err = ica._translate_value_error("tgp_btlm", ValueError("バケット風の文言"))
    assert err.error_type == "validation"
