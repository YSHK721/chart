"""Stage B 検証: profit_stc を Latest 増分計算フレームワークへ分類＋一致検証。

検証対象（Stage A 基盤・不変）:
  latest_compute は /compute 境界で (1) df を min_window で tail、(2) 既存 adapter.compute を
  不変呼び出し、(3) 応答 series の line/histogram data を末尾 K 点に切る。horizontal_line は
  data を持たず切らない（全件）。

profit_stc の分類（実コード接地）:
  * バインディング: call_binding._TABLE[("profit_stc","default")] = add_stc（line）、
    catalog def.series = [PF_LINE('stc_osc'), PF_HLINE('profit_stc')]。単一 variant=default。
  * 系列 kind: {line, horizontal_line} → horizontal_line を含むため frontend routing は "full"。
  * archetype: stc_osc は mql_builtins.compute_stochastic による生 %K で、各点 a は直近 period 本の
    high/low/close 窓（min/max）のみに依存する独立窓（window・再帰バッファ無し）。σ 水準線
    （P1/P2/M1/M2）は全系列平均±母σの大域統計（horizontal_line＝価格軸分布）。
    latest_meta に未登録 → 安全既定 LatestMeta("recurrence", None, 1)（full＋K=1）が適用される。
    full 経路のため latest の line 末尾 K 点は full の末尾 K 点と float 完全一致する。

不変条件（最重要）:
  line 系列について latest_compute の data[-K:] が full_compute の data[-K:] と float 完全一致。
  horizontal_line は latest でも全件（full と同一）返る。

import 規約: api/tests/conftest.py が api/ を sys.path へ追加済み。既存 src は read-only。
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from adapter.compute import IndicatorComputeAdapter
from adapter.compute.latest_meta import latest_meta
from adapter.compute.latest_dispatch import full_compute, latest_compute

_COMPUTE_ID = "profit_stc"
_VARIANTS = ("default",)
_TRIMMABLE = ("line", "histogram")


def _ohlcv(n: int = 200) -> pd.DataFrame:
    """昇順 OHLCV（合成・period=70 を満たす十分な本数）。"""
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
    """catalog 既定（period=70）。"""
    return {"period": 70}


def test_latest_meta_profit_stc_safe_default_recurrence_full_k1():
    # latest_meta 未登録 → 安全既定 recurrence / full / K=1（必ず full と一致）。
    for variant in _VARIANTS:
        meta = latest_meta(_COMPUTE_ID, variant, _params())
        assert meta.archetype == "recurrence"
        assert meta.min_window is None
        assert meta.trailing_k == 1


def test_latest_runs_without_error_and_returns_series():
    # latest 経路がエラーなく走り、系列を返す。
    adapter = IndicatorComputeAdapter()
    df = _ohlcv(200)
    for variant in _VARIANTS:
        latest = latest_compute(adapter, _COMPUTE_ID, variant, df, _params())
        assert latest, "latest series should not be empty"
        kinds = {s["kind"] for s in latest}
        # catalog 由来: line（stc_osc）＋ horizontal_line（σ 水準 P1/P2/M1/M2）。
        assert "line" in kinds
        assert "horizontal_line" in kinds


def test_latest_line_tail_equals_full_tail_exact():
    # 最重要: line 系列について latest の data[-K:] が full の data[-K:] と float 完全一致。
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
        assert trimmable, "expected at least one line series"
        for s in trimmable:
            f = full_by_name[s["name"]]
            assert len(s["data"]) <= k
            # 末尾 K 点が full の末尾 K 点と float 完全一致（time/value とも）。
            assert s["data"] == f["data"][-k:]


def test_latest_horizontal_line_returned_untrimmed():
    # horizontal_line は末尾K切りせず全件（full と同一）返る。
    adapter = IndicatorComputeAdapter()
    df = _ohlcv(200)
    for variant in _VARIANTS:
        params = _params()
        full = full_compute(adapter, _COMPUTE_ID, variant, df, dict(params))
        latest = latest_compute(adapter, _COMPUTE_ID, variant, df, dict(params))
        full_by_name = {s["name"]: s for s in full}

        hlines = [s for s in latest if s["kind"] == "horizontal_line"]
        assert hlines, "expected horizontal_line σ levels"
        for s in hlines:
            # horizontal_line は data を持たず lines を持つ → full と完全一致（切らない）。
            assert s == full_by_name[s["name"]]
