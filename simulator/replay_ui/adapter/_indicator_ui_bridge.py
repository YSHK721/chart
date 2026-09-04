"""indicator_ui ロード面の**再公開層**（実体は供給側スライスが所有する）。

ISSUE-479 Wave2 X-1: ロード面の実体は
``indigators/indicator_ui/api_loader.py``（供給側スライス）へ移した。
消費スライスは 3 つ（replay_ui / dashboard_ui / sim_ui）あり、所有者がその 1 人だと
他の 2 人が私有名（先頭がアンダースコアのモジュール）を越境 import することになる。
供給しているものの置き場所は供給側である。

本モジュールは**ロジックを 1 行も持たない**。同一オブジェクトを再公開するだけであり、
キャッシュも所有者側の 1 つを共有する（写しを作ると片方だけ腐る）。この性質は
``simulator/replay_ui/tests/unit/test_api_loader_owns_the_bridge.py`` が構文と実体の
両方で固定する。

新規の参照は所有者から直接行うこと（``from indigators.indicator_ui import api_loader``）。
本モジュールの削除は承認事項であり、本 Wave では行わない。
"""
from __future__ import annotations

from indigators.indicator_ui.api_loader import (  # noqa: F401
    _CACHE,
    _DEFAULT_REPO_ROOT,
    _ensure_paths,
    load,
    load_catalog_handler,
    load_compute,
    load_dataset,
    load_mp_handlers,
    load_tickvol_handler,
)

__all__ = [
    "load",
    "load_catalog_handler",
    "load_compute",
    "load_dataset",
    "load_mp_handlers",
    "load_tickvol_handler",
]
