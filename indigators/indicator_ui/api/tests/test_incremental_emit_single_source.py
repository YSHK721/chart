"""増分計算 emit の単一実装と時刻正規化の等価性を固定する（ISSUE-273）。

かつて末尾 K 点の組み立ては 5 箇所（btlm_trail / marod / tickvol / profit_rsi の
``_tail_points`` と moving_averages のインライン）に独立実装され、**時刻正規化の規約が
2 通りに分岐**していた（prepare 時に int64 化 / emit 時に UNIX 秒化）。
どちらも同じ値になるが「どちらが規約か」がコードから読めず、規約変更時に 5 箇所を
同時に直す必要があった。正規化を ``_emit`` 1 箇所へ寄せた。

本テストが固定すること:
  (1) 新実装が **両方の旧規約と同値**（int64 済み / 生 datetime のどちらを渡しても同じ）
  (2) 末尾 K 点の規約（NaN 除外・昇順・k 上限・形成中バーの last 優先）
  (3) 増分器モジュールが ``_tail_points`` を再実装していない（複製の再発防止）
"""
from __future__ import annotations

import re
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from adapter.compute.fake_chart import to_unix_seconds
from adapter.compute.incremental._emit import _unix_seconds, tail_points, tail_points_offset

_INCREMENTAL = Path(__file__).resolve().parents[1] / "adapter" / "compute" / "incremental"


def _both_time_forms(n: int = 50):
    """同一時刻列を「int64 UNIX 秒」と「生 datetime」の 2 形で返す（旧 2 規約の再現）。"""
    idx = pd.date_range("2026-08-01", periods=n, freq="h", tz=None)
    raw = np.asarray(pd.Series(idx))
    as_int = np.asarray(idx.to_numpy().astype("datetime64[s]").astype("int64"))
    return as_int, raw


def test_normalization_matches_both_legacy_conventions():
    """新実装が旧 2 規約（int(times[i]) / _to_unix_seconds(times[i])）の双方と同値。"""
    as_int, raw = _both_time_forms()
    for i in range(len(as_int)):
        assert _unix_seconds(as_int[i]) == int(as_int[i])          # 旧: int64 済み経路
        assert _unix_seconds(raw[i]) == to_unix_seconds(raw[i])    # 旧: 生 datetime 経路
        assert _unix_seconds(as_int[i]) == _unix_seconds(raw[i])   # 正規化位置に依らない


def test_tail_points_drops_nan_and_returns_ascending_k_points():
    as_int, _ = _both_time_forms(6)
    confirmed = np.array([1.0, float("nan"), 3.0, 4.0, float("nan"), 0.0])
    got = tail_points(confirmed, last=9.0, times=as_int, n=6, k=3)
    # i=5 は last（9.0）、i=4 は NaN で除外、i=3→4.0、i=2→3.0。昇順で 3 点。
    assert [p["value"] for p in got] == [3.0, 4.0, 9.0]
    assert [p["time"] for p in got] == [int(as_int[2]), int(as_int[3]), int(as_int[5])]


def test_tail_points_respects_k_and_handles_all_nan():
    as_int, _ = _both_time_forms(4)
    assert tail_points(np.array([1.0, 2.0, 3.0]), 4.0, as_int, 4, 2)[0]["value"] == 3.0
    allnan = np.array([float("nan")] * 3)
    assert tail_points(allnan, float("nan"), as_int, 4, 3) == []


def test_tail_points_offset_places_values_at_shifted_times():
    as_int, _ = _both_time_forms(6)
    buf = np.array([1.0, 2.0, 3.0, 4.0, 5.0, 6.0])
    got = tail_points_offset(buf, as_int, i_high=3, i_low=1, offset=1, k=2)
    assert [p["value"] for p in got] == [3.0, 4.0]
    assert [p["time"] for p in got] == [int(as_int[3]), int(as_int[4])]


@pytest.mark.parametrize(
    "module", sorted(p.name for p in _INCREMENTAL.glob("*.py") if p.name != "_emit.py"))
def test_incrementers_do_not_reimplement_tail_points(module):
    """増分器が末尾 K 点の組み立てを再実装していない（_emit へ委譲する）。"""
    src = (_INCREMENTAL / module).read_text(encoding="utf-8")
    assert "def _tail_points" not in src, f"{module} が _tail_points を再実装しています"
    # 時刻正規化の直書き（int(times[...]) / _to_unix_seconds(times[...])）も禁じる。
    body = re.sub(r"^\s*#.*$", "", src, flags=re.M)
    assert not re.search(r"int\(\s*(req\.)?times\[", body), f"{module} が時刻を直接 int 化しています"
    assert not re.search(r"_to_unix_seconds\(\s*(req\.)?times\[", body), \
        f"{module} が時刻正規化を直書きしています"
