"""latest_compute の入力境界（速度不変条件）の回帰テスト。

Latest（末尾K）計算の速度根拠は「確定済みの過去足を再計算しない」こと。その機構が
``latest_compute`` の ``df.tail(min_window)``（latest_dispatch.py:50）である:
  * meta.min_window が有限 M なら adapter へ渡す df は末尾 M 本に限定される（full 全体でない）。
  * meta.min_window が None なら full（全件）で adapter を呼ぶ（厳密一致設計の既定・安全側）。

本テストはこの入力境界を fake adapter で直接固定する。現行の登録指標は厳密一致設計
（option 1）により全て min_window=None（full）だが、本機構が壊れると「窓系を tail で
高速化する」将来拡張が無言で full へ退行するため、機構の契約をここで固定する。

import 規約: api/tests/conftest.py が api/ を sys.path へ追加済み。
"""

from __future__ import annotations

import pandas as pd

from adapter.compute import latest_dispatch
from adapter.compute.latest_meta import LatestMeta


class _RecordingAdapter:
    """adapter.compute が受け取った df の長さだけ記録する fake（純機構検証用）。"""

    def __init__(self) -> None:
        self.seen_len: int | None = None

    def compute(self, compute_id, variant, df, params):
        self.seen_len = len(df)
        # _trail が触る line 系列を 1 本返す（末尾K切りの経路も通す）。
        return [{"name": "x", "kind": "line", "data": [{"time": 1, "value": 1.0}]}]


def _df(n: int = 200) -> pd.DataFrame:
    idx = pd.date_range("2024-01-01", periods=n, freq="h")
    return pd.DataFrame({"close": [float(i) for i in range(n)]}, index=idx)


def test_latest_compute_feeds_min_window_tail_to_adapter(monkeypatch):
    # min_window=50（有限）なら adapter 入力は full(200) でなく tail(50) に境界化される。
    df = _df(200)
    monkeypatch.setattr(
        latest_dispatch, "latest_meta",
        lambda compute_id, variant, params: LatestMeta("window", 50, 1),
    )
    adapter = _RecordingAdapter()
    latest_dispatch.latest_compute(adapter, "x", "default", df, {})
    assert adapter.seen_len == 50, "min_window 有限時は adapter 入力を tail(min_window) に絞る"


def test_latest_compute_uses_full_when_min_window_none(monkeypatch):
    # min_window=None（厳密一致設計の既定）は full（全件）で adapter を呼ぶ。
    df = _df(200)
    monkeypatch.setattr(
        latest_dispatch, "latest_meta",
        lambda compute_id, variant, params: LatestMeta("recurrence", None, 1),
    )
    adapter = _RecordingAdapter()
    latest_dispatch.latest_compute(adapter, "x", "default", df, {})
    assert adapter.seen_len == 200, "min_window=None は full（全件）で計算する"
