"""LiveTickBuffer（adapter/compute/live_tick_buffer.py）— ライブ tick のジッターバッファ（配信系）。

ISSUE-049: indicator_ui（present・B方式）のライブ価格を「12 秒固定遅延のなめらか tick 再生」へ
強化するための**配信モジュール**。バックグラウンド thread で 5 秒周期の増分カーソルポーリングを行い、
直近 30 分の tick をメモリに保持する。フロント（LiveTickPlayer）が ``/live_ticks?since=`` で増分
取得し、固定遅延で元の時間間隔どおり再生する。

参照実装: prototype_260707-01/server.py の ``_poll_loop``（依頼者実機確認済み）。行儀を忠実に移植する:
  - カーソル維持（重複ダウンロードなし）・直列 1 接続（並列 fetch なし）。
  - エラー時は指数バックオフ（interval→×2→…最大 60 秒）。
  - 連続 8 失敗で 10 分停止のサーキットブレーカ（``paused_until``）。
  - バッファ保持 30 分（cut より古い tick をトリム）。lock で共有状態を保護。
  - mid=(bid+ask)/2 に畳んで保持する。

記録系（parquet / M1 / rollups）とは**完全分離**: 本クラスはファイル書込を一切行わない
（メモリのみ）。よって既存の tick_m1 / rollup パイプラインへ干渉しない。

隔離・注入（テスト容易性）: ``fetch_fn`` / ``time_fn`` を注入可能にする。既定の
``fetch_fn`` は ``marketdata.fetch_ticks_since``（(ms,bid,ask) を返す）を**遅延 import**で解決し、
ベンダ（dukascopy）依存をこのモジュールの import 時点に持ち込まない。``_poll_once`` は「次の
ポーリングまでの待機秒」を返し、``_run_loop`` はそれを ``threading.Event.wait`` で待つ（停止
応答可能＝``stop()`` が sleep 中でも即座に thread を終わらせ、orphan thread・二重起動を防ぐ）。
"""

from __future__ import annotations

import threading
import time
from typing import Callable, List, Optional, Tuple


