"""dataset（adapter/compute/dataset.py）の検証 — ホワイトリスト解決と candles 供給。

設計入力: 内部設計書 §6.3（candles JSON: time=UNIX 秒・解像度非依存）、§7.3（datasetRef
ホワイトリスト・生パス直送/パストラバーサル拒否）。

検証観点:
  - is_known: 既知キー True / 未知キー・生パス False。
  - load_candles: candles JSON 形（time/open/high/low/close）、time は int（UNIX 秒）、
    time 昇順、open/high/low/close は float。
"""

from __future__ import annotations

from adapter.compute import dataset


# --------------------------------------------------------------------------- #
# is_known（§7.3 ホワイトリスト）
# --------------------------------------------------------------------------- #
def test_is_known_returns_true_for_whitelisted_sample():
    # Arrange / Act / Assert
    assert dataset.is_known("sample") is True


def test_is_known_returns_false_for_unknown_ref():
    assert dataset.is_known("unknown_dataset") is False


def test_is_known_returns_false_for_path_traversal_string():
    # 生パス直送（パストラバーサル）はホワイトリストに無いため False。
    assert dataset.is_known("../../../etc/passwd") is False


def test_is_known_returns_false_for_none():
    assert dataset.is_known(None) is False


# --------------------------------------------------------------------------- #
# load_candles（§6.3 candles JSON・解像度非依存 UNIX 秒）
# --------------------------------------------------------------------------- #
def test_load_candles_returns_ohlc_points_with_int_unix_time():
    # Act
    candles = dataset.load_candles("sample")
    # Assert
    assert isinstance(candles, list)
    assert len(candles) > 0
    first = candles[0]
    assert set(first.keys()) == {"time", "open", "high", "low", "close"}
    assert isinstance(first["time"], int)  # UNIX 秒（解像度非依存）
    for k in ("open", "high", "low", "close"):
        assert isinstance(first[k], float)


def test_load_candles_time_is_strictly_increasing():
    # candles は time 昇順（lightweight-charts の要件）。
    candles = dataset.load_candles("sample")
    times = [c["time"] for c in candles]
    assert times == sorted(times)
    assert len(set(times)) == len(times)  # 重複なし


def test_load_candles_first_point_matches_sample_csv_2010_06_29():
    # サンプル CSV 先頭行（2010-06-29, open=1.2667）を解像度非依存変換で照合。
    candles = dataset.load_candles("sample")
    first = candles[0]
    # 2010-06-29 00:00:00 UTC = 1277769600（int(pd.Timestamp("2010-06-29").timestamp())）。
    assert first["time"] == 1277769600
    assert first["open"] == 1.2667


# --------------------------------------------------------------------------- #
# 時間足（timeframe）— 1 分足原子から resample（§チャート表示時間選択）
# --------------------------------------------------------------------------- #
def test_is_known_timeframe_accepts_whitelist_and_rejects_unknown():
    # ホワイトリスト（1m..1M）は True、未知コード・None は False。
    assert dataset.is_known_timeframe("1m") is True
    assert dataset.is_known_timeframe("1D") is True
    assert dataset.is_known_timeframe("1W") is True
    assert dataset.is_known_timeframe("9z") is False
    assert dataset.is_known_timeframe(None) is False


def test_load_candles_timeframe_none_is_passthrough_backward_compat():
    # timeframe 省略は原子そのまま（既存挙動・後方互換）。
    base = dataset.load_candles("sample")
    same = dataset.load_candles("sample", None)
    assert len(same) == len(base)
    assert same[0] == base[0]


def test_load_candles_weekly_resample_aggregates_ohlc_from_daily_base():
    # 日足 sample を週足へ resample。週足本数は日足より少なく、high=週内最大を満たす。
    daily = dataset.load_candles("sample")
    weekly = dataset.load_candles("sample", "1W")
    assert 0 < len(weekly) < len(daily)
    # 週足は OHLC 形・time 昇順・int UNIX 秒を維持する。
    assert set(weekly[0].keys()) == {"time", "open", "high", "low", "close"}
    times = [c["time"] for c in weekly]
    assert times == sorted(times)
    # 先頭週の high は、その週に含まれる日足 high の最大（集約の正しさ）。
    first_week_end = weekly[0]["time"]
    in_first_week = [c for c in daily if c["time"] <= first_week_end]
    assert weekly[0]["high"] == max(c["high"] for c in in_first_week)


