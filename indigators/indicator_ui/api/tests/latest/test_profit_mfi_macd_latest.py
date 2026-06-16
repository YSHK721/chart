"""Stage B 検証: profit_mfi_macd の Latest 増分計算フレームワーク一致検証。

対象: compute_id="profit_mfi_macd"（catalog def＋call_binding _TABLE にバインディング有）。
  variant=default : histogram(mfimacd_hist) ＋ line 2 本(MFIMACD/Signal)
                    ＋ σ 水準線 7 本（horizontal_line 群: ±1/2/3σ ＋ 中央線 50）。

archetype 判定（core.py 接地）:
  compute_mfimacd は iMFI(compute_mfi) → fast/slow EMA(exponential_ma_on_buffer)
  → macd=fast-slow → signal=EMA(macd) → histogram=2.618*(macd-signal) の
  「他系列合成（MACD 型コンポジション）」であり、σ7水準は histogram *全系列* の
  avg±σ（母σ÷N）という全系列リダクション＋価格軸水準（horizontal_line）である。
  単一の窓スライドでも単純漸化でもなく、複数系列を合成する composition。
  latest_meta は本指標を未登録（安全既定）として扱い LatestMeta("recurrence", None, 1)
  ＝min_window=None（full df）/ trailing_k=1 を返すため、latest は full と同一 df で
  計算され、内部の EMA 漸化（先頭シード依存）込みで float 完全一致する。

frontend routing: catalog def.series の kind は {histogram, line, horizontal_line}。
  horizontal_line を含むため "full"（末尾K切りせず全件返す経路）。

不変条件:
  - line/histogram 系列: latest の data[-K:] == full の data[-K:]（float 完全一致, K=trailing_k=1）。
  - horizontal_line 系列: latest でも full と同一に全件（σ7水準すべて）返ること。
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

_COMPUTE_ID = "profit_mfi_macd"
_VARIANTS = ("default",)
# catalog 既定 params（PF_INT mfi_period=13/fast=4/slow=8/signal=4）。
_PARAMS = {"mfi_period": 13, "fast": 4, "slow": 8, "signal": 4}

# 末尾K切り対象（時系列 data を持つ系列）。
_TRIMMABLE_KINDS = ("line", "histogram")


def _ohlcv(n: int = 100) -> pd.DataFrame:
    """最小妥当な昇順 OHLCV（date/open/high/low/close/volume・十分な本数）。

    mfi_period=13 ＋ EMA(4/8/4) を満たすよう n=100（warm-up を十分上回る）。
    iMFI は volume 必須・high>low を保証し、時刻解決のため date 列を持たせる。
    """
    base = 10.0 + np.sin(np.linspace(0.0, 6.0 * np.pi, n)) * 3.0
    sign = np.where(np.arange(n) % 2 == 0, 1.0, -1.0)
    open_ = base
    close = base + sign * 0.5
    high = np.maximum(open_, close) + 1.0
    low = np.minimum(open_, close) - 1.0
    # MFI は volume の偏りに依存するため一定でない volume を与える。
    volume = 1000.0 + (np.arange(n) % 7) * 100.0
    return pd.DataFrame(
        {
            "date": pd.date_range("2024-01-01", periods=n, freq="h"),
            "open": open_,
            "high": high,
            "low": low,
            "close": close,
            "volume": volume,
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


def test_binding_and_meta_defaults():
    """バインディング確認＋安全既定 meta（recurrence/full/K=1）の確認。"""
    meta = latest_meta(_COMPUTE_ID, "default", _PARAMS)
    # 未登録のため安全既定: full（min_window=None）＋ trailing_k=1。
    assert meta.archetype == "recurrence"
    assert meta.min_window is None
    assert meta.trailing_k == 1


@pytest.mark.parametrize("variant", _VARIANTS)
def test_latest_runs_without_error(adapter, df, variant):
    """latest 経路がエラーなく走り、非空 payload を返す。"""
    out = latest_compute(adapter, _COMPUTE_ID, variant, df, _PARAMS)
    assert isinstance(out, list)
    assert len(out) > 0


def test_series_kinds_present(adapter, df):
    """histogram 1・line 2・horizontal_line 群を出すこと（catalog def.series と整合）。"""
    full = full_compute(adapter, _COMPUTE_ID, "default", df, _PARAMS)
    assert len(_by_kind(full, "histogram")) == 1
    assert len(_by_kind(full, "line")) == 2
    assert len(_by_kind(full, "horizontal_line")) == 1


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
    assert latest_tr, "trimmable series should not be empty"
    for name, fp in full_tr.items():
        lp = latest_tr[name]
        # latest 各系列は末尾 K 点に切られている。
        assert len(lp["data"]) <= k
        f_tail = fp["data"][-k:]
        l_tail = lp["data"][-k:]
        assert len(l_tail) == len(f_tail)
        for fpt, lpt in zip(f_tail, l_tail):
            assert fpt["time"] == lpt["time"]
            assert fpt["value"] == lpt["value"]  # float 完全一致


@pytest.mark.parametrize("variant", _VARIANTS)
def test_horizontal_line_returned_in_full(adapter, df, variant):
    """horizontal_line（σ7水準）は latest でも full と同一に全件返る（末尾切りされない）。"""
    full = full_compute(adapter, _COMPUTE_ID, variant, df, _PARAMS)
    latest = latest_compute(adapter, _COMPUTE_ID, variant, df, _PARAMS)

    full_hl = _by_kind(full, "horizontal_line")
    latest_hl = _by_kind(latest, "horizontal_line")

    assert len(full_hl) == len(latest_hl) == 1
    f_lines = full_hl[0]["lines"]
    l_lines = latest_hl[0]["lines"]
    assert len(l_lines) == len(f_lines) == 7  # ±1/2/3σ ＋ 中央線 50
    for fl, ll in zip(f_lines, l_lines):
        assert fl["price"] == ll["price"]  # float 完全一致
        assert fl["text"] == ll["text"]
