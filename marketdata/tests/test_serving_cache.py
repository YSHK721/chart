"""serving_cache 分離の検証（ISSUE-094 🟡-7）。

供給時 mtime キャッシュ＋ロールアップ経路を serving_cache へ分離した後の不変条件を固定する:
  - 「キャッシュには生（未クランプ）を保存し、返却時にクランプする」（最重要）。
  - dataset の _BASE_CACHE / _RESAMPLE_CACHE は serving_cache の実体と同一オブジェクト（単一真実源）。
  - 依存注入（loader_factory / resample_fn）が呼び出し時の dataset 名前空間を反映する。
"""

from __future__ import annotations

import csv as _csv

import pandas as pd

from marketdata import dataset, serving_cache


def _write_csv(path, rows):
    with open(path, "w", newline="") as fh:
        w = _csv.writer(fh)
        w.writerow(("date", "open", "high", "low", "close"))
        w.writerows(rows)


# --- 単一真実源: dataset のキャッシュは serving_cache の実体 ---------------- #
def test_dataset_caches_are_serving_cache_objects():
    assert dataset._BASE_CACHE is serving_cache._BASE_CACHE
    assert dataset._RESAMPLE_CACHE is serving_cache._RESAMPLE_CACHE


# --- 不変条件: resample キャッシュは生（未クランプ）を保存し、返却時にクランプ --- #
def test_resample_cache_stores_raw_and_return_is_clamped(tmp_path, monkeypatch):
    # Arrange: 週内に 8/26 相当の外れ安値（low=15098・open/close ~42000）を含む日足 CSV。
    csv_path = tmp_path / "mkt_raw.csv"
    _write_csv(csv_path, [
        ("2025-08-25", 43076.97, 43199.07, 42527.94, 42650.00),
        ("2025-08-26", 42642.89, 42705.29, 15098.53, 42476.68),  # 外れ安値
        ("2025-08-27", 42481.76, 42626.97, 42268.89, 42343.92),
    ])
    monkeypatch.setitem(dataset.DATASET_WHITELIST, "_tmp_raw", csv_path)
    monkeypatch.setitem(dataset._OUTLIER_CLAMP_REFS_SET, "_tmp_raw", True)
    dataset._BASE_CACHE.clear()
    dataset._RESAMPLE_CACHE.clear()

    # Act
    weekly = dataset.load_candles("_tmp_raw", "1W")

    # Assert 1: 返却値はクランプ済（外れ安値 15098 は消え、>40000 台へ）。
    assert all(c["low"] > 40000.0 for c in weekly)
    # Assert 2: resample キャッシュに保存された df は **生**（未クランプ＝15098 が残る）。
    cached = dataset._RESAMPLE_CACHE[("_tmp_raw", "1W")][1]
    lm = {str(c).lower(): c for c in cached.columns}
    assert float(cached[lm["low"]].min()) == 15098.53


# --- 依存注入: resample_fn は呼び出し時の dataset 名前空間を反映する ---------- #
def test_resample_cached_uses_injected_resample_fn():
    calls = {"n": 0}
    base = pd.DataFrame(
        {"open": [1.0, 2.0], "high": [1.0, 2.0], "low": [1.0, 2.0], "close": [1.0, 2.0]},
        index=pd.date_range("2020-01-01", periods=2, freq="1D"),
    )

    def _fake_resample(df, rule):
        calls["n"] += 1
        return df

    serving_cache._RESAMPLE_CACHE.clear()
    serving_cache._BASE_CACHE.clear()
    out = serving_cache.resample_cached(
        "_inj", "1D", base, resample_fn=_fake_resample, rule="1D"
    )
    assert out is base
    assert calls["n"] == 1


# --- 依存注入: load_base_dataframe は注入 loader を使う ---------------------- #
def test_load_base_dataframe_uses_injected_loader_factory(tmp_path):
    csv_path = tmp_path / "inj.csv"
    _write_csv(csv_path, [("2020-01-01", 10.0, 12.0, 9.0, 11.0)])

    seen = {"factory": 0}

    class _Loader:
        def load_ohlc_csv(self, path, *, time_column):
            return pd.read_csv(path).set_index(time_column)

    def _factory():
        seen["factory"] += 1
        return _Loader()

    serving_cache._BASE_CACHE.clear()
    df = serving_cache.load_base_dataframe(
        "_inj_base", path=csv_path, loader_factory=_factory, time_column="date"
    )
    assert seen["factory"] == 1
    assert float(df["close"].iloc[-1]) == 11.0
