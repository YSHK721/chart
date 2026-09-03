"""汎用ポーリングループ（watch 共通基盤・アクター非依存の中立核）。

``update_fn → sleep_fn(interval)`` を繰り返す薄いループの**単一定義**。本体は sleep と例外捕捉
のみで、``tools``（運用スクリプト）にも ``indigators.indicator_ui``（チャート UI）にも属さない
汎用抽象である。旧所在 ``tools/watch_loop.py`` に置いたままだと
``indigators.indicator_ui.tools.export_jp225_m1`` → ``tools`` の依存辺が生まれ、``tools`` 側の
参照と合わせて循環（ISSUE-479 循環 C-2）になるため、stdlib のみの中立核 ``common`` へ移設した。
``tools.live_tick_watch`` / ``export_jp225_m1 --watch`` の両 watch が共用する。
"""
from __future__ import annotations

import logging
import time as _time
from typing import Callable, Optional

logger = logging.getLogger("watch_loop")


def run_watch(
    update_fn: Callable[[], None],
    *,
    interval: int,
    sleep_fn: Callable[[float], None] = _time.sleep,
    stop_after: Optional[int] = None,
) -> int:
    """``update_fn`` → ``sleep_fn(interval)`` を繰り返す薄いポーリングループ（副作用）。

    - ``sleep_fn`` 注入でテスト可能化。``stop_after``（回数）で有限終了。
    - ``update_fn`` の一過性例外（ネットワーク断・一時 fetch 失敗等）は捕捉してログし、
      次インターバルへ継続する（無人ポーリングの可用性を保つ）。
    - ``KeyboardInterrupt`` を捕捉して正常終了（0 を返す）。
    """
    count = 0
    try:
        while True:
            try:
                update_fn()
            except KeyboardInterrupt:
                raise
            except Exception:  # 一過性障害でポーリングを止めない（次インターバルへ継続）
                logger.exception("増分更新に失敗しました（次インターバルへ継続します）")
            count += 1
            if stop_after is not None and count >= stop_after:
                break
            sleep_fn(interval)
    except KeyboardInterrupt:
        return 0
    return 0
