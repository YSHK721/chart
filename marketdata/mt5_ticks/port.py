"""供給ポートと例外分類（usecase 層・**依存ゼロ**・stdlib のみ）。

DIP の適用点は 2 つだけに絞る（設計 §3）。

``IncrementalTickSource``
    実 HTTP / Fake / Spy の 3 実装が差し替わる唯一の境界。
``Clock``
    「日が変わったか」「確定してよい時刻か」を決める時刻源。実時刻を直に読むと
    日跨ぎ・確定条件の検定が書けなくなる。

永続化ポートを置かない理由（YAGNI）:
    ジャーナル・parquet・M1 は :mod:`marketdata.tick_m1` が権威であり、差し替える相手が
    存在しない。抽象を足すと「置き場所が 2 つある」状態を作るだけになる。検定での差替は
    monkeypatch（既存様式）で足りる。

例外を 2 段にする理由:
    待てば直る障害（認証・過負荷・端末不調）と、待っても直らない障害（引数不正）を同じ型に
    すると、直らない要求を投げ続けるか、直る障害で常駐ループを落とすかのどちらかになる。
"""
from __future__ import annotations

import json
from typing import Any, Mapping, Optional, Protocol, runtime_checkable


class Mt5SupplyError(RuntimeError):
    """MT5 供給の前提が崩れたことを表す（既定は **Fail-Stop**・再試行しない）。"""


class SupplyUnavailable(Mt5SupplyError):
    """一時的に供給できない（**バックオフして再試行してよい**）。"""


#: 待てば直りうる HTTP ステータス（設計 §4 のエラー表）。
_RETRYABLE_STATUSES = frozenset({401, 429, 502})
#: 待っても直らない HTTP ステータス。
_FAIL_STOP_STATUSES = frozenset({400})


def _detail(body: bytes) -> str:
    """応答 body から人が読める手掛かりを取り出す（端末の last_error を失わない）。"""
    text = body.decode("utf-8", errors="replace") if isinstance(body, (bytes, bytearray)) else str(body)
    try:
        parsed = json.loads(text)
    except ValueError:
        return text.strip()
    if isinstance(parsed, dict):
        return "; ".join(f"{k}={v}" for k, v in sorted(parsed.items()))
    return text.strip()


def error_for_status(
    status: int, headers: "Mapping[str, str]", body: bytes
) -> Mt5SupplyError:
    """HTTP ステータスを供給例外へ**分類する唯一の判断点**。

    未知のステータスは Fail-Stop 側へ倒す。「たぶん一時的だろう」で再試行に倒すと、
    原因不明の応答に対して無限再試行を作ってしまう。
    """
    code = int(status)
    detail = _detail(body)
    message = f"MT5 供給が失敗しました（status={code}）: {detail}"
    if code in _RETRYABLE_STATUSES:
        return SupplyUnavailable(message)
    if code in _FAIL_STOP_STATUSES:
        return Mt5SupplyError(message + "。要求が不正のため再試行しない。")
    return Mt5SupplyError(message + "。未知の応答のため再試行しない。")


@runtime_checkable
class IncrementalTickSource(Protocol):
    """増分ティックの供給元（実 HTTP / Fake / Spy が実装する）。"""

    def fetch(
        self, *, symbol: str, from_msc: int, to_msc: "Optional[int]", max_rows: int
    ) -> Any:
        """``[from_msc, to_msc]`` のティックを取る。戻りは `marketdata.mt5_ticks.wire.TickResponse`。

        失敗は :class:`SupplyUnavailable`（再試行可）か :class:`Mt5SupplyError`
        （Fail-Stop）を送出する。**戻り値で失敗を表さない**（黙って 0 行にしない）。
        """


@runtime_checkable
class Clock(Protocol):
    """現在時刻（UTC）を与える。日跨ぎ・確定条件の判断に使う唯一の時刻源。"""

    def now(self) -> Any:
        """UTC の現在時刻（``datetime``・tz-aware）。"""