def test_load_candles_limit_returns_recent_n_bars():
    # limit=N は直近 N 本に制限する（§配信設計: 直近 N 本）。末尾一致を確認。
    full = dataset.load_candles("sample", "1D")
    tail = dataset.load_candles("sample", "1D", 5)
    assert len(tail) == 5
    assert tail == full[-5:]


def test_resample_ohlc_none_rule_returns_same_object():
    # rule=None は無変換（原子そのもの）で同一オブジェクトを返す。
    df = dataset.load_dataframe("sample")
    assert dataset.resample_ohlc(df, None) is df


# --------------------------------------------------------------------------- #
# CSV mtime 検知キャッシュ（最内 _load_base_dataframe の1段のみ・有界）
#   設計入力: ライブ更新（1 分間隔）で CSV が更新されたら再読込する。公開シグネチャ
#   （load_candles / load_dataframe）は不変のまま、mtime 変化を全段貫通させる。
# --------------------------------------------------------------------------- #
import csv as _csv

# 決定論的な tmp CSV（loader が要求する open/high/low/close + date 列）。
_CSV_HEADER = ("date", "open", "high", "low", "close")


def _write_csv(path, rows):
    # rows: [(date, open, high, low, close), ...] を CSV へ書き出す。
    with open(path, "w", newline="") as fh:
        w = _csv.writer(fh)
        w.writerow(_CSV_HEADER)
        w.writerows(rows)


def _register_tmp_ref(monkeypatch, ref, path):
    # ホワイトリストへ tmp ref を一時登録する（テスト終了で復元）。手書き mtime
    #   キャッシュもクリアして前テストの残留を断つ。
    monkeypatch.setitem(dataset.DATASET_WHITELIST, ref, path)
    dataset._BASE_CACHE.clear()


def test_load_candles_reflects_new_content_after_csv_mtime_changes(tmp_path, monkeypatch):
    # CSV 生成→取得→CSV 上書きで mtime 変化→再取得で新内容が反映（無効化が全段貫通）。
    # Arrange
    csv_path = tmp_path / "live.csv"
    _write_csv(csv_path, [("2020-01-01", 10.0, 12.0, 9.0, 11.0)])
    _register_tmp_ref(monkeypatch, "_tmp_live", csv_path)
    first = dataset.load_candles("_tmp_live")
    assert first[-1]["close"] == 11.0
    # Act: CSV を上書きし mtime を確実に進める（os.utime で決定論化）。
    _write_csv(csv_path, [("2020-01-01", 10.0, 12.0, 9.0, 11.0),
                          ("2020-01-02", 11.0, 20.0, 10.0, 19.0)])
    import os as _os
    st = _os.stat(csv_path)
    _os.utime(csv_path, ns=(st.st_atime_ns, st.st_mtime_ns + 1_000_000_000))
    # Assert: 再取得で新しい末尾足（close=19.0）が反映される（mtime キーで無効化）。
    second = dataset.load_candles("_tmp_live")
    assert second[-1]["close"] == 19.0
    assert len(second) == len(first) + 1


def test_load_candles_serves_cached_when_mtime_unchanged(tmp_path, monkeypatch):
    # mtime 不変なら再読込しない（CSV を消しても直前結果が返る＝キャッシュヒット）。
    # Arrange
    csv_path = tmp_path / "cached.csv"
    _write_csv(csv_path, [("2020-01-01", 10.0, 12.0, 9.0, 11.0)])
    _register_tmp_ref(monkeypatch, "_tmp_cached", csv_path)
    first = dataset.load_candles("_tmp_cached")
    # Act: CSV を物理削除する（mtime 取得不能＝再読込が走れば例外/空になる）。
    csv_path.unlink()
    # Assert: 直前結果が返る（mtime を取りに行かずキャッシュヒットしている）。
    second = dataset.load_candles("_tmp_cached")
    assert second == first


