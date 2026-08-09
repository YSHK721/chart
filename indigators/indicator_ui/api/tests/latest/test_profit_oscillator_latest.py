"""Stage B 検証: profit_oscillator を Latest 増分計算フレームワークへ分類＋一致検証。

検証対象（Stage A 基盤・不変）:
  latest_compute は /compute 境界で (1) df を min_window で tail、(2) 既存 adapter.compute を
  不変呼び出し、(3) 応答 series の line/histogram data を末尾 K 点に切る。horizontal_line は
  data を持たず切らない（全件）。

profit_oscillator の分類（実コード接地）:
  * バインディング: call_binding._TABLE[("profit_oscillator","default")] = add_oscillator
    （histogram）、catalog def.series = [PF_HIST('oscillator_lc'), PF_HLINE('profit_oscillator')]。
    単一 variant=default。
  * 系列 kind: {histogram, horizontal_line} → horizontal_line を含むため frontend routing は "full"。
  * archetype: core.compute_level_count は ps_level_count の標準化窓（window＝DEFAULT_WINDOW=120）で
    σ 距離を算出し、出力は σ12 水準線（horizontal_line＝価格軸分布）＋クランプ済 level_count
    （histogram 時系列）。latest_meta に未登録 → 安全既定 LatestMeta("recurrence", None, 1)
    （full＋K=1）が適用される。full 経路のため latest の histogram 末尾 K 点は full の末尾 K 点と
    float 完全一致する。

不変条件（最重要）:
  histogram 系列について latest_compute の data[-K:] が full_compute の data[-K:] と float 完全一致。
  horizontal_line は latest でも全件（full と同一）返る。

import 規約: api/tests/conftest.py が api/ を sys.path へ追加済み。既存 src は read-only。
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from adapter.compute import IndicatorComputeAdapter
from adapter.compute.latest_meta import latest_meta
from adapter.compute.latest_dispatch import full_compute, latest_compute
import _contract  # noqa: E402  (latest/ 直下・pytest が本 dir を sys.path へ載せる)

_COMPUTE_ID = "profit_oscillator"
_VARIANTS = ("default",)
_TRIMMABLE = ("line", "histogram")


def _ohlcv(n: int = 200) -> pd.DataFrame:
    """昇順 OHLCV（合成・period_a=6 / period_b=60 / window=120 を満たす十分な本数）。"""
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
    """catalog 既定（period_a=6 / period_b=60 / window=120）。"""
    return {"period_a": 6, "period_b": 60, "window": 120}


def test_latest_meta_profit_oscillator_safe_default_recurrence_full_k1():
    # latest_meta 未登録 → 安全既定 recurrence / full / K=1（必ず full と一致）。
    for variant in _VARIANTS:
        meta = latest_meta(_COMPUTE_ID, variant, _params())
        assert meta.archetype == "recurrence"
        assert meta.min_window is None
        assert meta.trailing_k == 1


def test_latest_runs_without_error_and_returns_series():
    # latest 経路がエラーなく走り、catalog 由来の kind を含む系列を返す。
    _contract.assert_latest_returns_kinds(
        _COMPUTE_ID, _VARIANTS, _ohlcv(200), _params,
        required={"histogram", "horizontal_line"},
    )


def test_latest_histogram_tail_equals_full_tail_exact():
    # 最重要: latest の data[-K:] が full の data[-K:] と float 完全一致（trimmable 全 kind）。
    _contract.assert_tail_matches_full(
        _COMPUTE_ID, _VARIANTS, _ohlcv(200), _params, kinds=_TRIMMABLE, expect_k=1
    )


def test_latest_horizontal_line_returned_untrimmed():
    # horizontal_line は末尾K切りせず全件（full と同一）返る。
    _contract.assert_horizontal_lines_untrimmed(
        _COMPUTE_ID, _VARIANTS, _ohlcv(200), _params
    )


