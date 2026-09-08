"""日別 dwell ロールアップの**計算量テスト**（ISSUE-364 残面・CLAUDE.md 絶対命令 §4.1）。

固定するのは出力の正しさではなく **無駄の不在**。ここで数えるのは「確定済みの答えのために
発行されるディスク読み（_load_day_rollup）の回数」で、出力が正しいままいくらでも増えうる量である。

実測（是正前・2026-09-08・実データ 120 日窓）:
    dwell の全期間集計はリクエストごとに、データの無い完了日（週末・休場）の npz を毎回
    読み直していた（120 日窓で 1 リクエストあたり 35 回・1 日 0.206 ms＝再発費用の約 8 割。
    全期間なら約 1,700 日 × 0.2 ms ≒ 0.35 秒が毎リクエスト再発する）。
    npz の中身は「署名が同じなら空（None）」と**確定している**ので、読んでも新しい情報は無い。

ここで固定する不変量:
    完了空日の答えは素材署名が決める。**署名が前回と同じ要求ではディスクを読み直さない。**
    署名の取得（_day_source_signature・tick parquet の stat）は鮮度契約
    （``test_market_profile_dwell.py`` 🟡-1: 同一プロセス内のティック到着で再計算する）そのもの
    であり、ここでは削らない（削る案は zp 第 7 段との統一裁定＝承認事項）。

構造: Arrange-Act-Assert（AAA）。回数リテラルの焼き込みはしない（固定するのは「増えない」こと・
発行 − 使用 = 0）。
"""
from __future__ import annotations

import numpy as np
import pytest

from market_profile_api.compute import market_profile_dwell as mpd

_DAY = 1_700_000_000 - (1_700_000_000 % 86400)   # 適当な完了日の始端
_NOW = _DAY + 10 * 86400                          # 十分あとの時刻＝completed
_MISS = object()                                  # 番兵（テスト内 identity）


class _DiskSpy:
    """dwell の単一注入点（module 属性）を差し替え、素材への発行回数を数える。

    ``load_returns`` は「ディスクに入っている確定した答え」＝ ``(status, sig)``。
    """

    def __init__(self, sig: str = "sig-1") -> None:
        self.sig = sig                 # 素材（tick parquet）の現在署名
        self.stored_sig = sig          # ディスク npz に保存済みの署名
        self.signature_calls = 0
        self.load_calls = 0
        self.tick_loads = 0
        self.saved: list = []

    def install(self, monkeypatch) -> None:
        monkeypatch.setattr(mpd, "dwell_cache_miss", lambda: _MISS)
        monkeypatch.setattr(mpd, "_cache_path", lambda s, d: f"/spy/{s}/{d}.npz")
        monkeypatch.setattr(mpd, "_day_source_signature", self._signature)
        monkeypatch.setattr(mpd, "_load_day_rollup", self._load)
        monkeypatch.setattr(mpd, "_save_day_rollup", self._save)
        monkeypatch.setattr(
            mpd, "_load_window_ticks", self._load_ticks)

    def _signature(self, symbol, day_start) -> str:
        self.signature_calls += 1
        return self.sig

    def _load(self, path):
        self.load_calls += 1
        return None, self.stored_sig   # 「この日はデータ無し」が保存済みの状態

    def _save(self, path, roll, sig="") -> None:
        self.saved.append((path, roll, sig))
        self.stored_sig = sig          # 保存でディスク側の署名が更新される（実 Store と同じ）

    def _load_ticks(self, symbol, a, b):
        self.tick_loads += 1
        return mpd._EMPTY_SECS, mpd._EMPTY_MIDS   # 素材にもティックは無い


@pytest.fixture()
def spy(monkeypatch):
    s = _DiskSpy()
    s.install(monkeypatch)
    mpd._reset_caches()
    yield s
    mpd._reset_caches()


_OCCUPANCY_TABLE = np.ones((7, 24), dtype=bool)


def test_an_empty_completed_day_is_not_reread_from_disk(spy) -> None:
    """データの無い完了日の npz は 1 回だけ読み、署名が同じ限り読み直さない。

    読み直しても答えは同じ（署名が内容を決める）＝発行しても使わない読取。
    """
    results = [mpd._day_rollup("SPY", _DAY, _OCCUPANCY_TABLE, _NOW) for _ in range(20)]

    assert all(r is None for r in results), "答えは「データ無し」で一定でなければならない"
    assert spy.load_calls == 1, (
        f"確定済みの空日を {spy.load_calls} 回ディスクから読み直した（要るのは 1 回）")


def test_disk_rereads_do_not_grow_with_queries(spy) -> None:
    """問い合わせ回数を増やしても、ディスク読みは増えない（オーダーの表明・軸 1）。"""
    for _ in range(5):
        mpd._day_rollup("SPY", _DAY, _OCCUPANCY_TABLE, _NOW)
    few = spy.load_calls

    for _ in range(200):
        mpd._day_rollup("SPY", _DAY, _OCCUPANCY_TABLE, _NOW)

    assert spy.load_calls == few, (
        f"問い合わせを 5 → 205 回にしたらディスク読みが {few} → {spy.load_calls} 回に増えた")


@pytest.mark.parametrize("n_days", [3, 30])
def test_second_pass_issues_zero_disk_reads_regardless_of_day_count(spy, n_days) -> None:
    """空日の数を増やしても、2 巡目の要求が発行するディスク読みは 0 のまま（オーダーの表明・軸 2）。

    全期間集計は同じ日の列を毎リクエスト歩き直す。1 巡目で確定した答えのために
    2 巡目が発行する読取は、日数に依らず 0 でなければならない（発行 − 使用 = 0）。
    """
    days = [_DAY + i * 86400 for i in range(n_days)]
    for d in days:                      # 1 巡目（要求 1 本目に相当）
        mpd._day_rollup("SPY", d, _OCCUPANCY_TABLE, _NOW + n_days * 86400)
    first_pass = spy.load_calls

    for d in days:                      # 2 巡目（要求 2 本目に相当）
        mpd._day_rollup("SPY", d, _OCCUPANCY_TABLE, _NOW + n_days * 86400)

    assert spy.load_calls - first_pass == 0, (
        f"2 巡目が {spy.load_calls - first_pass} 回ディスクを読んだ（確定済みの答えに読取は要らない）")


def test_signature_change_still_recomputes(spy) -> None:
    """署名が変われば従来どおり照合・再計算する（鮮度契約 🟡-1 は不変＝過剰記憶の変異を検出）。"""
    mpd._day_rollup("SPY", _DAY, _OCCUPANCY_TABLE, _NOW)          # 空として確定
    before = spy.tick_loads

    spy.sig = "sig-2"                                    # 素材が変わった（ティック到着）
    got = mpd._day_rollup("SPY", _DAY, _OCCUPANCY_TABLE, _NOW)

    assert spy.tick_loads > before, "署名変化で素材から再計算しなければならない"
    assert spy.saved and spy.saved[-1][2] == "sig-2", "再計算の結果は新署名でディスクへ確定する"
    assert got is None                                   # スパイの素材も空なので答えは同じ


def test_an_unfinished_day_is_not_remembered(spy) -> None:
    """未完了日は確定していないので、空の記憶の対象にしない（毎回計算する）。"""
    now_inside = _DAY + 60                               # 当日（completed でない）

    mpd._day_rollup("SPY", _DAY, _OCCUPANCY_TABLE, now_inside)
    before = spy.tick_loads
    mpd._day_rollup("SPY", _DAY, _OCCUPANCY_TABLE, now_inside)

    assert spy.tick_loads > before, "未完了日を確定として記憶してはならない（毎回素材から計算する）"
