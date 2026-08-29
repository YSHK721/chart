"""形成中バーを 1 起動につき 1 回だけ組み立てることを固定する（ISSUE-450 原因 K）。

固定するのは出力の正しさではなく **無駄の不在**。ここで数えるのは「ティックから形成中バーを
組み立てた回数」で、出力が正しいままいくらでも増えうる量である。

実測（是正前・2026-08-28）:
    1 回のチャート起動で ``forming_bar`` が**指標の本数ぶん**（12〜16 回）走り、合計
    195〜362 ms＝起動全体の **15〜35%** を占めていた。すべて同じ ``(ref, tf, now_unix)`` に
    対する同じ答えで、``mode=latest`` のプロファイルでは 1 リクエスト 34 ms のうち 87% が
    ``apply_forming_bar`` → ``forming_bar_from_ticks``（tick parquet の読み込み）だった。

ここで固定する不変量:
    1. 同じ ``(ref, tf, now_unix)`` を何回問い合わせても、組み立ては 1 回だけ。
    2. ``now_unix`` が進んだら組み立て直す（古い断面を配り続けない）。
    3. 素材（tick parquet）が変わったら組み立て直す（同上）。

構造: Arrange-Act-Assert（AAA）。
"""
from __future__ import annotations

import pytest

from adapter.compute import forming_bar as forming_bar_mod

_REF = "jp225_tick"
_TF = "5m"
_NOW = 1_787_887_980


class _Counter:
    """``forming_bar_from_ticks`` の呼び出し回数を数える（Test Spy）。"""

    def __init__(self, monkeypatch, *, bar) -> None:
        monkeypatch.undo()
        self.calls = 0
        self.bar = dict(bar)

        def counting(start_unix, end_unix, **kwargs):
            self.calls += 1
            return {**self.bar, "time": int(start_unix)}

        monkeypatch.setattr(forming_bar_mod, "forming_bar_from_ticks", counting)


@pytest.fixture()
def bar():
    return {"time": 0, "open": 1.0, "high": 2.0, "low": 0.5, "close": 1.5, "volume": 3}


@pytest.fixture(autouse=True)
def _clear():
    forming_bar_mod.clear_forming_cache()
    yield
    forming_bar_mod.clear_forming_cache()


def test_same_moment_is_built_only_once(monkeypatch, bar) -> None:
    """1 起動ぶん（同じ時点）の問い合わせでは 1 回しか組み立てない。

    指標の本数ぶん組み立てていたのが ISSUE-450 原因 K。
    """
    counter = _Counter(monkeypatch, bar=bar)

    results = [forming_bar_mod.forming_bar(_REF, _TF, _NOW) for _ in range(16)]

    assert counter.calls == 1, (
        f"同じ時点を 16 回問い合わせて {counter.calls} 回組み立てた（要るのは 1 回）")
    assert all(r == results[0] for r in results), "同じ時点なのに違う答えを返している"
    assert results[0] is not None


def test_a_later_moment_is_rebuilt(monkeypatch, bar) -> None:
    """時点が進んだら組み立て直す（古い断面を配り続けない）。"""
    counter = _Counter(monkeypatch, bar=bar)

    forming_bar_mod.forming_bar(_REF, _TF, _NOW)
    forming_bar_mod.forming_bar(_REF, _TF, _NOW + 1)

    assert counter.calls == 2


def test_changed_tick_source_is_rebuilt(monkeypatch, bar) -> None:
    """素材（tick parquet）が変わったら組み立て直す。"""
    counter = _Counter(monkeypatch, bar=bar)
    token = {"v": 0}
    monkeypatch.setattr(forming_bar_mod, "_tick_source_token",
                        lambda start, end: (token["v"],))

    forming_bar_mod.forming_bar(_REF, _TF, _NOW)
    forming_bar_mod.forming_bar(_REF, _TF, _NOW)
    token["v"] = 1
    forming_bar_mod.forming_bar(_REF, _TF, _NOW)

    assert counter.calls == 2, "素材が変わったのに組み立て直していない"


def test_unknown_source_state_is_not_cached(monkeypatch, bar) -> None:
    """素材の状態を確かめられないときは記憶しない（古い断面を配る危険を作らない）。"""
    counter = _Counter(monkeypatch, bar=bar)
    monkeypatch.setattr(forming_bar_mod, "_tick_source_token", lambda start, end: None)

    forming_bar_mod.forming_bar(_REF, _TF, _NOW)
    forming_bar_mod.forming_bar(_REF, _TF, _NOW)

    assert counter.calls == 2
