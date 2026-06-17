"""rollup_store の検証（TDD: Red→Green）— 上位足ロールアップ CSV の解決・読込・mtime キャッシュ。

設計（dataset の _BASE_CACHE と同方式・単一真実源）:
  - path(ref, tf) -> <workspace>/marketdata/data/rollups/<ref>_<tf>.csv。
  - read(ref, tf) -> loader 再利用 + mtime キャッシュ（plain dict 上書き有界）+ torn-read フォールバック。

回帰観点（既存 dataset と同型・先行修正の非回帰を固定）:
  - mtime 変化で再読込 / 不変でキャッシュヒット / torn-read で直前キャッシュ維持 / 有界（1 エントリ）。
実ネット非依存（tmp CSV・monkeypatch）。実 284MB は読まない。
"""

from __future__ import annotations

import csv as _csv
import os as _os

import pandas as _pd
import pytest

from adapter.compute import rollup_store

_CSV_HEADER = ("date", "open", "high", "low", "close", "volume")


def _write_csv(path, rows):
    with open(path, "w", newline="") as fh:
        w = _csv.writer(fh)
        w.writerow(_CSV_HEADER)
        w.writerows(rows)


def _advance_mtime(path):
    st = _os.stat(path)
    _os.utime(path, ns=(st.st_atime_ns, st.st_mtime_ns + 1_000_000_000))


def _point(day, close):
    return (f"2020-01-{day:02d} 00:00:00", 10.0, 12.0, 9.0, close, 100.0)


def _clear_cache():
    rollup_store._ROLLUP_CACHE.clear()


# --------------------------------------------------------------------------- #
# path（<workspace>/marketdata/data/rollups/<ref>_<tf>.csv）
# --------------------------------------------------------------------------- #
def test_path_resolves_under_marketdata_rollups_with_ref_tf_filename():
    p = rollup_store.path("jp225_m1", "1h")
    assert p.parts[-3:] == ("marketdata", "data", "rollups") or (
        p.parent.name == "rollups"
        and p.parent.parent.name == "data"
        and p.parent.parent.parent.name == "marketdata"
    )
    assert p.name == "jp225_m1_1h.csv"


# --------------------------------------------------------------------------- #
# read（loader 再利用・DataFrame・date index）
# --------------------------------------------------------------------------- #
def test_read_returns_dataframe_indexed_by_date(tmp_path, monkeypatch):
    _clear_cache()
    csv_path = tmp_path / "jp225_m1_1h.csv"
    _write_csv(csv_path, [_point(1, 11.0), _point(2, 12.0)])
    monkeypatch.setattr(rollup_store, "path", lambda ref, tf: csv_path)
    df = rollup_store.read("jp225_m1", "1h")
    assert list(df.columns) == ["open", "high", "low", "close", "volume"]
    assert df.index.name == "date"
    assert float(df["close"].iloc[-1]) == 12.0


# --------------------------------------------------------------------------- #
# mtime キャッシュ: 変化で再読込 / 不変でヒット / 有界
# --------------------------------------------------------------------------- #
def test_read_reflects_new_content_after_mtime_changes(tmp_path, monkeypatch):
    _clear_cache()
    csv_path = tmp_path / "jp225_m1_1h.csv"
    _write_csv(csv_path, [_point(1, 11.0)])
    monkeypatch.setattr(rollup_store, "path", lambda ref, tf: csv_path)
    first = rollup_store.read("jp225_m1", "1h")
    assert len(first) == 1
    # Act: 追記して mtime を進める。
    _write_csv(csv_path, [_point(1, 11.0), _point(2, 19.0)])
    _advance_mtime(csv_path)
    second = rollup_store.read("jp225_m1", "1h")
    # Assert: mtime 変化で再読込（新内容反映）。
    assert len(second) == 2
    assert float(second["close"].iloc[-1]) == 19.0


def test_read_serves_cached_when_mtime_unchanged(tmp_path, monkeypatch):
    _clear_cache()
    csv_path = tmp_path / "jp225_m1_1h.csv"
    _write_csv(csv_path, [_point(1, 11.0)])
    monkeypatch.setattr(rollup_store, "path", lambda ref, tf: csv_path)
    first = rollup_store.read("jp225_m1", "1h")
    # Act: CSV を物理削除（再読込が走れば例外/空になる）。
    csv_path.unlink()
    second = rollup_store.read("jp225_m1", "1h")
    # Assert: mtime 取得不能でも直前キャッシュを返す（ヒット）。
    assert second.equals(first)


def test_cache_holds_single_entry_per_ref_tf_after_repeated_updates(tmp_path, monkeypatch):
    _clear_cache()
    csv_path = tmp_path / "jp225_m1_1h.csv"
    _write_csv(csv_path, [_point(1, 11.0)])
    monkeypatch.setattr(rollup_store, "path", lambda ref, tf: csv_path)
    for i in range(3):
        _write_csv(csv_path, [_point(1, 11.0 + i)])
        _advance_mtime(csv_path)
        rollup_store.read("jp225_m1", "1h")
    # Assert: (ref,tf) ごと最新 mtime の 1 エントリのみ（mtime ごと増殖しない・有界）。
    assert len(rollup_store._ROLLUP_CACHE) == 1
    assert ("jp225_m1", "1h") in rollup_store._ROLLUP_CACHE


# --------------------------------------------------------------------------- #
# torn-read フォールバック（直前キャッシュ維持・既存 dataset と同型）
# --------------------------------------------------------------------------- #
def test_read_falls_back_to_cached_on_torn_read(tmp_path, monkeypatch):
    _clear_cache()
    csv_path = tmp_path / "jp225_m1_1h.csv"
    _write_csv(csv_path, [_point(1, 11.0)])
    monkeypatch.setattr(rollup_store, "path", lambda ref, tf: csv_path)
    good = rollup_store.read("jp225_m1", "1h")
    assert float(good["close"].iloc[-1]) == 11.0
    # mtime を進めて cache-miss を起こしつつ、次の読込を torn-read で失敗させる。
    _advance_mtime(csv_path)

    class _RaisingLoader:
        def load_ohlc_csv(self, *a, **k):
            raise _pd.errors.ParserError("torn last line")

    monkeypatch.setattr(rollup_store, "_load_loader", lambda: _RaisingLoader())
    served = rollup_store.read("jp225_m1", "1h")
    # Assert: 直前の良好キャッシュを返し、汚染されない（不正データを配信しない）。
    assert served.equals(good)


def test_read_raises_on_torn_read_without_prior_cache(tmp_path, monkeypatch):
    _clear_cache()
    csv_path = tmp_path / "jp225_m1_1h.csv"
    _write_csv(csv_path, [_point(1, 11.0)])
    monkeypatch.setattr(rollup_store, "path", lambda ref, tf: csv_path)

    class _RaisingLoader:
        def load_ohlc_csv(self, *a, **k):
            raise _pd.errors.ParserError("torn")

    monkeypatch.setattr(rollup_store, "_load_loader", lambda: _RaisingLoader())
    # 良好キャッシュが無い状態の読込失敗はフォールバック先が無く送出する（隠蔽しない）。
    with pytest.raises(_pd.errors.ParserError):
        rollup_store.read("jp225_m1", "1h")
