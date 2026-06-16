"""Stage B 検証: profit_volatility の Latest（末尾K）増分計算フレームワーク一致検証。

分類（仕様 §4-0）:
  archetype = recurrence（安全既定で full 解決される系）。実際の core は 2 系統あるが
  描画 add_volatility は build_volatility 経由で本質コア（compute_core_volatility）を使い、
  既定 window=120 の **因果ローリング窓** で z 標準化する。各バー a の標準化基準（平均・
  母標準偏差）は直近 window 本の過去のみ（区間 [a-window+1, a]）から算出され look-ahead を
  含まない（repaint しない）。ただし σ12 水準（clamp 境界 up_329/dn_329）は有効点全体の
  バッチ統計から決まるため、df.tail で開始点を変えると末尾値（クランプ後）が変わりうる。
  よって full 必須であり、latest_meta 未登録の安全既定 ("recurrence", None, 1)＝full+K=1 で
  解決され、latest は full と必ず一致する（tail せず全件 → 末尾 K=1 切りのみ）。

frontend routing:
  series kind は {histogram(volatility_lc), horizontal_line(σ12 水準線)}。
  horizontal_line を含むため "full"。

不変条件（最重要）:
  histogram 系列（volatility_lc）について latest の data[-K:] == full の data[-K:]（float 完全一致）。
  horizontal_line（profit_volatility = σ12 水準線群）は latest でも全件返る（末尾K切りの対象外）。

import 規約: conftest.py（api/tests/conftest.py）が api/ を sys.path へ追加済み。既存 src は read-only。
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from adapter.compute import IndicatorComputeAdapter
from adapter.compute.latest_meta import latest_meta
from adapter.compute.latest_dispatch import full_compute, latest_compute

_COMPUTE_ID = "profit_volatility"
_VARIANTS = ["default"]
# catalog 既定 params（PF_INT('period', 6) / PF_WINDOW()=PF_INT('window', 120, min:2)）。
_PARAMS = {"period": 6, "window": 120}


def _ohlcv(n: int = 200) -> pd.DataFrame:
    """最小妥当な昇順 OHLCV（合成・period=6 + window=120 を満たす十分な本数）。

    既存 test_latest_compute._ohlcv と同流儀（正弦 + 交互符号で HL を膨らませる）。
    OHLC4 は正値（base≈10±3 > 0）であり対数差 ln(ohlc4[a]/ohlc4[a-period]) が定義される。
    """
    base = 10.0 + np.sin(np.linspace(0.0, 6.0 * np.pi, max(n, 1))) * 3.0
    sign = np.where(np.arange(max(n, 1)) % 2 == 0, 1.0, -1.0)
    open_ = base
    close = base + sign * 0.5
    high = np.maximum(open_, close) + 1.0
    low = np.minimum(open_, close) - 1.0
    return pd.DataFrame(
        {
            "date": pd.date_range("2024-01-01", periods=n, freq="h"),
            "open": open_[:n],
            "high": high[:n],
            "low": low[:n],
            "close": close[:n],
            "volume": np.full(n, 1000.0),
        }
    )


def test_latest_meta_is_recurrence_full_k1():
    # 未登録 → 安全既定 ("recurrence", None, 1)。full+K=1 で必ず full と一致する。
    for variant in _VARIANTS:
        meta = latest_meta(_COMPUTE_ID, variant, dict(_PARAMS))
        assert meta.archetype == "recurrence"
        assert meta.min_window is None  # full（tail せず全件）
        assert meta.trailing_k == 1


def test_latest_runs_without_error_and_returns_expected_kinds():
    adapter = IndicatorComputeAdapter()
    df = _ohlcv(200)
    for variant in _VARIANTS:
        latest = latest_compute(adapter, _COMPUTE_ID, variant, df, dict(_PARAMS))
        kinds = {s["kind"] for s in latest}
        # histogram（volatility_lc）と horizontal_line（σ12 水準線群）の双方が出る。
        assert kinds == {"histogram", "horizontal_line"}


def test_histogram_latest_tail_equals_full_tail_exact():
    # 最重要: histogram 系列の latest data[-K:] が full data[-K:] と float 完全一致する。
    adapter = IndicatorComputeAdapter()
    df = _ohlcv(200)
    for variant in _VARIANTS:
        k = latest_meta(_COMPUTE_ID, variant, dict(_PARAMS)).trailing_k
        assert k == 1
        full = full_compute(adapter, _COMPUTE_ID, variant, df, dict(_PARAMS))
        latest = latest_compute(adapter, _COMPUTE_ID, variant, df, dict(_PARAMS))

        full_by_name = {s["name"]: s for s in full}
        hist_latest = [s for s in latest if s["kind"] == "histogram"]
        assert hist_latest, "histogram series should be present"
        for s in hist_latest:
            f = full_by_name[s["name"]]
            assert len(s["data"]) <= k  # 末尾 K 点に切られている
            assert s["data"] == f["data"][-k:]  # float 完全一致（time/value/color とも）


def test_horizontal_line_returned_in_full_in_latest():
    # horizontal_line（σ12 水準線群）は末尾K切りの対象外。latest でも full と同一（全件）。
    adapter = IndicatorComputeAdapter()
    df = _ohlcv(200)
    for variant in _VARIANTS:
        full = full_compute(adapter, _COMPUTE_ID, variant, df, dict(_PARAMS))
        latest = latest_compute(adapter, _COMPUTE_ID, variant, df, dict(_PARAMS))
        full_hl = [s for s in full if s["kind"] == "horizontal_line"]
        latest_hl = [s for s in latest if s["kind"] == "horizontal_line"]
        assert latest_hl, "horizontal_line series should be present"
        # 水平線群は data を持たず lines を持つ（切らない＝full と完全一致）。
        assert latest_hl == full_hl
