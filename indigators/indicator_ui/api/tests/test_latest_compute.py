"""Latest（末尾K）増分計算フレームワーク（Stage A 基盤）の検証。

設計入力（latest_meta.py / latest_dispatch.py / compute_controller の mode 分岐）:
  純粋関数（各指標 core / add_*）は不変。Latest は /compute 境界で
  (1) 入力 df を min_window で tail、(2) 既存 adapter.compute を不変呼び出し、
  (3) 応答 series の各 line/histogram data を末尾 K 点に切る。

不変条件（最重要）:
  moving_averages の各 line 系列について latest_compute の data[-K:] が
  full_compute の対応 data[-K:] と float 完全一致する。

  ★ 設計上の確定事項（実コード接地・upstream-input-validation 済）:
    moving_averages の SMA core（simple_ma_on_buffer）は「各点独立の窓和」ではなく
    スライド和の再帰 buffer[i]=buffer[i-1]+(price[i]-price[i-period])/period であり、
    df.tail で開始点を変えると末尾値に ~1e-15 の浮動小数ドリフトが累積する。よって
    min_window=2*length では float 完全一致しない（実測 diff ~7e-15）。spec の分岐
    「満たさなければ full フォールバックを既定にする」に従い、window archetype も
    min_window=None（full）を既定とし float 完全一致を保証する（本テストで明示）。

import 規約: conftest.py が api/ を sys.path へ追加済み。既存 src は read-only。
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from adapter.compute import IndicatorComputeAdapter
from adapter.compute.latest_meta import LatestMeta, latest_meta
from adapter.compute.latest_dispatch import full_compute, latest_compute


# --------------------------------------------------------------------------- #
# テストデータ（合成 OHLCV・既存 test_compute_adapter._ohlcv と同流儀）
# --------------------------------------------------------------------------- #
def _ohlcv(n: int = 200) -> pd.DataFrame:
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


# =========================================================================== #
# latest_meta（archetype 登録・安全既定）
# =========================================================================== #
def test_latest_meta_unregistered_defaults_to_recurrence_full_k1():
    # 安全既定: 未登録 compute_id は recurrence / full / K=1（必ず full と一致）。
    meta = latest_meta("does_not_exist", "default", {})
    assert isinstance(meta, LatestMeta)
    assert meta.archetype == "recurrence"
    assert meta.min_window is None
    assert meta.trailing_k == 1


def test_latest_meta_moving_averages_sma_is_incremental_full_fallback_k1():
    # ISSUE-233: sma は増分計算（archetype=incremental・状態器 moving_averages）で計算する。
    # 増分器が扱えないパラメータで落ちる従来経路は、2*length が float 完全一致しないため
    # full フォールバック（min_window=None）を維持する。K=1。
    meta = latest_meta("moving_averages", "default", {"ma_type": "sma", "length": 9})
    assert meta.archetype == "incremental"
    assert meta.incremental == "moving_averages"
    assert meta.min_window is None  # full フォールバック（spec の分岐に従う）
    assert meta.trailing_k == 1


def test_latest_meta_moving_averages_ema_is_incremental_full_fallback_k1():
    # ema も同じく増分計算。落ちたときの従来経路は full・K=1。
    meta = latest_meta("moving_averages", "default", {"ma_type": "ema", "length": 9})
    assert meta.archetype == "incremental"
    assert meta.incremental == "moving_averages"
    assert meta.min_window is None
    assert meta.trailing_k == 1


def test_latest_meta_price_range_power_is_axis_distribution_full_no_trail():
    # price_range_power は axis_distribution・full・trailing_k=None（全件・末尾K切りしない）。
    meta = latest_meta("price_range_power", "default", {"interval": 1.0})
    assert meta.archetype == "axis_distribution"
    assert meta.min_window is None
    assert meta.trailing_k is None


# =========================================================================== #
# 不変条件（latest 末尾K == full 末尾K・float 完全一致）
# =========================================================================== #
@pytest.mark.parametrize("ma_type", ["sma", "ema"])
def test_latest_line_tail_equals_full_tail_exact_for_moving_averages(ma_type):
    # 最重要: latest_compute の各 line 系列 data[-K:] が full_compute の対応 data[-K:] と
    # float 完全一致する（増分計算・従来経路のどちらを通っても同値）。
    # K は production の trailing_k（moving_averages は 1）を用いる。
    adapter = IndicatorComputeAdapter()
    df = _ohlcv(200)
    params = {
        "ma_type": ma_type, "length": 9, "source": "close",
        "smoothing_type": "none", "offset": 0,
    }
    k = latest_meta("moving_averages", "default", params).trailing_k
    assert k == 1  # 増分計算・従来経路とも K=1
    full = full_compute(adapter, "moving_averages", "default", df, dict(params))
    latest = latest_compute(adapter, "moving_averages", "default", df, dict(params))

    full_by_name = {s["name"]: s for s in full}
    assert latest, "latest series should not be empty"
    for s in latest:
        f = full_by_name[s["name"]]
        # latest の各系列は末尾 K 点に切られている。
        assert len(s["data"]) <= k
        # 末尾K点が full の末尾K点と float 完全一致（time/value とも完全一致）。
        assert s["data"] == f["data"][-k:]


@pytest.mark.parametrize("k", [1, 3])
def test_latest_uses_trailing_k_when_meta_overridden(monkeypatch, k):
    # trailing_k を K に差し替えたとき、latest の line data がちょうど末尾 K 点になる。
    from adapter.compute import latest_dispatch

    def fake_meta(compute_id, variant, params):
        return LatestMeta("recurrence", None, k)

    monkeypatch.setattr(latest_dispatch, "latest_meta", fake_meta)
    adapter = IndicatorComputeAdapter()
    df = _ohlcv(200)
    params = {"ma_type": "ema", "length": 9, "source": "close",
              "smoothing_type": "none", "offset": 0}
    latest = latest_compute(adapter, "moving_averages", "default", df, dict(params))
    for s in latest:
        assert len(s["data"]) == k


# =========================================================================== #
# axis_distribution（price_range_power）— horizontal_line を全件返す
# =========================================================================== #
def test_latest_price_range_power_returns_horizontal_line_untrimmed():
    # axis_distribution（trailing_k=None）は horizontal_line を末尾K切りせず全件返す。
    adapter = IndicatorComputeAdapter()
    df = _ohlcv(120)
    params = {"interval": 1.0, "top_n": 3}
    full = full_compute(adapter, "price_range_power", "default", df, dict(params))
    latest = latest_compute(adapter, "price_range_power", "default", df, dict(params))
    assert all(s["kind"] == "horizontal_line" for s in latest)
    # horizontal_line は data を持たず lines を持つ（切らない＝full と同一）。
    assert latest == full


# =========================================================================== #
# full_compute は既存 adapter.compute と同一（後方互換の基準）
# =========================================================================== #
def test_full_compute_equals_adapter_compute():
    adapter = IndicatorComputeAdapter()
    df = _ohlcv(60)
    params = {"ma_type": "sma", "length": 9, "source": "close",
              "smoothing_type": "none", "offset": 0}
    direct = adapter.compute("moving_averages", "default", df, dict(params))
    via = full_compute(adapter, "moving_averages", "default", df, dict(params))
    assert via == direct
