"""CVFE の JSON Lines ログ（仕様 §6「ログ」要件）。

層名/責務:
    副作用（書き出し）を本モジュールに閉じ込め、計算モジュール（measures / jumps /
    quality / engine）は :class:`Logger` プロトコル越しにのみ副作用へ触れる（DIP）。
    既定は :data:`NULL_LOGGER`（無出力）であり、注入しない限り計算経路は純粋関数に留まる。

出力形式（仕様 §6）:
    1 事象につき JSON Lines 1 行。フィールドは ``{ts, level, code, bar_index, detail}``。
    ``ts`` は UTC の ISO 8601 文字列。時刻源は注入可能（テストの決定性のため）。

依存: 標準ライブラリのみ。
"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from typing import Any, Callable, Protocol, TextIO


class Logger(Protocol):
    """CVFE の計算モジュールが要求するログ契約（本パッケージが所有する抽象）。"""

    def emit(self, level: str, code: str, bar_index: int, detail: str) -> None: ...


class _NullLogger:
    """無出力ロガー。計算経路の既定値（副作用なし）。"""

    __slots__ = ()

    def emit(self, level: str, code: str, bar_index: int, detail: str) -> None:
        return None


#: 既定のロガー（無出力）。``logger=None`` はすべてこれに解決される。
NULL_LOGGER: Logger = _NullLogger()


class JsonlLogger:
    """JSON Lines を ``stream`` へ 1 行ずつ書き出すロガー（仕様 §6）。"""

    __slots__ = ("_stream", "_clock")

    def __init__(self, stream: TextIO | None = None,
                 clock: Callable[[], str] | None = None) -> None:
        self._stream = sys.stderr if stream is None else stream
        self._clock = clock if clock is not None else _utc_now_iso

    def emit(self, level: str, code: str, bar_index: int, detail: str) -> None:
        record: dict[str, Any] = {
            "ts": self._clock(),
            "level": level,
            "code": code,
            "bar_index": int(bar_index),
            "detail": detail,
        }
        self._stream.write(json.dumps(record, ensure_ascii=False) + "\n")


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def resolve(logger: Logger | None) -> Logger:
    """``None`` を :data:`NULL_LOGGER` に解決する（各計算モジュールの入口で用いる）。"""
    return NULL_LOGGER if logger is None else logger
