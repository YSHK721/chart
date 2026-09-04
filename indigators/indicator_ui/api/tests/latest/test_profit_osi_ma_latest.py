"""Stage B 検証: profit_osi_ma の Latest（末尾K）増分計算フレームワーク一致検証。

分類（仕様 §4-0）:
  archetype = recurrence。core.compute_osi_ma は MA バッファ（既定 ma_mode=1=EMA、
  exponential_ma_on_buffer の漸化 out[i]=out[i-1]+α(price[i]-out[i-1])）を共有 on_buffer で
  計算し、kairi[a]=(close[a-1]-ma_a)/ma_a*100 を求める。MA は先頭シードからの再帰のため
  df.tail で開始点を変えると末尾 MA 値が変わる（full 必須）。close[a-1] は 1 本古い終値で
  あり look-ahead ではない。出力は histogram 時系列（osi_ma_kairi）+ 水準線
  （±1/±0.5 の horizontal_line）で、価格軸分布（axis_distribution）ではない。
  latest_meta 未登録のため安全既定 ("recurrence", None, 1)＝full+K=1 で解決され、
  latest は full と必ず一致する。

frontend routing:
  series kind は {histogram, horizontal_line}。horizontal_line を含むため "full"。

不変条件（最重要）:
  histogram 系列（osi_ma_kairi）について latest の data[-K:] == full の data[-K:]（float 完全一致）。
  horizontal_line（profit_osi_ma = 水準線群）は latest でも全件返る（末尾K切りの対象外）。

import 規約: conftest.py（api/tests/conftest.py）が api/ を sys.path へ追加済み。既存 src は read-only。
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from adapter.compute import IndicatorComputeAdapter
from adapter.compute.latest_meta import latest_meta
from adapter.compute.latest_dispatch import full_compute, latest_compute
import _contract  # noqa: E402  (latest/ 直下・pytest が本 dir を sys.path へ載せる)

_COMPUTE_ID = "profit_osi_ma"
_VARIANTS = ["default"]
# catalog 既定 params（ma_mode ENUM 既定 1=EMA / PF_INT('ma_period', 21)）。
_PARAMS = {"ma_mode": 1, "ma_period": 21}


def _ohlcv(n: int = 200) -> pd.DataFrame:
    """最小妥当な昇順 OHLCV（合成・ma_period=21 を満たす十分な本数）。

    既存 test_latest_compute._ohlcv と同流儀（正弦 + 交互符号で HL を膨らませる）。
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
    _contract.assert_safe_default_meta(_COMPUTE_ID, _VARIANTS, lambda: dict(_PARAMS))


def test_latest_runs_without_error_and_returns_expected_kinds():
    # histogram と horizontal_line の双方が出る（catalog def.series と整合）。
    _contract.assert_latest_returns_kinds(
        _COMPUTE_ID, _VARIANTS, _ohlcv(200), lambda: dict(_PARAMS),
        exact={"histogram", "horizontal_line"},
    )


def test_histogram_latest_tail_equals_full_tail_exact():
    # 最重要: histogram 系列の latest data[-K:] が full data[-K:] と float 完全一致する。
    _contract.assert_tail_matches_full(
        _COMPUTE_ID, _VARIANTS, _ohlcv(200), lambda: dict(_PARAMS), expect_k=1
    )


def test_horizontal_line_returned_in_full_in_latest():
    # horizontal_line（σ 水準線群）は末尾K切りの対象外。latest でも full と同一（全件）。
    _contract.assert_horizontal_lines_identical_to_full(
        _COMPUTE_ID, _VARIANTS, _ohlcv(200), lambda: dict(_PARAMS)
    )


