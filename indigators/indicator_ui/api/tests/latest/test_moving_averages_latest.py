"""Stage B 検証 — moving_averages を Latest 増分計算フレームワークで一致検証する。

分類（本テストが接地する事実）:
  - has_binding: あり（call_binding._TABLE の ("moving_averages","default")＝line・kw、
    catalog def の compute.variants=['default']）。
  - archetype  : core バッファ関数は recurrence（ema/smma は buffer[i-1] 漸化、
    sma/lwma はスライド和の再帰）。latest_meta は ma_type で分類し、ema/smma→
    "recurrence"、sma/lwma→"window"。いずれも min_window=None（full フォールバック）
    のため latest 入力 df は full と同一＝末尾値が float 完全一致する。
  - series_kinds: catalog def.series は全て LINE（"MA"/"Smoothing"/"Upper"/"Lower"）。
    全て line → frontend routing="latest"（horizontal_line なし）。

不変条件（最重要）:
  各 line 系列について latest_compute の data[-K:] が full_compute の対応 data[-K:] と
  float 完全一致する（K=trailing_k=1）。

本テストは検証専用。共有ファイル（latest_meta.py / latest_dispatch.py / 指標 src）は
編集しない。import 規約: api/tests/conftest.py が api/ を sys.path へ追加済み。
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from adapter.compute import IndicatorComputeAdapter
from adapter.compute.latest_meta import latest_meta
from adapter.compute.latest_dispatch import full_compute, latest_compute

# catalog def.series は全て line（horizontal_line を含まない）→ frontend routing="latest"。
_SERIES_KINDS = {"line"}
# catalog def の compute.variants（単一 variant）。
_VARIANTS = ["default"]
# 主 MA 種別（catalog の ma_type enum 全件）。
_MA_TYPES = ["sma", "ema", "smma", "lwma"]


def _ohlcv(n: int = 100) -> pd.DataFrame:
    """最小妥当な昇順 OHLCV（length=9 を十分満たす本数）。"""
    idx = np.arange(max(n, 1))
    base = 10.0 + np.sin(np.linspace(0.0, 6.0 * np.pi, max(n, 1))) * 3.0
    sign = np.where(idx % 2 == 0, 1.0, -1.0)
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


def _base_params(ma_type: str, **overrides) -> dict:
    p = {
        "ma_type": ma_type,
        "length": 9,
        "source": "close",
        "offset": 0,
        "smoothing_type": "none",
        "smoothing_length": 9,
        "bb_stddev": 2.0,
        # catalog 既定（false）に合わせる: 最終足も計算し full/latest 同条件にする。
        "wait_for_close": False,
    }
    p.update(overrides)
    return p


# --------------------------------------------------------------------------- #
# 分類の接地（archetype / series kind / routing）
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "ma_type, expected_archetype",
    [("sma", "window"), ("ema", "recurrence"), ("smma", "recurrence"), ("lwma", "window")],
)
def test_archetype_classification(ma_type, expected_archetype):
    # latest_meta は ma_type により recurrence/window を分類し、いずれも full フォールバック
    # （min_window=None）・K=1。これにより latest 入力は full と同一になる。
    meta = latest_meta("moving_averages", "default", _base_params(ma_type))
    assert meta.archetype == expected_archetype
    assert meta.min_window is None
    assert meta.trailing_k == 1


# --------------------------------------------------------------------------- #
# 不変条件: latest の line data[-K:] == full の line data[-K:]（float 完全一致）
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("variant", _VARIANTS)
@pytest.mark.parametrize("ma_type", _MA_TYPES)
def test_latest_line_tail_equals_full_tail_exact(variant, ma_type):
    adapter = IndicatorComputeAdapter()
    df = _ohlcv(100)
    params = _base_params(ma_type)
    k = latest_meta("moving_averages", variant, params).trailing_k
    assert k == 1

    full = full_compute(adapter, "moving_averages", variant, df, dict(params))
    latest = latest_compute(adapter, "moving_averages", variant, df, dict(params))

    assert latest, "latest series should not be empty"
    # 全系列が line（catalog routing="latest" の前提を実出力で確認）。
    assert {s["kind"] for s in latest} == _SERIES_KINDS
    assert {s["kind"] for s in full} == _SERIES_KINDS

    full_by_name = {s["name"]: s for s in full}
    for s in latest:
        f = full_by_name[s["name"]]
        # line は末尾 K 点に切られている。
        assert len(s["data"]) <= k
        # 末尾K点が full の末尾K点と float 完全一致（time/value とも）。
        assert s["data"] == f["data"][-k:]


# --------------------------------------------------------------------------- #
# 平滑化 + BB 経路（Upper/Lower＝line）でも latest==full 末尾一致・全系列 line
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("variant", _VARIANTS)
def test_latest_smoothing_bb_lines_tail_equals_full_tail(variant):
    # smoothing_type=sma_bb のとき MA/Smoothing/Upper/Lower の 4 line を出力する。
    # 全て line（horizontal_line なし）であり末尾K一致を満たすことを確認する。
    adapter = IndicatorComputeAdapter()
    df = _ohlcv(100)
    params = _base_params("ema", smoothing_type="sma_bb", smoothing_length=9, bb_stddev=2.0)
    k = latest_meta("moving_averages", variant, params).trailing_k

    full = full_compute(adapter, "moving_averages", variant, df, dict(params))
    latest = latest_compute(adapter, "moving_averages", variant, df, dict(params))

    names = {s["name"] for s in full}
    assert {"MA", "Smoothing", "Upper", "Lower"} <= names
    assert {s["kind"] for s in latest} == _SERIES_KINDS

    full_by_name = {s["name"]: s for s in full}
    for s in latest:
        f = full_by_name[s["name"]]
        assert len(s["data"]) <= k
        assert s["data"] == f["data"][-k:]


# --------------------------------------------------------------------------- #
# latest 経路がエラーなく走る（全 ma_type）
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("ma_type", _MA_TYPES)
def test_latest_runs_without_error(ma_type):
    adapter = IndicatorComputeAdapter()
    df = _ohlcv(100)
    latest = latest_compute(
        adapter, "moving_averages", "default", df, _base_params(ma_type)
    )
    assert isinstance(latest, list)
    assert latest, "latest should return at least the MA line"
