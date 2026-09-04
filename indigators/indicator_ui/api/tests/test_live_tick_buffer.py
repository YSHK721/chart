"""ISSUE-049 — ``LiveTickBuffer`` の検証（TDD: Red→Green・ジッターバッファ配信の記録系分離）。

参照実装: prototype_260707-01/server.py の ``_poll_loop``（依頼者実機確認済み）。
行儀を忠実に移植する: カーソル維持・直列 1 接続・エラー時指数バックオフ（5→10→…最大 60 秒）・
連続 8 失敗で 10 分停止のサーキットブレーカ・バッファ保持 30 分・mid=(bid+ask)/2。

全フェイク（fetch_fn / time_fn 注入）・ネットワーク非依存。ポーリング 1 回分の純粋な
状態遷移は ``_poll_once`` を直接駆動して決定論的に検証する（background thread は start/stop の
冪等性のみ別途検証）。ファイル書込は一切行わない（メモリのみ＝記録系 parquet/M1 へ非干渉）。
"""

from __future__ import annotations

import pytest

from adapter.compute.live_tick_buffer import LiveTickBuffer


class FakeClock:
    """time_fn 注入用の可制御時計（秒）。``advance`` で進める。"""

    def __init__(self, t0: float = 1000.0) -> None:
        self.t = t0

    def __call__(self) -> float:
        return self.t

    def advance(self, dt: float) -> None:
        self.t += dt


def _buffer(fetch_fn, clock=None, interval=5.0):
    clock = clock or FakeClock()
    buf = LiveTickBuffer(fetch_fn=fetch_fn, interval=interval, time_fn=clock)
    return buf, clock


# --------------------------------------------------------------------------- #
# カーソル維持・mid 変換・ticks_since 境界
# --------------------------------------------------------------------------- #
def test_poll_advances_cursor_and_stores_mid(monkeypatch):
    # Arrange: (ms, bid, ask) を返す fetch。mid=(bid+ask)/2 で蓄積される。
    clock = FakeClock(1000.0)  # now_ms = 1_000_000
    rows = [(1_000_100, 39000.0, 39010.0), (1_000_200, 39002.0, 39012.0)]
    seen = []

    def fetch(cursor_ms):
        seen.append(cursor_ms)
        return rows if not seen[1:] else []

    buf, _clock = _buffer(fetch, clock)
    # Act
    buf._poll_once()
    # Assert: mid 変換・ticks_since(0) で 2 件・cursor は最新 ms。
    assert buf.ticks_since(0) == [(1_000_100, 39005.0), (1_000_200, 39007.0)]
    assert buf.stats()["cursor_ms"] == 1_000_200


def test_ticks_since_is_strictly_greater_boundary(monkeypatch):
    clock = FakeClock(1000.0)
    rows = [(1_000_100, 10.0, 20.0), (1_000_200, 12.0, 22.0)]
    buf, _clock = _buffer(lambda c: rows, clock)
    buf._poll_once()
    # since=ms は「その ms より後」のみ（境界は含まない・prototype `t[0] > since`）。
    assert buf.ticks_since(1_000_100) == [(1_000_200, 17.0)]
    assert buf.ticks_since(1_000_200) == []


def test_poll_excludes_rows_at_or_before_cursor(monkeypatch):
    # fetch が cursor 以下の行を含んでも取り込まない（重複防止）。
    clock = FakeClock(2_000_000.0)  # now_ms 大 → 初回 cursor が正の値になる。
    buf, _clock = _buffer(lambda c: [], clock)
    # 初回 cursor = now_ms - 30分。fetch が cursor と同値の行を返しても境界重複として除外。
    cur = buf._now_ms() - LiveTickBuffer.BUFFER_KEEP_MS
    buf2, _c2 = _buffer(lambda c: [(cur, 1.0, 3.0), (cur + 10, 2.0, 4.0)], clock)
    buf2._poll_once()
    assert buf2.ticks_since(cur) == [(cur + 10, 3.0)]


# --------------------------------------------------------------------------- #
# 30 分トリム
# --------------------------------------------------------------------------- #
def test_old_ticks_trimmed_beyond_30min(monkeypatch):
    clock = FakeClock(1000.0)  # now_ms=1_000_000
    # 1 回目: 現在付近の tick を投入。
    first = [(1_000_000, 10.0, 20.0)]
    second = [(2_000_000, 12.0, 22.0)]
    batches = iter([first, second])
    buf, _clock = _buffer(lambda c: next(batches), clock)
    buf._poll_once()
    assert len(buf.ticks_since(0)) == 1
    # 時刻を 40 分進める → cut=now-30分 が 1_000_000 を追い越す。次 poll でトリム。
    clock.advance(40 * 60)  # +2400s → now_ms=3_400_000
    buf._poll_once()
    # 古い 1_000_000 はトリムされ、新しい 2_000_000 のみ残る。
    assert [t[0] for t in buf.ticks_since(0)] == [2_000_000]


