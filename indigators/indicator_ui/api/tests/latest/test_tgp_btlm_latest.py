"""Stage B 検証: tgp_btlm を Latest 増分計算フレームワークへ分類＋一致検証する。

対象（catalog/call_binding バインディング・全 variant）:
  * tgp_btlm / default : add_btlm → bands.build_btlm_bands（fitter は backendParam）。
    catalog の fitter 既定は "ols"（reference.OlsBtlmFitter・R 非依存・決定論的）。
    "tgp" は R/tgp/rpy2 を要し本環境では fit_predict 時 ImportError（backend_unavailable）に
    なるため、検証は決定論の "ols" を用いる（variant は default 単一・fitter は計算 variant
    ではなく backend 選択パラメータ）。

archetype 分類（core/bands/lwc_chart を Read した結果・仕様 §4-0）:
  * window : bands.build_btlm_bands は直近 window=min(maxbars, n) 本の価格
    （series[-window:]）に対してのみ回帰を当てはめ、窓の手前は NaN を置く（遡及・窓系）。
    buf[i-1] 漸化（recurrence）でも他指標合成（composition）でも horizontal_line 出力
    （axis_distribution）でもない。
  latest_meta は未登録のため安全既定 LatestMeta("recurrence", None, 1)
  ＝ full（min_window=None）＋ K=1 が適用される。full で計算するため latest と full は
  同一 df を使い、末尾 K 点は float 完全一致する（window でも安全）。

系列 kind: lwc_chart は create_line のみを呼ぶ（btlm_mean ＋ 下/上分位線の 3 line）。
  catalog def.series も全て LINE。horizontal_line は出さない → frontend routing = "latest"。

不変条件（最重要）:
  各 line 系列について latest_compute の data[-K:] が full_compute の対応 data[-K:] と
  float 完全一致する（K = latest_meta の trailing_k = 既定 1）。

import 規約: conftest.py が api/ を sys.path へ追加済み。既存 src は read-only。
本ファイルのみを新規作成し、共有ファイル（latest_meta/latest_dispatch/指標 src）は触らない。
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from adapter.compute import IndicatorComputeAdapter
from adapter.compute.latest_meta import latest_meta
from adapter.compute.latest_dispatch import full_compute, latest_compute

_VARIANT = "default"


def _ohlcv(n: int = 100) -> pd.DataFrame:
    """最小妥当な昇順 OHLCV（time 必須・timeRequired=true）。

    n=100 は catalog 既定 maxbars=100 を満たし、OLS の最小観測数(3)も充足する。
    """
    base = 100.0 + np.sin(np.linspace(0.0, 6.0 * np.pi, max(n, 1))) * 3.0
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
    """catalog 既定相当の params（fitter は決定論の "ols" を用いる）。"""
    return {
        "fitter": "ols",
        "price": "open",
        "maxbars": 100,
        "q_low": 0.05,
        "q_high": 0.95,
        "color": "rgba(123, 104, 238, 1)",
    }


def test_meta_defaults_to_recurrence_full_k1():
    # tgp_btlm は latest_meta 未登録 → 安全既定 recurrence / full / K=1。
    meta = latest_meta("tgp_btlm", _VARIANT, _params())
    assert meta.archetype == "recurrence"  # 安全既定（window を full で吸収）
    assert meta.min_window is None          # full（tail せず全件）
    assert meta.trailing_k == 1


def test_all_series_are_line_kind():
    # tgp_btlm は line 系列のみ（horizontal_line なし）→ frontend routing = "latest"。
    adapter = IndicatorComputeAdapter()
    df = _ohlcv(100)
    series = full_compute(adapter, "tgp_btlm", _VARIANT, df, _params())
    assert series, "series should not be empty"
    assert all(s["kind"] == "line" for s in series)


def test_latest_line_tail_equals_full_tail_exact():
    # 最重要不変条件: latest の各 line data[-K:] が full の対応 data[-K:] と float 完全一致。
    adapter = IndicatorComputeAdapter()
    df = _ohlcv(100)
    params = _params()
    k = latest_meta("tgp_btlm", _VARIANT, params).trailing_k
    assert k == 1

    full = full_compute(adapter, "tgp_btlm", _VARIANT, df, dict(params))
    latest = latest_compute(adapter, "tgp_btlm", _VARIANT, df, dict(params))

    assert latest, "latest series should not be empty"
    full_by_name = {s["name"]: s for s in full}
    for s in latest:
        assert s["kind"] == "line"
        f = full_by_name[s["name"]]
        # latest 各系列は末尾 K 点に切られている。
        assert len(s["data"]) <= k
        # 末尾 K 点が full の末尾 K 点と float 完全一致（time/value とも）。
        assert s["data"] == f["data"][-k:]


def test_latest_path_runs_without_error():
    # latest 経路がエラーなく走り、line 系列を返す。
    adapter = IndicatorComputeAdapter()
    df = _ohlcv(100)
    latest = latest_compute(adapter, "tgp_btlm", _VARIANT, df, _params())
    assert isinstance(latest, list)
    assert all(s["kind"] == "line" for s in latest)
