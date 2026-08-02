"""tickvol（ティックボリューム）の /compute 境界検証。

指標本体（列の素通し）と外れ値水準は ``indigators/tickvol/tests`` が固定し、増分計算は
``test_tickvol_incremental.py`` が固定する。本ファイルは **結線**を固定する:
  - CALL_BINDING に登録され、既存 adapter.compute 経由で histogram 1 + line 3 が返る。
  - 系列名が front（web/js/usecase/catalog.js の SeriesDef.seriesName）と一致する。
  - 増分計算（archetype="incremental"）を宣言している。
  - volume 欠落は ``missing_column`` へ翻訳される（OHLC 事前検査を通過してからの KeyError）。

import 規約: conftest.py が api/ を sys.path へ追加済み。既存 src は read-only。
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from adapter.compute import ComputeError, IndicatorComputeAdapter
from adapter.compute.latest_dispatch import full_compute, latest_compute
from adapter.compute.latest_meta import latest_meta

_SERIES_NAME = "tickvol"  # front SeriesDef.seriesName と同値（F3 照合）。


def _ohlcv(n: int = 200) -> pd.DataFrame:
    idx = pd.date_range("2026-01-05 00:00:00", periods=n, freq="5min")
    base = 10.0 + np.sin(np.linspace(0.0, 6.0 * np.pi, n)) * 3.0
    return pd.DataFrame(
        {
            "open": base,
            "high": base + 1.0,
            "low": base - 1.0,
            "close": base + 0.5,
            # 決定論的かつ点ごとに異なる tick 数（末尾一致の検証で値がずれれば落ちる）。
            "volume": (np.arange(n, dtype=float) % 97.0) * 3.0 + 1.0,
        },
        index=idx,
    )


# front（web/js/usecase/catalog.js の SeriesDef）と一致させる系列名の全集合（emit 順）。
#   帯は分位依存の動的名（既定 q_low=0.10 / q_high=0.90）。
#   回帰トレンド系（tickvol_trend_*）は ISSUE-244 で UI から外した。
_SERIES_NAMES = (
    "tickvol", "tickvol_q10", "tickvol_q90",
    "tickvol_evq_med_hi", "tickvol_evq_ext_hi", "tickvol_gpd_hi",
)


def test_compute_returns_the_histogram_bands_and_levels():
    adapter = IndicatorComputeAdapter()
    df = _ohlcv(400)
    series = adapter.compute("tickvol", "default", df, {})

    assert [s["name"] for s in series] == list(_SERIES_NAMES)
    kinds = {s["name"]: s["kind"] for s in series}
    assert kinds[_SERIES_NAME] == "histogram"
    assert all(kinds[n] == "line" for n in _SERIES_NAMES[1:])


def test_band_series_names_follow_the_quantile_pair():
    # 命名は btlm_trail_q{pct} と対称（分位値そのものが名前に出る）。
    adapter = IndicatorComputeAdapter()
    series = adapter.compute("tickvol", "default", _ohlcv(400),
                             {"q_low": 0.05, "q_high": 0.95})
    names = [s["name"] for s in series]
    assert "tickvol_q5" in names and "tickvol_q95" in names
    assert len(names) == len(set(names))


def test_trend_series_are_not_emitted_anymore():
    # ISSUE-244: 回帰トレンド（btlm_trail 仕様）は UI から外した。計算は
    #   indigators/tickvol/src/trend.py にアーカイブとして残るが、結線からは出ない。
    adapter = IndicatorComputeAdapter()
    names = [s["name"] for s in adapter.compute("tickvol", "default", _ohlcv(400), {})]
    assert not any(n.startswith("tickvol_trend") for n in names)


def test_histogram_values_are_the_source_volume_unchanged():
    adapter = IndicatorComputeAdapter()
    df = _ohlcv(120)
    body = next(s for s in adapter.compute("tickvol", "default", df, {})
                if s["name"] == _SERIES_NAME)
    assert len(body["data"]) == len(df)
    assert [p["value"] for p in body["data"]] == df["volume"].tolist()


def test_latest_meta_declares_incremental_computation():
    # 水準は確定イベント全体に依存し有限 tail を取れない＝ISSUE-233 と同じ解を採る。
    meta = latest_meta("tickvol", "default", {})
    assert meta.archetype == "incremental"
    assert meta.incremental == "tickvol"
    assert meta.trailing_k == 1


def test_missing_volume_is_translated_to_missing_column():
    adapter = IndicatorComputeAdapter()
    df = _ohlcv(30).drop(columns=["volume"])
    with pytest.raises(ComputeError) as exc:
        adapter.compute("tickvol", "default", df, {})
    assert exc.value.error_type == "missing_column"


def test_rows_without_volume_do_not_produce_points():
    # リプレイの形成中バー（OHLC のみ・volume 無し）は点を立てない＝値を捏造しない。
    adapter = IndicatorComputeAdapter()
    df = _ohlcv(50)
    df.loc[df.index[-1], "volume"] = np.nan
    series = adapter.compute("tickvol", "default", df, {})
    assert len(series[0]["data"]) == len(df) - 1
