"""unit テスト共有 fixture。

`tokyo_local_timezone` は「naive 時刻はローカル TZ に依存せず UTC」という共有規則
（`datawindow.half_open` / ISSUE-401・402・406）を検定する側の**唯一の TZ 固定手段**。
`monkeypatch.setenv("TZ", ...)` だけではプロセスの TZ は変わらない（``time.tzset()``
が要る）ため、各テストで手書きすると「無力なテストが有効に見える」再発源になる
（ISSUE-406 レビュー 🟡-1 の実測: setenv のみの検定は局所 TZ 変異体を生存させた）。
"""
from __future__ import annotations

import os
import time

import pytest


@pytest.fixture()
def tokyo_local_timezone():
    """プロセスのローカル TZ を Asia/Tokyo に固定する（UTC との差 +9h）。"""
    saved = os.environ.get("TZ")
    os.environ["TZ"] = "Asia/Tokyo"
    time.tzset()
    try:
        yield
    finally:
        if saved is None:
            os.environ.pop("TZ", None)
        else:
            os.environ["TZ"] = saved
        time.tzset()
