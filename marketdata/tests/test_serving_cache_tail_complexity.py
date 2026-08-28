"""1m 経路の再読み込みに関する**計算量テスト**（ISSUE-450・CLAUDE.md 絶対命令 §4.1）。

固定するのは出力の正しさではなく **無駄の不在**。ここで数えるのは「ディスクから読み直した回数」で、
出力が正しいままいくらでも増えうる量である。

実測（是正前・2026-08-28）:
    表示時間足 1m のチャートを開くと 1 回の起動で ``load_dataframe`` が 15 回走り、そのすべてが
    ``tail_reader.read_tail`` でディスクを読み直していた。3.45 秒中 **1.97 秒（57%）** がこれ。
    上位足（5m..1M）は ``rollup_store.read`` の mtime キャッシュが効くため 2 回目以降が速く、
    **1m だけ**が毎回読み直しになっていた（1m の 2 回目 2,514ms 対 5m の 2 回目 573ms）。

ここで固定する不変量:
    1. 素材が変わっていなければ、何回問い合わせてもディスク読みは 1 回だけ。
    2. 素材が変わったら読み直す（古い断面を配り続けない）。

構造: Arrange-Act-Assert（AAA）。
"""
from __future__ import annotations

import pandas as pd
import pytest

from marketdata import serving_cache, tail_reader


@pytest.fixture()
def csv_path(tmp_path):
    """1m 素材に見立てた CSV（date 列＋OHLCV）。"""
    path = tmp_path / "jp225_tick_m1.csv"
    rows = ["date,open,high,low,close,volume"]
    rows += [f"2026-08-28 00:{i:02d}:00,1,2,0.5,1.5,10" for i in range(5)]
    path.write_text("\n".join(rows) + "\n", encoding="utf-8")
    return path


class _ReadCounter:
    """``tail_reader.read_tail`` の実読み回数を数える（Test Spy）。"""

    def __init__(self, monkeypatch) -> None:
        self.reads = 0
        real = tail_reader.read_tail

        def counting(path, n_rows):
            self.reads += 1
            return real(path, n_rows)

        monkeypatch.setattr(serving_cache.tail_reader, "read_tail", counting)


def _clear() -> None:
    """キャッシュを空にする（既存テストと同じ作法＝実体の dict を直接 clear する）。"""
    serving_cache._BASE_CACHE.clear()
    serving_cache._RESAMPLE_CACHE.clear()
    serving_cache._TAIL_CACHE.clear()


def _resolve(path):
    return serving_cache.resolve_rollup_dataframe(
        "jp225_tick", "1m", path=path, atomic_tail_rows=1000)


def test_unchanged_source_is_read_from_disk_only_once(monkeypatch, csv_path) -> None:
    """素材が変わらなければ、何回問い合わせてもディスク読みは増えない。

    1 回のチャート起動で同じ素材を 15 回読み直していたのが ISSUE-450 の 1m の主因。
    """
    _clear()
    counter = _ReadCounter(monkeypatch)

    frames = [_resolve(csv_path) for _ in range(15)]

    assert counter.reads == 1, (
        f"素材が変わっていないのに {counter.reads} 回読み直した（要るのは 1 回）")
    for frame in frames[1:]:
        pd.testing.assert_frame_equal(frame, frames[0])


def test_changed_source_is_read_again(monkeypatch, csv_path) -> None:
    """素材が変わったら読み直す（古い断面を配り続けない）。"""
    _clear()
    counter = _ReadCounter(monkeypatch)
    before = _resolve(csv_path)

    with csv_path.open("a", encoding="utf-8") as handle:
        handle.write("2026-08-28 00:05:00,9,9,9,9,99\n")
    after = _resolve(csv_path)

    assert counter.reads >= 2, "追記後も読み直していない（古い断面を配り続ける）"
    assert len(after) == len(before) + 1, "追記した 1 行が反映されていない"
