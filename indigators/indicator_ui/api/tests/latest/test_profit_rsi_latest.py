"""Stage B 検証: profit_rsi を Latest 増分計算フレームワークへ分類＋一致検証する。

分類（仕様 §4-0）:
  profit_rsi の出力（add_rsi）はすべて line で、次の 3 系列群からなる:
    - rsi 線（PF_LINE 'rsi'）            : iRSI（Wilder RSI）。core.compute_rsi は
                                          seed（i==period）→ Wilder 平滑
                                          pos[i]=(pos[i-1]*(period-1)+up)/period の **再帰**
                                          （buf[i] が buf[i-1] に依存）。
    - 正常帯 2 本（動的名 'rsi_q{pct}'）  : 当該バー除外の因果ローリング分位（窓 window_n）。
    - 外れ値水準 4 本（'rsi_evq_ext_*' / 'rsi_gpd_*'）: 閾値超過エピソードの経験的分位 /
                                          GPD 外挿。**先頭からの観測列の累積**に依存する。
  rsi（Wilder）は先頭シードからの再帰、外れ値水準は先頭からの観測累積のため、
  指標全体としては full 必須＝archetype は **recurrence**（安全既定と一致）。

系列 kind → frontend routing:
  catalog def.series の kind 群 = {line} のみ（σ 水平線は因果ローリング水準へ置換済み）。

不変条件:
  latest_meta は profit_rsi を明示登録していないため安全既定
  LatestMeta("recurrence", None, 1) で解決される（min_window=None＝full）。
  latest_dispatch._trail は line/histogram を末尾K切りする。full フォールバック
  （tail せず全件計算）のため、全 line 系列で data[-K:] が full の data[-K:] と
  float 完全一致する。→ K=1 で末尾 1 点一致。

import 規約: conftest.py が api/ を sys.path へ追加済み。既存 src は read-only。
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from adapter.compute import IndicatorComputeAdapter
from adapter.compute.latest_meta import latest_meta
from adapter.compute.latest_dispatch import full_compute, latest_compute

# 本指標の compute_id / variant（catalog def＝単一 variant）。
_COMPUTE_ID = "profit_rsi"
_VARIANTS = ("default",)

# trail 対象 kind（latest_dispatch._TRIMMABLE_KINDS と同義）。
_TRIMMABLE_KINDS = ("line", "histogram")


def _ohlcv(n: int = 400) -> pd.DataFrame:
    """昇順 OHLCV（date 含む）。水準（正常帯・外れ値）が定義される長さを満たす合成波形。

    既存 test_latest_compute._ohlcv と同流儀。add_rsi は open/high/low/close と
    time/date 列を要求する（volume は不要だが既存流儀に合わせ含める）。
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
            "volume": np.full(n, 1000.0) + np.arange(n, dtype=float),
        }
    )


def _params() -> dict:
    """catalog 既定（rsi_period=6, apply=5, window_n=500, q 0.10/0.90/0.99, K=50）。

    N=100 本の合成データでも水準が定義されるよう window_n だけ小さくする（既定 500 では
    正常帯が warm-up のまま＝水準が全 NaN になり、系列の存在自体を検証できないため）。
    """
    return {"rsi_period": 6, "apply": 5, "window_n": 20,
            "q_low": 0.10, "q_high": 0.90, "q_out": 0.99, "k_events": 50}


# =========================================================================== #
# 分類: archetype（recurrence・安全既定）と frontend routing（full）
# =========================================================================== #
def test_series_kinds_are_all_line():
    # 出力 kind 群 = {line} のみ（rsi 線＋正常帯＋外れ値水準。すべて時系列）。
    adapter = IndicatorComputeAdapter()
    df = _ohlcv(400)
    for variant in _VARIANTS:
        series = full_compute(adapter, _COMPUTE_ID, variant, df, _params())
        kinds = {s["kind"] for s in series}
        assert kinds == {"line"}, f"{variant}: {kinds}"
        for s in series:
            assert "data" in s and "lines" not in s
        names = {s["name"] for s in series}
        assert "rsi" in names
        assert {"rsi_evq_ext_hi", "rsi_evq_ext_lo", "rsi_gpd_hi", "rsi_gpd_lo"} <= names


def test_meta_resolves_to_safe_default_recurrence_full_k1():
    # 未登録 → 安全既定 recurrence/full/K=1（Wilder RSI＋観測累積の水準で full 必須・正しい）。
    meta = latest_meta(_COMPUTE_ID, "default", _params())
    assert meta.archetype == "recurrence"  # 安全既定（明示登録なし・full フォールバック）
    assert meta.min_window is None
    assert meta.trailing_k == 1


# =========================================================================== #
# 不変条件: latest 末尾K == full 末尾K（line は float 完全一致・水平線は全件）
# =========================================================================== #
def test_latest_line_tail_equals_full_tail_exact():
    adapter = IndicatorComputeAdapter()
    df = _ohlcv(400)
    for variant in _VARIANTS:
        params = _params()
        k = latest_meta(_COMPUTE_ID, variant, params).trailing_k
        assert k == 1

        full = full_compute(adapter, _COMPUTE_ID, variant, df, dict(params))
        latest = latest_compute(adapter, _COMPUTE_ID, variant, df, dict(params))

        # latest 経路がエラーなく走り、非空であること。
        assert latest, f"{variant}: latest series should not be empty"

        full_by_name = {s["name"]: s for s in full}
        for s in latest:
            f = full_by_name[s["name"]]
            if s["kind"] in _TRIMMABLE_KINDS:
                # 末尾 K 点に切られ、full の末尾 K 点と float 完全一致（time/value 完全一致）。
                assert len(s["data"]) <= k
                assert s["data"] == f["data"][-k:], f"{variant}/{s['name']}"
            else:
                # horizontal_line は latest でも全件（切られない）。
                assert s["kind"] == "horizontal_line"
                assert len(s["lines"]) == len(f["lines"])


def test_latest_lines_are_trimmed_to_tail():
    # full フォールバック（min_window=None）かつ K=1。全 line が末尾 1 点に切られ、
    # その 1 点は full の末尾と完全一致する（NaN 区間は emit 側で除外済み）。
    adapter = IndicatorComputeAdapter()
    df = _ohlcv(400)
    full = full_compute(adapter, _COMPUTE_ID, "default", df, _params())
    latest = latest_compute(adapter, _COMPUTE_ID, "default", df, _params())

    full_by_name = {s["name"]: s for s in full}
    for s in latest:
        f = full_by_name[s["name"]]
        assert s["kind"] == "line"
        assert len(s["data"]) == 1
        assert s["data"][-1] == f["data"][-1]


def test_band_levels_are_causal_and_inside_rsi_bounds():
    # 水準はすべて [0,100] の内側（余地割合スケールの構成上の不変条件）。
    adapter = IndicatorComputeAdapter()
    df = _ohlcv(400)
    series = full_compute(adapter, _COMPUTE_ID, "default", df, _params())
    for s in series:
        values = [pt["value"] for pt in s["data"]]
        assert min(values) >= 0.0 and max(values) <= 100.0, s["name"]
