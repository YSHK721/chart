"""Stage B 検証: profit_band を Latest 増分計算フレームワークへ分類＋一致検証する。

対象（catalog/call_binding バインディング・全 variant）:
  * profit_band / global  : add_profit_band   → bands.build_bands
  * profit_band / robust  : add_robust_profit_band → robust_bands.build_robust_bands

archetype 分類（core/bands/robust_bands を Read した結果・仕様 §4-0）:
  * global : bands.build_bands は np.quantile を *全期間* サンプルへ適用し、得た単一の
             分位点オフセットを全足の open に一律加算する。各足のバンド値が将来足を含む
             全データに依存する → look-ahead（df.tail で末尾値が変わる）。
  * robust : robust_bands.build_robust_bands は因果窓（expanding=bars[0..i] / rolling=直近N）で
             各足 i の分位点を逐次算出する → window（遡及・初期 min_obs 未満は NaN）。
  双方とも latest_meta は未登録のため安全既定 LatestMeta("recurrence", None, 1)
  ＝ full（min_window=None）＋ K=1 が適用される。full で計算するため latest と full は
  同一 df を使い、末尾 K 点は float 完全一致する（look-ahead/window でも安全）。

系列 kind: lwc_chart は create_line のみを呼ぶ（FakeChart 収集は全て kind="line"）。
  horizontal_line は出さない → frontend routing = "latest"（全 line）。

不変条件（最重要）:
  各 line 系列について latest_compute の data[-K:] が full_compute の対応 data[-K:] と
  float 完全一致する（K = latest_meta の trailing_k = 既定 1）。

import 規約: conftest.py が api/ を sys.path へ追加済み。既存 src は read-only。
本ファイルのみを新規作成し、共有ファイル（latest_meta/latest_dispatch/指標 src）は触らない。
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from adapter.compute import IndicatorComputeAdapter
from adapter.compute.latest_meta import latest_meta
from adapter.compute.latest_dispatch import full_compute, latest_compute

_VARIANTS = ("global", "robust")


def _ohlcv(n: int = 100) -> pd.DataFrame:
    """最小妥当な昇順 OHLCV。陽線/陰線を交互に持たせ、必須バケット(pOL/nOH/pOH/nOL)を
    確実に充足する（require_full=True / robust の min_obs=30 を満たすよう n>=100）。"""
    base = 100.0 + np.sin(np.linspace(0.0, 6.0 * np.pi, max(n, 1))) * 3.0
    sign = np.where(np.arange(max(n, 1)) % 2 == 0, 1.0, -1.0)
    open_ = base
    close = base + sign * 0.5  # 偶数足=陽線, 奇数足=陰線
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


def _params(variant: str) -> dict:
    """catalog 既定相当の params（call_binding が variant 非対象キーを捨てるため両用で渡せる）。"""
    return {
        "probabilities": [0.51, 0.8, 0.85, 0.9, 0.95, 0.98, 0.99],
        "buckets": ["nOH", "pOL", "pOH", "nOL"],
        "require_full": True,
        "legend": False,
        "normalize": "return",
        "window": "expanding",
        "atr_period": 14,
        "min_obs": 30,
    }


@pytest.mark.parametrize("variant", _VARIANTS)
def test_meta_defaults_to_recurrence_full_k1(variant):
    # profit_band は latest_meta 未登録 → 安全既定 recurrence / full / K=1。
    meta = latest_meta("profit_band", variant, _params(variant))
    assert meta.archetype == "recurrence"  # 安全既定（look-ahead/window を full で吸収）
    assert meta.min_window is None         # full（tail せず全件）
    assert meta.trailing_k == 1


@pytest.mark.parametrize("variant", _VARIANTS)
def test_all_series_are_line_kind(variant):
    # profit_band は line 系列のみ（horizontal_line なし）→ frontend routing = "latest"。
    adapter = IndicatorComputeAdapter()
    df = _ohlcv(100)
    series = full_compute(adapter, "profit_band", variant, df, _params(variant))
    assert series, "series should not be empty"
    assert all(s["kind"] == "line" for s in series)


@pytest.mark.parametrize("variant", _VARIANTS)
def test_latest_line_tail_equals_full_tail_exact(variant):
    # 最重要不変条件: latest の各 line data[-K:] が full の対応 data[-K:] と float 完全一致。
    adapter = IndicatorComputeAdapter()
    df = _ohlcv(100)
    params = _params(variant)
    k = latest_meta("profit_band", variant, params).trailing_k
    assert k == 1

    full = full_compute(adapter, "profit_band", variant, df, dict(params))
    latest = latest_compute(adapter, "profit_band", variant, df, dict(params))

    assert latest, "latest series should not be empty"
    full_by_name = {s["name"]: s for s in full}
    for s in latest:
        assert s["kind"] == "line"
        f = full_by_name[s["name"]]
        # latest 各系列は末尾 K 点に切られている。
        assert len(s["data"]) <= k
        # 末尾 K 点が full の末尾 K 点と float 完全一致（time/value とも）。
        assert s["data"] == f["data"][-k:]


@pytest.mark.parametrize("variant", _VARIANTS)
def test_latest_path_runs_without_error(variant):
    # latest 経路がエラーなく走り、line 系列を返す。
    adapter = IndicatorComputeAdapter()
    df = _ohlcv(100)
    latest = latest_compute(adapter, "profit_band", variant, df, _params(variant))
    assert isinstance(latest, list)
    assert all(s["kind"] == "line" for s in latest)
