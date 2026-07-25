"""adapter/compute（FakeChart / CallBinding / IndicatorComputeAdapter）の検証。

PORTING_GUIDE §7 / 既存 tests/test_lwc_chart.py の Fake チャート手法を流用し、
描画ライブラリに依存せず既存 add_* を実接続で呼び、系列 JSON 化を検証する。

import 規約: conftest.py が api/ を sys.path へ追加済み。既存指標 src は
read-only import（改変しない）。
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

# adapter は各指標 src を一意なパッケージ名でファイルパスから読み込むため（同名 src
# 衝突の回避・call_binding._load_src_package）、本テストは top-level ``src`` を import
# しない。adapter 経由でのみ既存 add_* を呼ぶ。

from adapter.compute import (  # noqa: E402
    CallBinding,
    FakeHorizontalChart,
    FakeLineChart,
    IndicatorComputeAdapter,
)


def _patch_tgp_unavailable(monkeypatch):
    """tgp バックエンド不在（rpy2/R/tgp 未導入）を環境非依存に再現する。

    R/tgp/rpy2 を導入済みの環境でも「backend_unavailable へのエラー翻訳」契約を検証できるよう、
    _fitter_factory("tgp") が fit_predict 時に ImportError を送出する fitter を返すよう差し替える。
    """
    from adapter.compute import call_binding

    class _UnavailableTgpFitter:
        def fit_predict(self, *args, **kwargs):
            raise ImportError("rpy2 未導入（テストで tgp 不在を再現）")

    original = call_binding._fitter_factory

    def fake(name, samples="standard"):
        return _UnavailableTgpFitter() if name == "tgp" else original(name, samples)

    monkeypatch.setattr(call_binding, "_fitter_factory", fake)


# --------------------------------------------------------------------------- #
# テストデータ（合成 OHLCV）
# --------------------------------------------------------------------------- #
def _ohlcv(n: int = 80, *, with_time: bool = True) -> pd.DataFrame:
    # 陽線/陰線を交互に含む合成 OHLCV（profit_band の require_full で全バケットを満たす）。
    base = 10.0 + np.sin(np.linspace(0.0, 6.0 * np.pi, max(n, 1))) * 3.0
    sign = np.where(np.arange(max(n, 1)) % 2 == 0, 1.0, -1.0)  # 偶数=陽線 / 奇数=陰線
    open_ = base
    close = base + sign * 0.5
    high = np.maximum(open_, close) + 1.0
    low = np.minimum(open_, close) - 1.0
    data = {
        "open": open_[:n],
        "high": high[:n],
        "low": low[:n],
        "close": close[:n],
        "volume": np.full(n, 1000.0),
    }
    if with_time:
        data["time"] = pd.date_range("2024-01-01", periods=n, freq="h")
    return pd.DataFrame(data)


# =========================================================================== #
# FakeChart
# =========================================================================== #
def test_fake_line_chart_create_line_collects_name_and_kwargs():
    # Arrange
    chart = FakeLineChart()
    # Act
    line = chart.create_line(name="btlm_mean", color="rgba(1,2,3,1)", style="solid", width=2)
    # Assert
    assert line.name == "btlm_mean"
    assert line.kwargs["color"] == "rgba(1,2,3,1)"
    assert line.kwargs["style"] == "solid"
    assert line.kwargs["width"] == 2


def test_fake_line_set_collects_dataframe():
    # Arrange
    chart = FakeLineChart()
    line = chart.create_line(name="btlm_mean")
    df = pd.DataFrame({"time": pd.to_datetime(["2024-01-01"]), "btlm_mean": [1.5]})
    # Act
    line.set(df)
    # Assert
    assert line.points is df


def test_fake_line_chart_to_payloads_emits_line_with_unix_seconds():
    # Arrange
    chart = FakeLineChart()
    line = chart.create_line(name="btlm_mean", style="solid", width=2, color="rgba(1,2,3,1)")
    ts = pd.Timestamp("2024-01-01 00:00:00")
    line.set(pd.DataFrame({"time": [ts], "btlm_mean": [1.25]}))
    # Act
    payloads = chart.to_payloads()
    # Assert
    assert len(payloads) == 1
    p = payloads[0]
    assert p["name"] == "btlm_mean"
    assert p["kind"] == "line"
    assert p["style"] == "solid"
    assert p["width"] == 2
    assert p["color"] == "rgba(1,2,3,1)"
    assert p["data"] == [{"time": int(ts.timestamp()), "value": 1.25}]
    assert isinstance(p["data"][0]["time"], int)


def test_fake_line_chart_legend_is_absorbed():
    # Arrange
    chart = FakeLineChart()
    # Act / Assert: add_profit_band の legend 呼び出しを吸収（例外を出さない）
    chart.legend(visible=True)


def test_fake_line_chart_to_payloads_empty_data_when_set_not_called():
    # Arrange: create_line のみで set 未呼出（points is None）
    chart = FakeLineChart()
    chart.create_line(name="btlm_mean", style="solid", width=2)
    # Act
    payloads = chart.to_payloads()
    # Assert: データ無し（空 list）でも payload は生成される
    assert payloads[0]["name"] == "btlm_mean"
    assert payloads[0]["data"] == []


def test_fake_horizontal_chart_collects_price_and_axis_label():
    # Arrange
    chart = FakeHorizontalChart()
    # Act
    chart.horizontal_line(
        price=1.2345, color="rgba(46,158,91,0.9)", width=2, style="solid",
        text="BULL 12.34", axis_label_visible=False,
    )
    # Assert
    assert len(chart.lines) == 1
    hl = chart.lines[0]
    assert hl.price == 1.2345
    assert hl.kwargs["text"] == "BULL 12.34"
    assert hl.kwargs["axis_label_visible"] is False


def test_fake_horizontal_chart_to_payloads_emits_horizontal_line():
    # Arrange
    chart = FakeHorizontalChart()
    chart.horizontal_line(
        price=1.2345, color="rgba(46,158,91,0.9)", width=2, style="solid",
        text="BULL 12.34", axis_label_visible=False,
    )
    # Act
    payloads = chart.to_payloads()
    # Assert
    assert len(payloads) == 1
    p = payloads[0]
    assert p["kind"] == "horizontal_line"
    assert p["axis_label_visible"] is False
    assert len(p["lines"]) == 1
    assert p["lines"][0]["price"] == 1.2345
    assert p["lines"][0]["text"] == "BULL 12.34"


# =========================================================================== #
# CallBinding
# =========================================================================== #
def test_call_binding_resolves_tgp_btlm_to_line():
    # Arrange / Act
    binding = CallBinding.resolve("tgp_btlm", "default")
    # Assert
    assert binding.output_kind == "line"


def test_call_binding_invoke_passes_fitter_as_third_positional():
    # Arrange: fitter enum "ols" → OlsBtlmFitter 実体化、第3位置で add_btlm へ
    chart = FakeLineChart()
    binding = CallBinding.resolve("tgp_btlm", "default")
    df = _ohlcv(60)
    # Act
    binding.invoke(chart, df, {"fitter": "ols", "maxbars": 40, "q_low": 0.05, "q_high": 0.95})
    # Assert: 3 ライン収集（btlm_mean/btlm_q5/btlm_q95）
    payloads = chart.to_payloads()
    names = [p["name"] for p in payloads]
    assert names == ["btlm_mean", "btlm_q5", "btlm_q95"]


def test_fitter_factory_tgp_uses_fixed_seed_for_live_determinism():
    # 回帰: tgp(MCMC) は seed 固定でなければライブ再計算ごとにトレンド/帯が揺れる。
    # _fitter_factory("tgp") が固定 seed 付き TgpBtlmFitter を返すことを担保（None 退行防止）。
    from adapter.compute import call_binding

    assert call_binding._TGP_SEED is not None
    fitter = call_binding._fitter_factory("tgp")  # R 不在でも実体化は成功
    assert fitter.seed == call_binding._TGP_SEED


def test_fitter_factory_tgp_samples_select_bte_preset():
    # MCMC サンプル選択: standard(既定)/high/max が BTE プリセットへ写像される。
    from adapter.compute import call_binding

    assert call_binding._fitter_factory("tgp").bte == call_binding._BTE_PRESETS["standard"]
    assert call_binding._fitter_factory("tgp", "high").bte == call_binding._BTE_PRESETS["high"]
    assert call_binding._fitter_factory("tgp", "max").bte == call_binding._BTE_PRESETS["max"]
    # 未知値は standard へフォールバック（不正入力で壊れない）。
    assert call_binding._fitter_factory("tgp", "bogus").bte == call_binding._BTE_PRESETS["standard"]
    # Total は standard<high<max（サンプル増の単調性）。
    totals = [call_binding._BTE_PRESETS[k][1] for k in ("standard", "high", "max")]
    assert totals == sorted(totals) and len(set(totals)) == 3


def test_fitter_factory_ols_ignores_samples():
    # ols は解析解のため samples を無視（bte 属性を持たない OlsBtlmFitter）。
    from adapter.compute import call_binding

    fitter = call_binding._fitter_factory("ols", "max")
    assert not hasattr(fitter, "bte")


def test_fitter_factory_default_matches_catalog():
    # 既定の二重定義（catalog.js mcmc_samples 既定 'standard' と _DEFAULT_SAMPLES）の乖離防止。
    # catalog 側は catalog.test.js が 'standard' を固定。backend 側を本テストで固定する。
    from adapter.compute import call_binding

    assert call_binding._DEFAULT_SAMPLES == "standard"
    assert call_binding._DEFAULT_SAMPLES in call_binding._BTE_PRESETS


def test_invoke_btlm_passes_mcmc_samples_to_factory_not_to_add_btlm(monkeypatch):
    # E2E（invoke 経路）: params の mcmc_samples が pop されて factory へ届き、add_btlm には
    # 漏れない（漏れれば TypeError）。fitter は R 不要の OlsBtlmFitter で完走させ samples を捕捉。
    from adapter.compute import call_binding

    captured = {}

    def fake_factory(name, samples="standard"):
        captured["name"] = name
        captured["samples"] = samples
        src = call_binding._load_src_package("tgp_btlm")
        return src.OlsBtlmFitter()  # R 不要・add_btlm が完走

    monkeypatch.setattr(call_binding, "_fitter_factory", fake_factory)
    chart = FakeLineChart()
    binding = CallBinding.resolve("tgp_btlm", "default")
    binding.invoke(chart, _ohlcv(60),
                   {"fitter": "tgp", "mcmc_samples": "high",
                    "maxbars": 40, "q_low": 0.05, "q_high": 0.95})
    # samples が factory へ届いた
    assert captured == {"name": "tgp", "samples": "high"}
    # add_btlm が mcmc_samples 漏れなく完走（3 ライン収集）
    assert [p["name"] for p in chart.to_payloads()] == ["btlm_mean", "btlm_q5", "btlm_q95"]


def test_call_binding_invoke_profit_band_keyword_only():
    # Arrange
    chart = FakeLineChart()
    binding = CallBinding.resolve("profit_band", "global")
    df = _ohlcv(60)
    # Act
    binding.invoke(chart, df, {"probabilities": (0.99,), "buckets": ("pOL",)})
    # Assert: series_name は "pOL 99%"
    names = [p["name"] for p in chart.to_payloads()]
    assert "pOL 99%" in names


def test_call_binding_resolves_profit_band_robust_variant():
    # Arrange / Act
    binding = CallBinding.resolve("profit_band", "robust")
    chart = FakeLineChart()
    df = _ohlcv(80)
    binding.invoke(chart, df, {"probabilities": (0.99,), "buckets": ("pOL",), "min_obs": 5})
    # Assert: robust も pOL 99% を生成
    names = [p["name"] for p in chart.to_payloads()]
    assert "pOL 99%" in names


def test_call_binding_invoke_price_range_power_keyword_only():
    # Arrange
    chart = FakeHorizontalChart()
    binding = CallBinding.resolve("price_range_power", "default")
    df = _ohlcv(80)
    # Act
    binding.invoke(chart, df, {"interval": 1.0, "top_n": 3})
    # Assert
    assert binding.output_kind == "horizontal_line"


def test_call_binding_fitter_tgp_raises_import_error(monkeypatch):
    # Arrange: tgp バックエンド不在を再現（fit_predict 時に ImportError）
    _patch_tgp_unavailable(monkeypatch)
    chart = FakeLineChart()
    binding = CallBinding.resolve("tgp_btlm", "default")
    df = _ohlcv(40)
    # Act / Assert
    with pytest.raises(ImportError):
        binding.invoke(chart, df, {"fitter": "tgp", "maxbars": 30})


def test_call_binding_unknown_compute_id_raises_keyerror():
    # Act / Assert
    with pytest.raises(KeyError):
        CallBinding.resolve("does_not_exist", "default")


# =========================================================================== #
# IndicatorComputeAdapter（統合・read-only import で実 add_* を呼ぶ）
# =========================================================================== #
def test_adapter_tgp_btlm_ols_emits_three_line_series_with_unix_time():
    # Arrange
    adapter = IndicatorComputeAdapter()
    df = _ohlcv(60)
    # Act
    series = adapter.compute("tgp_btlm", "default",
                             df, {"fitter": "ols", "maxbars": 40, "q_low": 0.05, "q_high": 0.95})
    # Assert
    names = [s["name"] for s in series]
    assert names == ["btlm_mean", "btlm_q5", "btlm_q95"]
    assert all(s["kind"] == "line" for s in series)
    first_point = series[0]["data"][0]
    assert isinstance(first_point["time"], int)
    assert isinstance(first_point["value"], float)


def test_adapter_profit_band_uses_series_name_not_source_column():
    # Arrange
    adapter = IndicatorComputeAdapter()
    df = _ohlcv(60)
    # Act
    series = adapter.compute("profit_band", "global",
                             df, {"probabilities": (0.99,), "buckets": ("pOL",)})
    # Assert: name は series_name "pOL 99%"（source_column "pOL_99" ではない）
    names = [s["name"] for s in series]
    assert "pOL 99%" in names
    assert "pOL_99" not in names


def test_adapter_price_range_power_emits_horizontal_line_with_axis_label():
    # Arrange
    adapter = IndicatorComputeAdapter()
    df = _ohlcv(80)
    # Act
    series = adapter.compute("price_range_power", "default",
                             df, {"interval": 1.0, "top_n": 3})
    # Assert
    assert all(s["kind"] == "horizontal_line" for s in series)
    assert all(s["axis_label_visible"] is False for s in series)


def test_adapter_missing_ohlc_column_translates_to_missing_column():
    # Arrange: open 列欠落
    adapter = IndicatorComputeAdapter()
    df = _ohlcv(60).drop(columns=["open"])
    # Act / Assert
    with pytest.raises(ComputeError) as exc:
        adapter.compute("profit_band", "global", df, {"probabilities": (0.99,)})
    assert exc.value.error_type == "missing_column"


def test_adapter_missing_time_translates_to_missing_time():
    # Arrange: time_required=true（tgp_btlm）で time/date/DatetimeIndex 無し
    adapter = IndicatorComputeAdapter()
    df = _ohlcv(60, with_time=False)
    # Act / Assert
    with pytest.raises(ComputeError) as exc:
        adapter.compute("tgp_btlm", "default", df, {"fitter": "ols", "maxbars": 40})
    assert exc.value.error_type == "missing_time"


def test_adapter_correlation_constraint_violation_translates_to_validation():
    # Arrange: q_low >= q_high（相関制約違反）
    adapter = IndicatorComputeAdapter()
    df = _ohlcv(60)
    # Act / Assert
    with pytest.raises(ComputeError) as exc:
        adapter.compute("tgp_btlm", "default",
                        df, {"fitter": "ols", "maxbars": 40, "q_low": 0.96, "q_high": 0.95})
    assert exc.value.error_type == "validation"


def test_adapter_empty_input_translates_to_empty_series():
    # Arrange: 空 OHLC
    adapter = IndicatorComputeAdapter()
    df = _ohlcv(0)
    # Act / Assert
    with pytest.raises(ComputeError) as exc:
        adapter.compute("tgp_btlm", "default", df, {"fitter": "ols", "maxbars": 40})
    assert exc.value.error_type == "empty_series"


def test_adapter_required_bucket_empty_translates_to_empty_series():
    # Arrange: 全行が陽線（単調増加）→ profit_band の require_full で陰線バケットが空
    n = 60
    ramp = np.linspace(10.0, 20.0, n)
    df = pd.DataFrame(
        {
            "open": ramp,
            "high": ramp + 1.0,
            "low": ramp - 1.0,
            "close": ramp + 0.5,  # 常に close>open（陽線のみ）
            "time": pd.date_range("2024-01-01", periods=n, freq="h"),
        }
    )
    adapter = IndicatorComputeAdapter()
    # Act / Assert
    with pytest.raises(ComputeError) as exc:
        adapter.compute("profit_band", "global", df, {"probabilities": (0.99,)})
    assert exc.value.error_type == "empty_series"


def test_profit_band_value_error_translation_is_type_based_not_message_based():
    # LSP 是正 LSP-3: 翻訳は EmptyBucketError 型で識別する（日本語メッセージ片照合ではない）。
    from adapter.compute.call_binding import profit_band_empty_bucket_error
    from adapter.compute.indicator_compute_adapter import (
        _translate_profit_band_value_error,
    )

    empty_bucket_cls = profit_band_empty_bucket_error()
    assert issubclass(empty_bucket_cls, ValueError)  # 後方互換（サブクラス）

    # 型が EmptyBucketError → empty_series（メッセージに "バケット" が無くても型で判定）。
    got_empty = _translate_profit_band_value_error(empty_bucket_cls("必須系統なし"))
    assert got_empty.error_type == "empty_series"

    # 素の ValueError（型が EmptyBucketError でない）→ validation。
    #   従来のメッセージ照合なら "バケット" を含めば empty_series になったが、型判定では validation。
    got_validation = _translate_profit_band_value_error(ValueError("バケットという語を含むが型は素の ValueError"))
    assert got_validation.error_type == "validation"


def test_adapter_profit_band_robust_invalid_normalize_translates_to_validation():
    # robust 経路の normalize 不正（EmptyBucketError ではない素の ValueError）→ validation。
    adapter = IndicatorComputeAdapter()
    df = _ohlcv(60)
    with pytest.raises(ComputeError) as exc:
        adapter.compute("profit_band", "robust", df,
                        {"probabilities": (0.99,), "buckets": ("pOL",),
                         "min_obs": 5, "normalize": "zscore"})
    assert exc.value.error_type == "validation"


def test_adapter_tgp_fitter_without_rpy2_translates_to_backend_unavailable(monkeypatch):
    # Arrange: tgp バックエンド不在を再現 → ImportError を backend_unavailable へ翻訳
    _patch_tgp_unavailable(monkeypatch)
    adapter = IndicatorComputeAdapter()
    df = _ohlcv(60)
    # Act / Assert
    with pytest.raises(ComputeError) as exc:
        adapter.compute("tgp_btlm", "default", df, {"fitter": "tgp", "maxbars": 40})
    assert exc.value.error_type == "backend_unavailable"


# =========================================================================== #
# moving_averages（複数 MA 種別 × 期間・line 群）
# =========================================================================== #
def test_call_binding_resolves_moving_averages_to_line_kw():
    binding = CallBinding.resolve("moving_averages", "default")
    assert binding.output_kind == "line"
    assert binding._kind == "kw"


def test_adapter_moving_averages_main_only_when_smoothing_none():
    # 単一 MA モデル: 平滑化 none は主系列 "MA" のみ（time は int UNIX 秒）。
    adapter = IndicatorComputeAdapter()
    df = _ohlcv(60)
    series = adapter.compute(
        "moving_averages", "default", df,
        {"ma_type": "ema", "length": 9, "source": "close",
         "smoothing_type": "none", "wait_for_close": False},
    )
    assert [s["name"] for s in series] == ["MA"]
    assert series[0]["kind"] == "line"
    assert isinstance(series[0]["data"][0]["time"], int)


def test_adapter_moving_averages_emits_smoothing_and_bb_series():
    # 平滑化 sma_bb は MA / Smoothing / Upper / Lower の 4 系列を出力する。
    adapter = IndicatorComputeAdapter()
    df = _ohlcv(80)
    series = adapter.compute(
        "moving_averages", "default", df,
        {"ma_type": "sma", "length": 20, "source": "close", "offset": 0,
         "smoothing_type": "sma_bb", "smoothing_length": 9, "bb_stddev": 2.0,
         "wait_for_close": False},
    )
    assert sorted(s["name"] for s in series) == ["Lower", "MA", "Smoothing", "Upper"]
    assert all(s["kind"] == "line" for s in series)


def test_adapter_moving_averages_source_compound_hl2_is_accepted():
    # 合成ソース hl2 を受理し主 MA を出力する。
    adapter = IndicatorComputeAdapter()
    df = _ohlcv(40)
    series = adapter.compute(
        "moving_averages", "default", df,
        {"ma_type": "sma", "length": 5, "source": "hl2",
         "smoothing_type": "none", "wait_for_close": False},
    )
    assert [s["name"] for s in series] == ["MA"]


@pytest.mark.parametrize(
    "source, formula",
    [
        ("hl2", lambda o, h, low, c: (h + low) / 2.0),
        ("hlc3", lambda o, h, low, c: (h + low + c) / 3.0),
        ("ohlc4", lambda o, h, low, c: (o + h + low + c) / 4.0),
        ("hlcc4", lambda o, h, low, c: (h + low + c + c) / 4.0),
    ],
)
def test_adapter_moving_averages_composite_source_values_are_exact(source, formula):
    # 合成ソースの SMA が「合成価格の rolling 平均」と厳密一致する（独立手計算と突合）。
    adapter = IndicatorComputeAdapter()
    n, length = 40, 5
    df = _ohlcv(n)
    o = df["open"].to_numpy(float)
    h = df["high"].to_numpy(float)
    low = df["low"].to_numpy(float)
    c = df["close"].to_numpy(float)
    # 独立に合成価格 → SMA（rolling mean）を時刻キーで算出。
    price = formula(o, h, low, c)
    exp = pd.Series(price).rolling(length, min_periods=length).mean().to_numpy()
    times = [int(pd.Timestamp(t).timestamp()) for t in df["time"]]
    expected = {times[i]: exp[i] for i in range(n) if not np.isnan(exp[i])}

    series = adapter.compute(
        "moving_averages", "default", df,
        {"ma_type": "sma", "length": length, "source": source,
         "smoothing_type": "none", "offset": 0, "wait_for_close": False},
    )
    got = {p["time"]: p["value"] for p in series[0]["data"]}
    assert set(got) == set(expected)
    for t in got:
        assert abs(got[t] - expected[t]) < 1e-6


def test_adapter_moving_averages_empty_when_length_exceeds_bars():
    # 期間が本数を超える場合は系列を出さない（空）。
    adapter = IndicatorComputeAdapter()
    df = _ohlcv(10)
    series = adapter.compute(
        "moving_averages", "default", df,
        {"ma_type": "sma", "length": 999, "smoothing_type": "none"},
    )
    assert series == []


# ComputeError は adapter.compute の §6.3.4 翻訳例外（テスト import の都合で末尾に置く）。
from adapter.compute import ComputeError  # noqa: E402


# ===========================================================================
# FakeChart（統合スタブ）— line / histogram / horizontal_line の一括収集
# ===========================================================================
from adapter.compute import FakeChart  # noqa: E402


def test_fake_chart_collects_line_histogram_and_levels_in_one_chart():
    # 1 指標内で create_line / create_histogram / horizontal_line を併用しても
    # 統合 FakeChart は各 payload を kind 別に収集する（旧 Fake は排他で不可だった）。
    chart = FakeChart(name="profit_x")
    line = chart.create_line(name="osc")
    line.set(pd.DataFrame({"time": pd.date_range("2024-01-01", periods=2, freq="h"), "osc": [1.0, 2.0]}))
    hist = chart.create_histogram(name="lc")
    hist.set(pd.DataFrame({
        "time": pd.date_range("2024-01-01", periods=2, freq="h"),
        "lc": [-1.0, 3.0],
        "color": ["#aaa", "#bbb"],  # per-bar 着色
    }))
    chart.horizontal_line(price=1.65, color="#545454", width=1, style="dotted", text="up")
    chart.horizontal_line(price=-1.65, color="#545454", width=1, style="dotted", text="dn")

    payloads = chart.to_payloads()
    by_name = {p["name"]: p for p in payloads}
    assert by_name["osc"]["kind"] == "line"
    assert by_name["lc"]["kind"] == "histogram"
    # histogram は per-point color を載せる。
    assert by_name["lc"]["data"][0]["color"] == "#aaa"
    # horizontal_line 群は name=コンストラクタ名（compute_id）で 1 件にまとまる。
    hl = by_name["profit_x"]
    assert hl["kind"] == "horizontal_line"
    assert len(hl["lines"]) == 2


def test_fake_chart_reproduces_price_range_power_horizontal_payload():
    # price_range_power は compute_id と同名のため統合 FakeChart でも従来 name を再現する。
    chart = FakeChart(name="price_range_power")
    chart.horizontal_line(price=100.0, color="g", width=2, style="solid", text="BULL")
    payloads = chart.to_payloads()
    assert payloads[0]["name"] == "price_range_power"
    assert payloads[0]["kind"] == "horizontal_line"


def test_adapter_profit_rsi_emits_two_lines_and_levels():
    # line+水準線併用指標が統合 FakeChart 経由で系列名どおり出る（F3 照合対象名）。
    adapter = IndicatorComputeAdapter()
    df = _ohlcv(120)
    series = adapter.compute("profit_rsi", "default", df, {"rsi_period": 6, "apply": 5, "ma_period": 5})
    names = {p["name"]: p["kind"] for p in series}
    assert names["rsi"] == "line"
    assert names["rsi_ma"] == "line"
    assert names["profit_rsi"] == "horizontal_line"


def test_adapter_profit_adx_needle_emits_histogram_with_per_bar_color():
    adapter = IndicatorComputeAdapter()
    df = _ohlcv(120)
    series = adapter.compute("profit_adx_needle", "default", df, {"period": 6})
    hist = next(p for p in series if p["kind"] == "histogram")
    assert hist["name"] == "adx_needle"
    assert any("color" in pt for pt in hist["data"])  # level_colors の per-bar 着色
