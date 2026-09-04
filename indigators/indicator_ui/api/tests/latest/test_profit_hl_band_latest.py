"""Stage B 検証: profit_hl_band を Latest 増分計算フレームワークへ分類＋一致検証する。

分類（仕様 §4-0）:
  profit_hl_band の出力は価格軸の水平バンド線 8 本（up_*/dn_*）のみ＝非時系列の
  価格軸分布（axis_distribution）。catalog def.series は PF_HLINE 1 件
  （kind=horizontal_line）。系列 kind 群が horizontal_line を含むため frontend
  routing は "full"。

不変条件:
  latest_meta は profit_hl_band を明示登録していないため安全既定
  LatestMeta("recurrence", None, 1) で解決される。latest_dispatch._trail は
  line/histogram（data を持つ系列）のみ末尾K切りし、horizontal_line（lines を持ち
  data を持たない）は不変で素通しする。したがって:
    - line/histogram 系列は存在しない（trail 対象なし）。
    - horizontal_line は latest でも full と完全一致（全件・切られない）。
  → latest_compute(...) == full_compute(...)。

import 規約: conftest.py が api/ を sys.path へ追加済み。既存 src は read-only。
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from adapter.compute import IndicatorComputeAdapter
from adapter.compute.latest_meta import latest_meta
from adapter.compute.latest_dispatch import full_compute, latest_compute
import _contract  # noqa: E402  (latest/ 直下・pytest が本 dir を sys.path へ載せる)

# 本指標の compute_id / variant（catalog def＝単一 variant）。
_COMPUTE_ID = "profit_hl_band"
_VARIANTS = ("default",)

# trail 対象 kind（latest_dispatch._TRIMMABLE_KINDS と同義）。
_TRIMMABLE_KINDS = ("line", "histogram")


def _ohlcv(n: int = 150) -> pd.DataFrame:
    """昇順 OHLCV（date 含む）。close>0・N>=2（close[-2] 起点）・window=120 を満たす。

    既存 test_latest_compute._ohlcv と同流儀の合成波形。close は常に正（比率正規化の
    0 除算ガードを満たす）。add_hl_band は time/date 列を要求するため date を含める。
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


def _params() -> dict:
    """catalog 既定（PF_WINDOW=window:120）。core 既定とも整合。"""
    return {"window": 120}


# =========================================================================== #
# 分類: archetype（axis_distribution）と frontend routing（full）
# =========================================================================== #
def test_series_are_horizontal_line_only_axis_distribution():
    # 出力は horizontal_line のみ（価格軸分布）。line/histogram は出さない。
    adapter = IndicatorComputeAdapter()
    df = _ohlcv(150)
    for variant in _VARIANTS:
        series = full_compute(adapter, _COMPUTE_ID, variant, df, _params())
        kinds = {s["kind"] for s in series}
        assert kinds == {"horizontal_line"}, f"{variant}: {kinds}"
        # horizontal_line 群は lines を持ち data を持たない（trail 対象外）。
        for s in series:
            assert "lines" in s and "data" not in s


def test_meta_resolves_to_safe_default_recurrence_full_k1():
    # 未登録 → 安全既定 recurrence/full/K=1（EMA 平滑＋大域σで full 必須・正しい）。
    _contract.assert_safe_default_meta(_COMPUTE_ID, ("default",), _params)


# =========================================================================== #
# 不変条件: latest == full（horizontal_line は全件・切られない）
# =========================================================================== #
def test_latest_equals_full_for_all_variants():
    adapter = IndicatorComputeAdapter()
    df = _ohlcv(150)
    for variant in _VARIANTS:
        full = full_compute(adapter, _COMPUTE_ID, variant, df, _params())
        latest = latest_compute(adapter, _COMPUTE_ID, variant, df, _params())

        # latest 経路がエラーなく走り、非空であること。
        assert latest, f"{variant}: latest series should not be empty"

        # horizontal_line は latest でも全件返る（末尾K切りされない）。
        for s in latest:
            assert s["kind"] == "horizontal_line"
            assert len(s["lines"]) == len(
                next(f for f in full if f["name"] == s["name"])["lines"]
            )

        # line/histogram 系列は存在しない（trail 対象が無い）→ 全体が full と完全一致。
        assert all(s["kind"] not in _TRIMMABLE_KINDS for s in latest)
        assert latest == full, f"{variant}: latest != full"


def test_horizontal_line_band_count_is_eight():
    # 8 バンド（up_067/165/196/258・dn_067/165/196/258）が全件返ること。
    adapter = IndicatorComputeAdapter()
    df = _ohlcv(150)
    latest = latest_compute(adapter, _COMPUTE_ID, "default", df, _params())
    hl = next(s for s in latest if s["kind"] == "horizontal_line")
    assert len(hl["lines"]) == 8
    texts = {ln["text"] for ln in hl["lines"]}
    assert texts == {
        "up_067", "up_165", "up_196", "up_258",
        "dn_067", "dn_165", "dn_196", "dn_258",
    }
