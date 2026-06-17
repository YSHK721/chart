"""rollup_store の検証（TDD: Red→Green）— 上位足ロールアップ CSV の解決・読込・mtime キャッシュ。

設計（dataset の _BASE_CACHE と同方式・単一真実源）:
  - path(ref, tf) -> <workspace>/marketdata/data/rollups/<ref>_<tf>.csv。
  - read(ref, tf) -> 末尾読み（tail_reader・_ROLLUP_TAIL_ROWS 上限）+ mtime キャッシュ（plain dict
    上書き有界）+ torn-read フォールバック。全件は読まない（応答時間・RSS 有界化）。

回帰観点（既存 dataset と同型・先行修正の非回帰を固定）:
  - mtime 変化で再読込 / 不変でキャッシュヒット / torn-read で直前キャッシュ維持 / 有界（1 エントリ）。
  - 末尾読み: 行数が上限超なら末尾 N 行のみ返す（全件読みしない）。
実ネット非依存（tmp CSV・monkeypatch）。実 284MB は読まない。
"""

from __future__ import annotations

import csv as _csv
import os as _os
import tracemalloc

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

    def _raise(*a, **k):
        raise _pd.errors.ParserError("torn last line")

    monkeypatch.setattr(rollup_store, "_read_tail_df", _raise)
    served = rollup_store.read("jp225_m1", "1h")
    # Assert: 直前の良好キャッシュを返し、汚染されない（不正データを配信しない）。
    assert served.equals(good)


def test_read_raises_on_torn_read_without_prior_cache(tmp_path, monkeypatch):
    _clear_cache()
    csv_path = tmp_path / "jp225_m1_1h.csv"
    _write_csv(csv_path, [_point(1, 11.0)])
    monkeypatch.setattr(rollup_store, "path", lambda ref, tf: csv_path)

    def _raise(*a, **k):
        raise _pd.errors.ParserError("torn")

    monkeypatch.setattr(rollup_store, "_read_tail_df", _raise)
    # 良好キャッシュが無い状態の読込失敗はフォールバック先が無く送出する（隠蔽しない）。
    with pytest.raises(_pd.errors.ParserError):
        rollup_store.read("jp225_m1", "1h")


# --------------------------------------------------------------------------- #
# 末尾読み: 上限超なら末尾 N 行のみ返す（全件読みしない・応答時間/RSS 有界化）
# --------------------------------------------------------------------------- #
def test_read_returns_only_tail_rows_when_exceeding_cap(tmp_path, monkeypatch):
    _clear_cache()
    csv_path = tmp_path / "jp225_m1_1h.csv"
    # 5 行のうち上限 3 行に制限 → 末尾 3 行（close=3,4,5）のみ返る（先頭 2 行は読まない）。
    _write_csv(csv_path, [_point(d, float(d)) for d in range(1, 6)])
    monkeypatch.setattr(rollup_store, "path", lambda ref, tf: csv_path)
    monkeypatch.setattr(rollup_store, "_ROLLUP_TAIL_ROWS", 3)
    df = rollup_store.read("jp225_m1", "1h")
    assert len(df) == 3
    assert [float(c) for c in df["close"]] == [3.0, 4.0, 5.0]
    # 末尾域は全件読みの末尾と一致（全件読みの代替として安全）。
    assert df.index.name == "date"


def test_read_tail_equals_full_read_tail_oracle(tmp_path, monkeypatch):
    # 正確性オラクル: 末尾読みは「全件読み .tail(cap)」と index/全列値で完全一致する
    #   （tail_reader の off-by-one・列/dtype ドリフトで末尾配信が崩れる退行を検知）。
    _clear_cache()
    csv_path = tmp_path / "jp225_m1_1h.csv"
    # 各列を行ごとに変えた決定論データ（定数列だとオラクルが緩むため）。
    rows = [
        (f"2020-02-{d:02d} 00:00:00", 10.0 + d, 20.0 + d, 5.0 + d, 12.0 + d, 100.0 + d)
        for d in range(1, 13)
    ]
    _write_csv(csv_path, rows)
    monkeypatch.setattr(rollup_store, "path", lambda ref, tf: csv_path)
    monkeypatch.setattr(rollup_store, "_ROLLUP_TAIL_ROWS", 4)
    got = rollup_store.read("jp225_m1", "1h")
    # オラクル: 独立に全件読みして date index 化し末尾 4 行を取る。
    full = _pd.read_csv(csv_path)
    full["date"] = _pd.to_datetime(full["date"])
    expected = full.set_index("date").tail(4)
    assert list(got.index) == list(expected.index)
    for col in ("open", "high", "low", "close", "volume"):
        assert [float(v) for v in got[col]] == pytest.approx([float(v) for v in expected[col]])


def test_read_peak_memory_is_bounded_by_cap_not_file_size(tmp_path, monkeypatch):
    # 性能退行ガード: read は cap 行ぶんのメモリしか使わない（ファイル全体に比例しない）。
    #   「全件読み→tail」へ戻す退行は rows 上限テストを通過してしまうため、メモリで別途固定する。
    #   実測: 400k 行で 末尾読み peak ~16MB / 全件読み→tail ~60MB。閾値 40MB はその間隙（非 flaky）。
    _clear_cache()
    csv_path = tmp_path / "jp225_m1_5m.csv"
    n = 400_000
    idx = _pd.date_range("2018-01-01", periods=n, freq="5min")
    _pd.DataFrame(
        {"date": idx, "open": 100.0, "high": 101.0, "low": 99.0, "close": 100.5, "volume": 10.0}
    ).to_csv(csv_path, index=False)
    monkeypatch.setattr(rollup_store, "path", lambda ref, tf: csv_path)
    tracemalloc.start()
    try:
        df = rollup_store.read("jp225_m1", "5m")
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    assert len(df) == rollup_store._ROLLUP_TAIL_ROWS  # cap で頭打ち（全件 40 万行を読まない）。
    peak_mb = peak / 1e6
    assert peak_mb < 40, (
        f"read peak {peak_mb:.0f}MB が上限 40MB 超過。全件読み（→tail）退行の疑い（O(file) 化）。"
    )
