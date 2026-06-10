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