def test_base_cache_holds_single_entry_per_ref_after_repeated_updates(tmp_path, monkeypatch):
    # 同一 ref を複数回更新しても内部保持が 1 エントリで増えない（有界・サイズ検証）。
    # Arrange
    csv_path = tmp_path / "bounded.csv"
    _write_csv(csv_path, [("2020-01-01", 10.0, 12.0, 9.0, 11.0)])
    _register_tmp_ref(monkeypatch, "_tmp_bounded", csv_path)
    import os as _os
    # Act: 同一 ref を 3 回 mtime 変化させて取得する。
    for i in range(3):
        _write_csv(csv_path, [("2020-01-01", 10.0, 12.0, 9.0, 11.0 + i)])
        st = _os.stat(csv_path)
        _os.utime(csv_path, ns=(st.st_atime_ns, st.st_mtime_ns + (i + 1) * 1_000_000_000))
        dataset.load_candles("_tmp_bounded")
    # Assert: ref ごとに最新 mtime の 1 エントリのみ保持（旧 mtime は破棄され増えない）。
    assert len(dataset._BASE_CACHE) == 1
    assert "_tmp_bounded" in dataset._BASE_CACHE


# --------------------------------------------------------------------------- #
# reader 耐性（torn-read フォールバック・🟡-1）
#   ライブ更新の writer が CSV を非アトミックに追記中、末尾行が途中の torn-read になり
#   pandas が解析失敗しうる。失敗をキャッシュへ焼かず直前の良好 df を返す（不正配信防止）。
# --------------------------------------------------------------------------- #
def test_load_base_dataframe_falls_back_to_cached_on_torn_read(tmp_path, monkeypatch):
    import os as _os
    import pandas as _pd
    csv_path = tmp_path / "torn.csv"
    _write_csv(csv_path, [("2020-01-01", 10.0, 12.0, 9.0, 11.0)])
    _register_tmp_ref(monkeypatch, "_tmp_torn", csv_path)
    good = dataset.load_candles("_tmp_torn")  # 良好 df をキャッシュへ
    assert good[-1]["close"] == 11.0
    # mtime を進めて cache-miss を起こしつつ、次の CSV 読込を torn-read で失敗させる。
    st = _os.stat(csv_path)
    _os.utime(csv_path, ns=(st.st_atime_ns, st.st_mtime_ns + 1_000_000_000))

    class _RaisingLoader:
        def load_ohlc_csv(self, *a, **k):
            raise _pd.errors.ParserError("Error tokenizing data (torn last line)")

    monkeypatch.setattr(dataset, "_load_loader", lambda: _RaisingLoader())
    # Act: 読込失敗でも直前の良好キャッシュを返す（例外を出さず・不正データを配信しない）。
    served = dataset.load_candles("_tmp_torn")
    # Assert: stale（直前）内容が返り、キャッシュは汚染されない。
    assert served == good
    assert float(dataset._BASE_CACHE["_tmp_torn"][1]["close"].iloc[-1]) == 11.0


def test_load_base_dataframe_raises_on_read_error_without_prior_cache(tmp_path, monkeypatch):
    import pandas as _pd
    import pytest
    csv_path = tmp_path / "nocache.csv"
    _write_csv(csv_path, [("2020-01-01", 10.0, 12.0, 9.0, 11.0)])
    _register_tmp_ref(monkeypatch, "_tmp_nocache", csv_path)  # キャッシュをクリア

    class _RaisingLoader:
        def load_ohlc_csv(self, *a, **k):
            raise _pd.errors.ParserError("torn")

    monkeypatch.setattr(dataset, "_load_loader", lambda: _RaisingLoader())
    # 良好キャッシュが無い状態の読込失敗はフォールバック先が無く送出する（隠蔽しない）。
    with pytest.raises(_pd.errors.ParserError):
        dataset.load_candles("_tmp_nocache")


def test_sample_candles_behavior_unchanged_with_mtime_cache(monkeypatch):
    # sample（静的）の既存挙動不変（先頭足が従来どおり 2010-06-29 / open=1.2667）。
    dataset._BASE_CACHE.clear()
    candles = dataset.load_candles("sample")
    assert candles[0]["time"] == 1277769600
    assert candles[0]["open"] == 1.2667
