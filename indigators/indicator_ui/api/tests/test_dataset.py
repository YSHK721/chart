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