# --------------------------------------------------------------------------- #
# 指数バックオフ（5→10→20→40→最大 60）
# --------------------------------------------------------------------------- #
def test_backoff_doubles_on_error_capped_at_60(monkeypatch):
    clock = FakeClock(1000.0)

    def boom(cursor_ms):
        raise RuntimeError("feed down")

    buf, _clock = _buffer(boom, clock, interval=5.0)
    seen = []
    for _ in range(8):
        buf._poll_once()
        seen.append(buf.stats()["backoff_s"])
    # 5→10→20→40→60→60→60→(8回目で circuit) 60。倍化と 60 上限を固定する。
    assert seen[0] == 10.0
    assert seen[1] == 20.0
    assert seen[2] == 40.0
    assert seen[3] == 60.0
    assert seen[4] == 60.0


def test_backoff_resets_on_success(monkeypatch):
    clock = FakeClock(1000.0)
    calls = {"n": 0}

    def sometimes(cursor_ms):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("transient")
        return [(1_000_500, 1.0, 3.0)]

    buf, _clock = _buffer(sometimes, clock, interval=5.0)
    buf._poll_once()  # error → backoff 10
    assert buf.stats()["backoff_s"] == 10.0
    buf._poll_once()  # success → backoff reset to interval
    assert buf.stats()["backoff_s"] == 5.0


# --------------------------------------------------------------------------- #
# サーキットブレーカ（連続 8 失敗で 10 分停止）
# --------------------------------------------------------------------------- #
def test_circuit_breaker_pauses_10min_after_8_failures(monkeypatch):
    clock = FakeClock(1000.0)

    def boom(cursor_ms):
        raise RuntimeError("feed down")

    buf, _clock = _buffer(boom, clock)
    for _ in range(8):
        buf._poll_once()
    st = buf.stats()
    # 8 連続失敗 → paused_until = now + 600s、errors_in_row は 0 にリセット。
    assert st["paused_until"] == pytest.approx(1000.0 + 600.0)
    assert st["errors_in_row"] == 0


def test_paused_poll_is_noop_and_does_not_fetch(monkeypatch):
    clock = FakeClock(1000.0)
    fetched = {"n": 0}

    def fetch(cursor_ms):
        fetched["n"] += 1
        return []

    buf, _clock = _buffer(fetch, clock, interval=5.0)
    # 手動で pause 状態にする。
    buf._paused_until = clock.t + 600.0
    delay = buf._poll_once()
    # 停止中は fetch を呼ばず、次ポーリングまでの待機は 1 秒（backoff=5s を上乗せしない＝
    #   prototype の 1 秒毎再チェック行儀。停止明けを最大 1 秒で検知する）。
    assert fetched["n"] == 0
    assert delay == LiveTickBuffer.PAUSE_RECHECK_S == 1.0


def test_run_loop_stop_interrupts_wait_no_orphan_thread():
    # 🟡1 回帰: fetch が失敗し backoff が最大 60s まで育っても、stop() は Event.wait を割り込んで
    #   即座に thread を終わらせる（旧実装は time.sleep(backoff) を割り込めず join timeout で
    #   orphan thread が残り、直後の start で二重化した）。実 thread・実時刻で駆動する。
    import time as _t

    def boom(cursor_ms):
        raise RuntimeError("feed down")

    buf = LiveTickBuffer(fetch_fn=boom, interval=0.05)  # 数回失敗で backoff が育ち wait に入る。
    buf.start()
    _t.sleep(0.3)  # 数回ポーリングさせ backoff を育て、長い wait に入らせる。
    t0 = _t.time()
    buf.stop()
    elapsed = _t.time() - t0
    assert buf.is_running() is False
    assert elapsed < 1.5  # 60s backoff 中でも即停止（旧実装は join(2.0) まで戻らず orphan 残存）。
    # 停止後の start で thread は単一（二重化しない）。
    buf.start()
    assert buf.is_running() is True
    buf.stop()
    assert buf.is_running() is False


# --------------------------------------------------------------------------- #
# start/stop 冪等（background thread）
# --------------------------------------------------------------------------- #
def test_start_stop_are_idempotent(monkeypatch):
    buf = LiveTickBuffer(fetch_fn=lambda c: [], interval=5.0)
    buf.stop()  # 未起動での stop は no-op
    buf.start()
    buf.start()  # 二重 start は無視（単一 thread）
    assert buf.is_running() is True
    buf.stop()
    buf.stop()  # 二重 stop は no-op
    assert buf.is_running() is False


def test_default_fetch_fn_is_marketdata_fetch_ticks_since(monkeypatch):
    # fetch_fn 未注入時は marketdata.fetch_ticks_since を遅延 import して使う。
    import marketdata

    captured = {}

    def fake(cursor_ms):
        captured["cursor"] = cursor_ms
        return [(cursor_ms + 1, 1.0, 3.0)]

    monkeypatch.setattr(marketdata, "fetch_ticks_since", fake, raising=False)
    buf = LiveTickBuffer(interval=5.0)
    buf._poll_once()
    assert "cursor" in captured
    assert buf.stats()["buffer_ticks"] == 1
