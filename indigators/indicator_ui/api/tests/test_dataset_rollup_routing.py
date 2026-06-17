"""dataset.load_dataframe の jp225_m1 ルーティング検証（TDD: Red→Green）。

設計（公開シグネチャ不変・後方互換）:
  - ref=jp225_m1 かつ timeframe in (None,'1m') → tail_reader.read_tail（D-2: lookback_rows 安全上限適用）。
  - ref=jp225_m1 かつ上位足（5m..1M） → rollup_store.read(ref, tf)。
  - それ以外の ref（sample/jp225 日足等・小データ） → 従来経路（_load_base_dataframe + resample_ohlc）据置。

★実 284MB（jp225_m1.csv）は読まない。tail_reader / rollup_store を monkeypatch でスパイし、
jp225_m1 がどの経路へ分岐するかを「呼び出しの有無」で検証する（経路の正しさ）。sample 既存テスト
（test_dataset / test_dataset_resample_cache）は本変更で全緑維持する（別途 full suite で確認）。
"""

from __future__ import annotations

import pandas as pd

from adapter.compute import dataset


def _fake_df():
    idx = pd.date_range("2020-01-01 00:00:00", periods=3, freq="1min")
    return pd.DataFrame(
        {"open": [1.0, 2, 3], "high": [1.0, 2, 3], "low": [1.0, 2, 3],
         "close": [1.0, 2, 3], "volume": [1.0, 1, 1]},
        index=idx,
    )


# --------------------------------------------------------------------------- #
# jp225_m1 + 1m（None / '1m'）→ tail_reader 経由（rollup_store / base は使わない）
# --------------------------------------------------------------------------- #
def test_jp225_m1_atomic_routes_to_tail_reader(monkeypatch):
    calls = {"tail": 0, "rollup": 0}
    fake = _fake_df()

    def _spy_tail(csv_path, n_rows):
        calls["tail"] += 1
        return fake

    def _spy_rollup(ref, tf):
        calls["rollup"] += 1
        return fake

    monkeypatch.setattr(dataset.tail_reader, "read_tail", _spy_tail)
    monkeypatch.setattr(dataset.rollup_store, "read", _spy_rollup)
    # Act: 1m（None）と '1m' の両方。
    out_none = dataset.load_dataframe("jp225_m1", None)
    out_1m = dataset.load_dataframe("jp225_m1", "1m")
    # Assert: tail_reader 経由（rollup_store は呼ばない）。
    assert calls["tail"] == 2
    assert calls["rollup"] == 0
    assert out_none.equals(fake)
    assert out_1m.equals(fake)


def test_jp225_m1_tail_reader_applies_lookback_upper_bound(monkeypatch):
    # D-2: 1m 全件 tail で OOM 復活させない。read_tail へ渡す n_rows は有限の安全上限（>0）。
    seen = {}

    def _spy_tail(csv_path, n_rows):
        seen["n_rows"] = n_rows
        return _fake_df()

    monkeypatch.setattr(dataset.tail_reader, "read_tail", _spy_tail)
    dataset.load_dataframe("jp225_m1", "1m")
    # Assert: 有限の安全上限が適用される（全件読みではない・正の有限値）。
    assert isinstance(seen["n_rows"], int)
    assert 0 < seen["n_rows"] < 10_000_000


# --------------------------------------------------------------------------- #
# jp225_m1 + 上位足（5m..1M）→ rollup_store 経由（tail_reader / base は使わない）
# --------------------------------------------------------------------------- #
def test_jp225_m1_upper_timeframe_routes_to_rollup_store(monkeypatch):
    calls = {"tail": 0, "rollup": 0}
    fake = _fake_df()
    monkeypatch.setattr(dataset.tail_reader, "read_tail", lambda *a, **k: (calls.__setitem__("tail", calls["tail"] + 1), fake)[1])
    monkeypatch.setattr(dataset.rollup_store, "read", lambda ref, tf: (calls.__setitem__("rollup", calls["rollup"] + 1), fake)[1])

    for tf in ("5m", "15m", "1h", "4h", "1D", "1W", "1M"):
        dataset.load_dataframe("jp225_m1", tf)
    # Assert: 全上位足が rollup_store 経由（tail_reader は呼ばない・base resample しない）。
    assert calls["rollup"] == 7
    assert calls["tail"] == 0


def test_jp225_m1_upper_timeframe_passes_ref_and_tf_to_rollup_store(monkeypatch):
    seen = {}
    monkeypatch.setattr(dataset.rollup_store, "read", lambda ref, tf: (seen.update(ref=ref, tf=tf), _fake_df())[1])
    dataset.load_dataframe("jp225_m1", "1h")
    assert seen == {"ref": "jp225_m1", "tf": "1h"}


# --------------------------------------------------------------------------- #
# sample / jp225（小データ）→ 従来経路据置（rollup_store / tail_reader を経由しない）
# --------------------------------------------------------------------------- #
def test_sample_still_uses_legacy_base_path(monkeypatch):
    calls = {"tail": 0, "rollup": 0}
    monkeypatch.setattr(dataset.tail_reader, "read_tail", lambda *a, **k: (calls.__setitem__("tail", calls["tail"] + 1), _fake_df())[1])
    monkeypatch.setattr(dataset.rollup_store, "read", lambda ref, tf: (calls.__setitem__("rollup", calls["rollup"] + 1), _fake_df())[1])
    # Act: sample（小データ・静的）。
    daily = dataset.load_dataframe("sample")
    weekly = dataset.load_dataframe("sample", "1W")
    # Assert: 従来経路（tail_reader / rollup_store を経由しない）。先頭足は従来どおり。
    assert calls["tail"] == 0
    assert calls["rollup"] == 0
    assert len(daily) > 0
    assert len(weekly) > 0
