"""日別 ZP ロールアップの**計算量テスト**（ISSUE-450 原因 L・CLAUDE.md 絶対命令 §4.1）。

固定するのは出力の正しさではなく **無駄の不在**。ここで数えるのは「素材へ問い合わせた回数」で、
出力が正しいままいくらでも増えうる量である。

実測（是正前・2026-08-28）:
    market_profile は全期間集計のため 1 リクエストで日別ロールアップを 5,187 回呼ぶ。うち
    3,642 回はメモリ記憶に当たるが、**データの無い日 1,545 回（30%）は記憶されず**、毎回
    ``day_source_signature``（tick parquet の走査）と ``load_null``（ディスク読み）を
    やり直していた。1 リクエスト 630 ms のほぼ全部がこれ。

ここで固定する不変量:
    完了日の答えは確定している。**答えが「データ無し」でも記憶し、2 回目以降は素材を見ない。**

構造: Arrange-Act-Assert（AAA）。
"""
from __future__ import annotations

import pytest

from market_profile_api.compute import market_profile_zp as zp

_SYMBOL = "TEST_SYMBOL"
_DAY = 1_700_000_000 - (1_700_000_000 % 86400)   # 適当な完了日の始端
_NOW = _DAY + 10 * 86400                          # 十分あとの時刻＝completed


class _StoreSpy:
    """``zp_null_store`` の差し替え。素材への問い合わせ回数を数える。"""

    CACHE_MISS = object()

    def __init__(self) -> None:
        self.signature_calls = 0
        self.load_calls = 0
        self.saved: list = []

    def null_path(self, symbol, day_start):
        return f"/dev/null/{symbol}/{day_start}"

    def day_source_signature(self, symbol, day_start):
        self.signature_calls += 1
        return "sig-1"

    def load_null(self, path):
        self.load_calls += 1
        return None, "sig-1"          # 「この日はデータ無し」が確定している状態

    def save_null(self, path, roll, sig):
        self.saved.append((path, roll, sig))


@pytest.fixture()
def spy(monkeypatch):
    store = _StoreSpy()
    monkeypatch.setattr(zp, "zp_null_store", lambda: store)
    zp._NULL_CACHE.clear()
    yield store
    zp._NULL_CACHE.clear()


def test_a_day_without_data_is_remembered(spy) -> None:
    """データの無い完了日も 1 回だけ調べ、以後は素材を見ない。

    記憶しないと、全期間集計のたびに休場日ぶん（実測 1,545 日）を調べ直すことになる。
    """
    results = [zp._zp_day_rollup(_SYMBOL, _DAY, _NOW) for _ in range(20)]

    assert all(r is None for r in results), "答えは「データ無し」で一定でなければならない"
    assert spy.load_calls == 1, (
        f"データの無い日を {spy.load_calls} 回調べた（要るのは 1 回）")
    assert spy.signature_calls == 1, (
        f"素材の指紋を {spy.signature_calls} 回取った（要るのは 1 回）")


def test_remembering_does_not_grow_with_the_number_of_queries(spy) -> None:
    """問い合わせ回数を増やしても、素材への問い合わせは増えない（オーダーの表明）。"""
    for _ in range(5):
        zp._zp_day_rollup(_SYMBOL, _DAY, _NOW)
    few = spy.load_calls

    for _ in range(200):
        zp._zp_day_rollup(_SYMBOL, _DAY, _NOW)

    assert spy.load_calls == few, (
        f"問い合わせを 5 → 205 回にしたら素材読みが {few} → {spy.load_calls} 回に増えた")


def test_an_unfinished_day_is_not_remembered_as_final(spy) -> None:
    """未完了日は確定していないので、この記憶の対象にしない。"""
    zp._NULL_CACHE.clear()

    zp._zp_day_rollup(_SYMBOL, _DAY, _DAY + 60)      # 当日（completed でない）

    assert (_SYMBOL, _DAY) not in zp._NULL_CACHE, "未完了日を確定として記憶してはならない"