class LiveTickBuffer:
    """5 秒周期の増分カーソルポーリングで直近 30 分の tick を保持する配信バッファ。"""

    POLL_INTERVAL = 5.0
    BUFFER_KEEP_MS = 30 * 60 * 1000  # バッファ保持 30 分。
    MAX_BACKOFF = 60.0               # 指数バックオフ上限（prototype 実測）。
    CIRCUIT_FAILS = 8                # 連続失敗閾値（サーキットブレーカ）。
    CIRCUIT_PAUSE_S = 600.0          # 10 分停止。
    FETCH_LIMIT = 30_000             # 1 リクエスト最大行数（prototype と同一）。

    PAUSE_RECHECK_S = 1.0            # サーキットブレーカ停止中の再チェック粒度（prototype と同一）。

    def __init__(
        self,
        *,
        fetch_fn: Optional[Callable[[int], List[Tuple[int, float, float]]]] = None,
        interval: float = POLL_INTERVAL,
        time_fn: Callable[[], float] = time.time,
    ) -> None:
        # fetch_fn(cursor_ms) -> list[(ms, bid, ask)]。None は marketdata.fetch_ticks_since を遅延解決。
        self._fetch_fn = fetch_fn
        self._interval = interval
        self._time = time_fn

        self._lock = threading.Lock()
        self._ticks: List[Tuple[int, float]] = []  # (unix_ms, mid) 昇順。
        self._cursor: Optional[int] = None          # 取得済みカーソル（初回 poll で確定）。
        self._backoff = interval
        self._errors_in_row = 0
        self._paused_until = 0.0
        self._status = "starting"
        self._polls = 0
        self._last_lag_ms: Optional[int] = None

        self._thread: Optional[threading.Thread] = None
        self._stop = threading.Event()

    # ---- 時刻ヘルパ -------------------------------------------------------- #
    def _now_ms(self) -> int:
        return int(self._time() * 1000)

    def _get_fetch(self) -> Callable[[int], List[Tuple[int, float, float]]]:
        """fetch_fn を解決する（未注入時は marketdata.fetch_ticks_since を遅延 import）。"""
        if self._fetch_fn is not None:
            return self._fetch_fn
        from marketdata import fetch_ticks_since  # 遅延: ここで初めて dukascopy を要求。
        return fetch_ticks_since

    # ---- ポーリング 1 回分（純粋な状態遷移・テストが直接駆動する） ----------- #
    def _poll_once(self) -> float:
        """増分カーソルで 1 回ポーリングし、**次のポーリングまでの待機秒**を返す。

        成功時は蓄積・トリムして ``interval`` を、失敗時はバックオフ倍化してその値を返す。
        サーキットブレーカ停止中は fetch せず ``PAUSE_RECHECK_S``（1 秒）を返す（prototype と同じ
        行儀＝backoff を上乗せせず 1 秒毎に ``paused_until`` を再チェックし、停止明けを即座に検知する）。
        """
        if self._cursor is None:
            # 初期カーソル: 30 分前から（初回だけ catch-up し、以降は増分）。
            self._cursor = self._now_ms() - self.BUFFER_KEEP_MS

        if self._time() < self._paused_until:
            # サーキットブレーカ中は fetch せず 1 秒後に再チェック（backoff を上乗せしない）。
            return self.PAUSE_RECHECK_S

        fetch = self._get_fetch()
        cursor = self._cursor
        try:
            rows = fetch(cursor)
        except Exception as exc:  # noqa: BLE001 — 一過性障害はバックオフして継続（記録系へ影響させない）。
            with self._lock:
                self._errors_in_row += 1
                self._status = f"error: {type(exc).__name__}: {exc}"
                if self._errors_in_row >= self.CIRCUIT_FAILS:
                    self._paused_until = self._time() + self.CIRCUIT_PAUSE_S
                    self._errors_in_row = 0
                    self._status += " (circuit-breaker: 10min pause)"
            self._backoff = min(self._backoff * 2, self.MAX_BACKOFF)
            return self._backoff

        # cursor より厳密に後の行のみ mid へ畳む（境界重複を排する）。
        new = [
            (int(r[0]), (float(r[1]) + float(r[2])) / 2.0)
            for r in rows
            if int(r[0]) > cursor
        ]
        with self._lock:
            if new:
                self._ticks.extend(new)
                self._cursor = new[-1][0]
                cut = self._now_ms() - self.BUFFER_KEEP_MS
                while self._ticks and self._ticks[0][0] < cut:
                    self._ticks.pop(0)
            self._status = "ok"
            self._errors_in_row = 0
            self._polls += 1
            self._last_lag_ms = self._now_ms() - self._cursor
        self._backoff = self._interval
        return self._backoff

    # ---- background thread（直列 1 接続・停止応答可能） ------------------- #
    def _run_loop(self) -> None:
        while not self._stop.is_set():
            t0 = self._time()
            delay = self._poll_once()
            elapsed = self._time() - t0
            # Event.wait は stop() の set で即座に True を返す＝sleep 中でも割り込めるため、
            #   最大 60s の待機中に stop されても orphan thread を残さない（🟡1・二重起動防止）。
            if self._stop.wait(max(0.0, delay - elapsed)):
                break

    def start(self) -> None:
        """ポーリング thread を起動する（冪等・稼働中の再 start は無視）。"""
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                return
            self._stop.clear()
            self._thread = threading.Thread(target=self._run_loop, daemon=True)
            self._thread.start()

    def stop(self) -> None:
        """ポーリング thread を停止する（冪等・未起動での stop は no-op）。"""
        self._stop.set()
        thread = self._thread
        if thread is not None and thread.is_alive():
            thread.join(timeout=2.0)
        self._thread = None

    def is_running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    # ---- 公開読取（配信 endpoint が使う） ------------------------------- #
    def ticks_since(self, ms: int) -> List[Tuple[int, float]]:
        """``ms`` より後（境界含まず）の ``(unix_ms, mid)`` を昇順で返す（prototype `t[0] > since`）。"""
        with self._lock:
            return [t for t in self._ticks if t[0] > ms]

    def stats(self) -> dict:
        """メトリクス（状態・バッファ件数・カーソル・バックオフ・停止時刻）を返す。"""
        with self._lock:
            return {
                "status": self._status,
                "buffer_ticks": len(self._ticks),
                "newest_ms": self._ticks[-1][0] if self._ticks else None,
                "cursor_ms": self._cursor,
                "errors_in_row": self._errors_in_row,
                "paused_until": self._paused_until,
                "backoff_s": self._backoff,
                "polls": self._polls,
                "lag_ms": self._last_lag_ms,
            }
