"""Stage B 検証: profit_oscillator2 を Latest 増分計算フレームワークへ分類＋一致検証する。

対象: compute_id="profit_oscillator2"（catalog def＋call_binding _TABLE にバインディング有・
  variant=default・add_oscillator2）。

archetype 判定（core.py / lwc_chart.py 接地）:
  composition（他指標合成）。compute_oscillator2_full は RSI / MFI / WPR / iStochastic /
  MAROD（EMA）など複数サブオシレーターを funLevelCount 加重で合成し level_count を作る
  （compute_level_count）。さらに:
    - level_count は EMA（exponential_ma_on_buffer）を含む＝先頭シードからの再帰。
    - compute_levels2 は level_count 全系列の mean / 母σ（np.mean(x)）を取る全系列リダクション
      （σ6 水準線・RCI の sigma_ref に波及）。
  いずれも df.tail で開始点を変えると末尾値が変わるため、安全既定（full df）でのみ
  full と一致する。latest_meta は本指標を未登録＝安全既定 LatestMeta("recurrence", None, 1)
  ＝min_window=None（full df）/ trailing_k=1 で解決し、latest は full と同一 df で計算され
  float 完全一致する。

frontend routing: catalog def.series の kind は {histogram(oscillator2_lc),
  line(oscillator2_rci), horizontal_line(σ6 水準 6 本)}。horizontal_line を含むため
  "full"（末尾K切りせず全件返す経路）。

不変条件:
  - line/histogram 系列: latest の data[-K:] == full の data[-K:]（float 完全一致, K=trailing_k=1）。
  - horizontal_line 系列: latest でも full と同一に全件（σ6 水準線）返ること。
  - latest 経路がエラーなく走ること。

import 規約: conftest.py が api/ を sys.path へ追加済み。既存 src は read-only・改変禁止。
本ファイルのみ新規作成（他指標・共有ファイルは触らない）。
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from adapter.compute import IndicatorComputeAdapter
from adapter.compute.latest_meta import latest_meta
from adapter.compute.latest_dispatch import full_compute, latest_compute

# 本指標の compute_id / variant（catalog def＝単一 variant）。
_COMPUTE_ID = "profit_oscillator2"
_VARIANTS = ("default",)

# catalog 既定 params（PROFIT_OSCILLATOR2）。
_PARAMS = {
    "osc_period": 6,
    "stc_slow": 6,
    "ma_period": 60,
    "rci_period": 12,
    "direction": False,
}

# 末尾K切り対象（時系列 data を持つ系列）。
_TRIMMABLE_KINDS = ("line", "histogram")


def _ohlcv(n: int = 100) -> pd.DataFrame:
    """最小妥当な昇順 OHLCV（date/open/high/low/close/volume・十分な本数）。

    ma_period=60 / rci_period=12 を満たすため n=100（>=60+α）とする。high>low・volume>0 を
    保証し、add_oscillator2 の時刻解決のため date 列を持たせる。既存 latest テストと同流儀の
    合成波形。
    """
    base = 10.0 + np.sin(np.linspace(0.0, 6.0 * np.pi, n)) * 3.0
    sign = np.where(np.arange(n) % 2 == 0, 1.0, -1.0)
    open_ = base
    close = base + sign * 0.5
    high = np.maximum(open_, close) + 1.0
    low = np.minimum(open_, close) - 1.0
    return pd.DataFrame(
        {
            "date": pd.date_range("2024-01-01", periods=n, freq="h"),
            "open": open_,
            "high": high,
            "low": low,
            "close": close,
            "volume": np.full(n, 1000.0) + np.arange(n, dtype=np.float64),
        }
    )


# =========================================================================== #
# 分類: archetype（composition）と frontend routing（full）
# =========================================================================== #
def test_series_kinds_include_histogram_line_horizontal_line():
    # 出力 kind は {histogram(lc), line(rci), horizontal_line(σ6 水準)}。
    adapter = IndicatorComputeAdapter()
    df = _ohlcv()
    for variant in _VARIANTS:
        series = full_compute(adapter, _COMPUTE_ID, variant, df, _PARAMS)
        kinds = {s["kind"] for s in series}
        assert kinds == {"histogram", "line", "horizontal_line"}, f"{variant}: {kinds}"
        # horizontal_line は lines を持ち data を持たない（trail 対象外）。
        for s in series:
            if s["kind"] == "horizontal_line":
                assert "lines" in s and "data" not in s
            else:
                assert "data" in s


def test_meta_resolves_to_safe_default_recurrence_full_k1():
    # 未登録 → 安全既定 recurrence/full/K=1。
    #   composition だが EMA 再帰＋全系列統計（σ6/RCI）のため full df 必須。
    meta = latest_meta(_COMPUTE_ID, "default", _PARAMS)
    assert meta.archetype == "recurrence"  # 安全既定（明示 composition 登録なし）
    assert meta.min_window is None  # full df（tail しない）
    assert meta.trailing_k == 1


# =========================================================================== #
# 不変条件: latest の data[-K:] == full の data[-K:]（float 完全一致）
# =========================================================================== #
def test_latest_tail_equals_full_tail_for_all_variants():
    adapter = IndicatorComputeAdapter()
    df = _ohlcv()
    k = latest_meta(_COMPUTE_ID, "default", _PARAMS).trailing_k
    assert k == 1
    for variant in _VARIANTS:
        full = full_compute(adapter, _COMPUTE_ID, variant, df, _PARAMS)
        latest = latest_compute(adapter, _COMPUTE_ID, variant, df, _PARAMS)

        # latest 経路がエラーなく走り、非空であること。
        assert latest, f"{variant}: latest series should not be empty"
        # full と latest で系列構成（name/kind）が一致すること。
        assert [(s["name"], s["kind"]) for s in latest] == [
            (s["name"], s["kind"]) for s in full
        ], f"{variant}: series shape mismatch"

        full_by_name = {s["name"]: s for s in full}
        for s in latest:
            ref = full_by_name[s["name"]]
            if s["kind"] in _TRIMMABLE_KINDS:
                # 末尾 K 点が full の末尾 K 点と float 完全一致（==）。
                assert s["data"] == ref["data"][-k:], (
                    f"{variant}/{s['name']}: latest tail != full tail"
                )
                assert len(s["data"]) == k, f"{variant}/{s['name']}: tail len != K"
            else:
                # horizontal_line は latest でも全件（切られない・完全一致）。
                assert s["kind"] == "horizontal_line"
                assert s["lines"] == ref["lines"], (
                    f"{variant}/{s['name']}: horizontal_line not all returned"
                )


def test_horizontal_line_sigma6_levels_returned_in_full_on_latest():
    # σ6 水準線 6 本（up_165/196/258・dn_165/196/258）が latest でも全件返ること。
    adapter = IndicatorComputeAdapter()
    df = _ohlcv()
    latest = latest_compute(adapter, _COMPUTE_ID, "default", df, _PARAMS)
    hl = next(s for s in latest if s["kind"] == "horizontal_line")
    assert len(hl["lines"]) == 6
    texts = {ln["text"] for ln in hl["lines"]}
    assert texts == {"up_165", "up_196", "up_258", "dn_165", "dn_196", "dn_258"}
