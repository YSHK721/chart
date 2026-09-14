"""Stage B 検証: price_range_power を Latest 増分計算フレームワークに対し一致検証。

分類（仕様 §4-0）:
  archetype       = axis_distribution（価格軸分布・非時系列）。
                    core.compute_price_range_power は価格帯（級）ごとの度数・比率を
                    返し、lwc_chart.add_price_range_power は horizontal_line を発行する
                    （時系列 data を持たない）。よって末尾K切りは行わず全件返す
                    （latest_meta: archetype=axis_distribution / min_window=None /
                    trailing_k=None）。
  series_kinds    = {horizontal_line}（catalog def.series・catalog.js:184-186）。
  frontend_routing= full（horizontal_line を含むため latest 増分の対象外＝全件）。

不変条件:
  axis_distribution（trailing_k=None）では latest_compute は horizontal_line を
  末尾K切りせず全件返し、full_compute と完全一致する（latest == full）。
  line/histogram 系列は本指標に存在しないため末尾K一致条件は空に適用される。

共有ファイル（latest_meta.py / latest_dispatch.py / 指標 src）は read-only。本ファイルのみ新規。
import 規約: conftest.py が api/ を sys.path へ追加済み。
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from adapter.compute import IndicatorComputeAdapter
from adapter.compute.latest_meta import latest_meta
from adapter.compute.latest_dispatch import full_compute, latest_compute

_COMPUTE_ID = "price_range_power"
_VARIANTS = ("default",)
_TRIMMABLE_KINDS = ("line", "histogram")


def _ohlcv(n: int = 100) -> pd.DataFrame:
    """最小妥当な昇順 OHLCV（十分な本数・interval=1.0 でバンド数が妥当）。

    価格は 10±3 の正弦波。陽線/陰線/同値が混在するよう open/close を交互符号で構築し、
    high/low は ±1.0 でヒゲを付ける（4 系統 hc/ol/hl/lh が全て発生）。
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
    # interval=1.0 で価格レンジ ~12 → バンド数 ~13（爆発防止適応に掛からない妥当値）。
    return {"interval": 1.0, "top_n": 5}


def test_archetype_is_axis_distribution_full_no_trail():
    # 分類検証: axis_distribution / full（min_window=None）/ 末尾K切りなし（trailing_k=None）。
    for variant in _VARIANTS:
        meta = latest_meta(_COMPUTE_ID, variant, _params())
        assert meta.archetype == "axis_distribution"
        assert meta.min_window is None
        assert meta.trailing_k is None


def test_latest_runs_without_error_and_returns_horizontal_line():
    # latest 経路がエラーなく走り、系列が horizontal_line のみであること。
    adapter = IndicatorComputeAdapter()
    df = _ohlcv(100)
    for variant in _VARIANTS:
        latest = latest_compute(adapter, _COMPUTE_ID, variant, df, _params())
        assert latest, "latest series should not be empty"
        assert all(s["kind"] == "horizontal_line" for s in latest)


def test_horizontal_line_returned_full_and_latest_equals_full():
    # 不変条件: horizontal_line は latest でも全件（末尾K切りされない）→ latest == full。
    adapter = IndicatorComputeAdapter()
    df = _ohlcv(100)
    for variant in _VARIANTS:
        full = full_compute(adapter, _COMPUTE_ID, variant, df, _params())
        latest = latest_compute(adapter, _COMPUTE_ID, variant, df, _params())

        # horizontal_line は data を持たず lines を持つ（時系列ではない）。
        for s in full:
            assert s["kind"] == "horizontal_line"
            assert "data" not in s
            assert "lines" in s
        # 全件一致（切られていない）。
        assert latest == full


def test_no_trimmable_series_present():
    # 本指標に line/histogram 系列は存在しない（末尾K一致条件は空適用）。
    adapter = IndicatorComputeAdapter()
    df = _ohlcv(100)
    for variant in _VARIANTS:
        full = full_compute(adapter, _COMPUTE_ID, variant, df, _params())
        trimmable = [s for s in full if s.get("kind") in _TRIMMABLE_KINDS]
        assert trimmable == []
