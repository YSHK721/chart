"""Stage B 検証: profit_rmm_macd を Latest 増分計算フレームワークへ分類＋一致検証。

検証対象（Stage A 基盤・不変）:
  latest_compute は /compute 境界で (1) df を min_window で tail、(2) 既存 adapter.compute を
  不変呼び出し、(3) 応答 series の line/histogram data を末尾 K 点に切る。

profit_rmm_macd の分類（実コード接地）:
  * バインディング: call_binding._TABLE[("profit_rmm_macd","default")] = add_rmmmacd（histogram）。
    catalog def.series = [PF_HIST('rmmmacd_hist'), PF_LINE('RMMWMACD'), PF_LINE('Signal')]。
    単一 variant=default。
  * 系列 kind: {histogram, line} → horizontal_line を含まないため frontend routing は "latest"。
    本指標は σ 水準線を出力しない（元 funIndicatorSet を OnCalculate で呼ばず・lwc_chart.py 参照）。
  * archetype: core は 4 オシレーター（iRSI/iWPR/iMFI/MAROD）を funLevelCount で合算し level_count を
    生成（合成）、それに fast/slow EMA → macd(=slow-fast) → signal EMA → histogram の MACD 連鎖
    （recurrence）を適用する。level_count の標準化窓（window=120・因果ローリング span）も併用する。
    合成＋EMA 再帰（先頭シード依存）の複合のため、latest_meta 未登録 → 安全既定
    LatestMeta("recurrence", None, 1)（full＋K=1）が適用される。full 経路のため latest の
    histogram/line 末尾 K 点は full の末尾 K 点と float 完全一致する。

不変条件（最重要）:
  histogram/line 各系列について latest_compute の data[-K:] が full_compute の data[-K:] と
  float 完全一致。

import 規約: api/tests/conftest.py が api/ を sys.path へ追加済み。既存 src は read-only。
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from adapter.compute import IndicatorComputeAdapter
from adapter.compute.latest_meta import latest_meta
from adapter.compute.latest_dispatch import full_compute, latest_compute

_COMPUTE_ID = "profit_rmm_macd"
_VARIANTS = ("default",)
_TRIMMABLE = ("line", "histogram")


def _ohlcv(n: int = 200) -> pd.DataFrame:
    """昇順 OHLCV（合成・osc_period=6 / window=120 を満たす十分な本数）。

    既定 window=120 の因果ローリング span は先頭 119 本が warm-up（NaN）であり、
    EMA 連鎖も加味すると有限 histogram/line を得るには 120 本超が必要。安全に 200 本とる。
    """
    base = 10.0 + np.sin(np.linspace(0.0, 6.0 * np.pi, max(n, 1))) * 3.0
    sign = np.where(np.arange(max(n, 1)) % 2 == 0, 1.0, -1.0)
    open_ = base
    close = base + sign * 0.5
    high = np.maximum(open_, close) + 1.0
    low = np.minimum(open_, close) - 1.0
    return pd.DataFrame(
        {
            "open": open_[:n],
            "high": high[:n],
            "low": low[:n],
            "close": close[:n],
            "volume": np.full(n, 1000.0),
            "time": pd.date_range("2024-01-01", periods=n, freq="h"),
        }
    )


def _params() -> dict:
    """catalog 既定（osc_period=6 / ma_period=6 / fast=4 / slow=8 / signal=4 / window=120）。"""
    return {
        "osc_period": 6, "ma_period": 6,
        "fast": 4, "slow": 8, "signal": 4,
        "window": 120,
    }


def test_latest_meta_profit_rmm_macd_safe_default_recurrence_full_k1():
    # latest_meta 未登録 → 安全既定 recurrence / full / K=1（必ず full と一致）。
    for variant in _VARIANTS:
        meta = latest_meta(_COMPUTE_ID, variant, _params())
        assert meta.archetype == "recurrence"
        assert meta.min_window is None
        assert meta.trailing_k == 1


# --------------------------------------------------------------------------- #
# 回帰: 既定 window=120 の warm-up NaN を line 系列も dropna する（lwc_chart 修正済み）。
#   修正前は histogram のみ dropna し line 2 本（RMMWMACD/Signal）は NaN 込みで set したため、
#   iterrows が「datetime の time 列＋NaN 値列」の warm-up 行を datetime64 と推論し NaN→NaT 化、
#   描画側 float(NaT) が TypeError で full/latest 双方を crash させていた。line も dropna 済み。
# --------------------------------------------------------------------------- #
def test_default_window_compute_succeeds_after_line_dropna_fix():
    """回帰: catalog 既定 window=120 で full/latest が TypeError なく compute できる。

    line 系列の warm-up NaN を dropna しない欠陥が再発すると iterrows の NaN→NaT 化で
    float(NaT) が落ちるため、本テストが crash して検知する。
    """
    adapter = IndicatorComputeAdapter()
    df = _ohlcv(200)
    params = _params()  # window=120（catalog 既定）
    full = full_compute(adapter, _COMPUTE_ID, "default", df, dict(params))
    latest = latest_compute(adapter, _COMPUTE_ID, "default", df, dict(params))
    assert full and latest
    assert {s["kind"] for s in full} == {"histogram", "line"}


def test_latest_runs_without_error_and_returns_series():
    # 既定 params（window=120）で latest 経路がエラーなく走り、histogram＋line を返す（水準線なし）。
    adapter = IndicatorComputeAdapter()
    df = _ohlcv(200)
    for variant in _VARIANTS:
        latest = latest_compute(adapter, _COMPUTE_ID, variant, df, _params())
        assert latest, "latest series should not be empty"
        kinds = {s["kind"] for s in latest}
        assert "histogram" in kinds
        assert "line" in kinds
        assert "horizontal_line" not in kinds  # frontend routing = "latest"
        assert all(s["kind"] in _TRIMMABLE for s in latest)


def test_latest_trimmable_tail_equals_full_tail_exact():
    # 最重要: histogram/line 各系列について latest の data[-K:] が full の data[-K:] と
    # float 完全一致（既定 params・安全既定 recurrence/full/K=1）。
    adapter = IndicatorComputeAdapter()
    df = _ohlcv(200)
    for variant in _VARIANTS:
        params = _params()
        k = latest_meta(_COMPUTE_ID, variant, params).trailing_k
        assert k == 1
        full = full_compute(adapter, _COMPUTE_ID, variant, df, dict(params))
        latest = latest_compute(adapter, _COMPUTE_ID, variant, df, dict(params))
        full_by_name = {s["name"]: s for s in full}

        trimmable = [s for s in latest if s["kind"] in _TRIMMABLE]
        assert trimmable, "expected at least one histogram/line series"
        for s in trimmable:
            f = full_by_name[s["name"]]
            assert len(s["data"]) <= k
            assert s["data"] == f["data"][-k:]
