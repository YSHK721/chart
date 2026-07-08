"""Stage B 検証: profit_hlband の Latest 増分計算フレームワーク一致検証。

対象: compute_id="profit_hlband"（catalog def＋call_binding _TABLE にバインディング有）。
  variant=separate : histogram(hl_range) ＋ σ 水準線（horizontal_line 群）
  variant=overlay  : High/Low バンドの horizontal_line 群 8 本

archetype 判定（core.py 接地）:
  compute_range は range[i]=high[i]-low[i]（各点独立・warm-up なし）だが、
  compute_range_stats は np.mean / 母σ を *全系列* に対して取る全系列リダクション、
  compute_hl_bands は最新足 high[-1]/low[-1] へ σ 帯を投影する。窓スライドでも漸化でもなく
  「全系列統計 ＋ 最新足投影」。latest_meta は本指標を未登録（安全既定）として扱い
  LatestMeta("recurrence", None, 1)＝min_window=None（full df）/ trailing_k=1 を返すため、
  latest は full と同一 df で計算され float 完全一致する。

frontend routing: catalog def.series の kind は {histogram, horizontal_line}。
  horizontal_line を含むため "full"（末尾K切りせず全件返す経路）。

不変条件:
  - line/histogram 系列: latest の data[-K:] == full の data[-K:]（float 完全一致, K=trailing_k=1）。
  - horizontal_line 系列: latest でも full と同一に全件（全水平線）返ること。
  - latest 経路がエラーなく走ること。

import 規約: conftest.py が api/ を sys.path へ追加済み。既存 src は read-only・改変禁止。
本ファイルのみ新規作成（他指標・共有ファイルは触らない）。
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from adapter.compute import IndicatorComputeAdapter
from adapter.compute.latest_meta import latest_meta
from adapter.compute.latest_dispatch import full_compute, latest_compute

_COMPUTE_ID = "profit_hlband"
_VARIANTS = ("separate", "overlay")
_PARAMS = {"draw_levels": True}

# 末尾K切り対象（時系列 data を持つ系列）。
_TRIMMABLE_KINDS = ("line", "histogram")


def _ohlcv(n: int = 100) -> pd.DataFrame:
    """最小妥当な昇順 OHLCV（date/open/high/low/close/volume・十分な本数）。

    profit_hlband は warm-up（period）を持たず全 i 定義のため n=100 で十分。
    high>low を保証し、separate variant の時刻解決のため date 列を持たせる。
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
            "volume": np.full(n, 1000.0),
        }
    )


def _by_kind(payloads, kind):
    return [p for p in payloads if p.get("kind") == kind]


@pytest.fixture(scope="module")
def adapter():
    return IndicatorComputeAdapter()


@pytest.fixture(scope="module")
def df():
    return _ohlcv(100)


@pytest.mark.parametrize("variant", _VARIANTS)
def test_latest_runs_without_error(adapter, df, variant):
    """latest 経路がエラーなく走り、非空 payload を返す。"""
    out = latest_compute(adapter, _COMPUTE_ID, variant, df, _PARAMS)
    assert isinstance(out, list)
    assert len(out) > 0


@pytest.mark.parametrize("variant", _VARIANTS)
def test_latest_matches_full_trimmable(adapter, df, variant):
    """line/histogram 各系列で latest の data[-K:] == full の data[-K:]（float 完全一致）。"""
    meta = latest_meta(_COMPUTE_ID, variant, _PARAMS)
    k = meta.trailing_k if meta.trailing_k is not None else 1

    full = full_compute(adapter, _COMPUTE_ID, variant, df, _PARAMS)
    latest = latest_compute(adapter, _COMPUTE_ID, variant, df, _PARAMS)

    full_tr = {p["name"]: p for p in _by_kind(full, "line") + _by_kind(full, "histogram")}
    latest_tr = {p["name"]: p for p in _by_kind(latest, "line") + _by_kind(latest, "histogram")}

    assert set(full_tr) == set(latest_tr)
    for name, fp in full_tr.items():
        lp = latest_tr[name]
        f_tail = fp["data"][-k:]
        l_tail = lp["data"][-k:]
        assert len(l_tail) == len(f_tail)
        for fpt, lpt in zip(f_tail, l_tail):
            assert fpt["time"] == lpt["time"]
            assert fpt["value"] == lpt["value"]  # float 完全一致


@pytest.mark.parametrize("variant", _VARIANTS)
def test_horizontal_line_returned_in_full(adapter, df, variant):
    """horizontal_line は latest でも full と同一に全件（全水平線）返る。"""
    full = full_compute(adapter, _COMPUTE_ID, variant, df, _PARAMS)
    latest = latest_compute(adapter, _COMPUTE_ID, variant, df, _PARAMS)

    full_hl = _by_kind(full, "horizontal_line")
    latest_hl = _by_kind(latest, "horizontal_line")

    assert len(full_hl) == len(latest_hl)
    for fp, lp in zip(full_hl, latest_hl):
        f_lines = fp["lines"]
        l_lines = lp["lines"]
        assert len(l_lines) == len(f_lines)  # 末尾切りされず全件
        for fl, ll in zip(f_lines, l_lines):
            assert fl["price"] == ll["price"]  # float 完全一致


def test_series_kinds_present(adapter, df):
    """separate は histogram＋horizontal_line、overlay は horizontal_line を出すこと。"""
    sep = full_compute(adapter, _COMPUTE_ID, "separate", df, _PARAMS)
    ov = full_compute(adapter, _COMPUTE_ID, "overlay", df, _PARAMS)

    assert _by_kind(sep, "histogram")
    assert _by_kind(sep, "horizontal_line")
    assert _by_kind(ov, "horizontal_line")
